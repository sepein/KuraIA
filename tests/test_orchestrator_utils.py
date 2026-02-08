import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from team_orchestrator_v2 import AppConfig, OpenCodeTeam, _env_bool, _env_float, _env_int


class EnvHelpersTests(unittest.TestCase):
    def test_env_int_and_float_with_invalid_values(self):
        with patch.dict(os.environ, {"X_INT": "abc", "X_FLOAT": "xyz"}, clear=False):
            self.assertEqual(_env_int("X_INT", 7), 7)
            self.assertEqual(_env_float("X_FLOAT", 1.5), 1.5)

    def test_env_bool_variants(self):
        with patch.dict(os.environ, {"X_BOOL": "yes"}, clear=False):
            self.assertTrue(_env_bool("X_BOOL", False))
        with patch.dict(os.environ, {"X_BOOL": "OFF"}, clear=False):
            self.assertFalse(_env_bool("X_BOOL", True))
        with patch.dict(os.environ, {"X_BOOL": "unknown"}, clear=False):
            self.assertTrue(_env_bool("X_BOOL", True))


class AppConfigTests(unittest.TestCase):
    def test_app_config_reads_environment_on_instantiation(self):
        env_values = {
            "OPENCODE_BASE_URL": "http://localhost:9999/",
            "MAX_BUDGET_EUR": "1.25",
            "ENABLE_EVENT_LOGGING": "false",
            "MAX_LOG_TEXT_CHARS": "123",
        }
        with patch.dict(os.environ, env_values, clear=False):
            cfg = AppConfig()

        self.assertEqual(cfg.base_url, "http://localhost:9999")
        self.assertAlmostEqual(cfg.max_budget_eur, 1.25)
        self.assertFalse(cfg.enable_event_logging)
        self.assertEqual(cfg.max_log_text_chars, 123)


class OpenCodeTeamUtilityTests(unittest.TestCase):
    def test_normalize_roles_removes_duplicates_and_empty(self):
        normalized = OpenCodeTeam.normalize_roles(["A", "B", "A", "", None, "B", "C"])
        self.assertEqual(normalized, ["A", "B", "C"])

    def test_clip_for_log_truncates(self):
        cfg = AppConfig(max_log_text_chars=5, enable_event_logging=False)
        team = OpenCodeTeam(config=cfg)
        self.assertEqual(team._clip_for_log("abc"), "abc")
        self.assertEqual(team._clip_for_log("abcdef"), "abcde... [truncated 1 chars]")

    def test_estimate_cost_uses_input_and_output(self):
        cfg = AppConfig(enable_event_logging=False)
        team = OpenCodeTeam(config=cfg)
        team.total_input_chars = 400
        team.total_output_chars = 800

        cost_usd, cost_eur = team.estimate_cost()
        expected_usd = (100 * cfg.groq_cost_per_input_token_usd) + (200 * cfg.groq_cost_per_output_token_usd)
        expected_eur = expected_usd * cfg.eur_per_usd

        self.assertAlmostEqual(cost_usd, expected_usd)
        self.assertAlmostEqual(cost_eur, expected_eur)

    def test_log_event_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = os.path.join(tmp_dir, "events.jsonl")
            sessions_path = os.path.join(tmp_dir, "sessions.json")
            cfg = AppConfig(
                sessions_file=sessions_path,
                debate_log_file=log_path,
                enable_event_logging=True,
                max_log_text_chars=500,
            )
            team = OpenCodeTeam(config=cfg)
            team._log_event("round_started", debate_id="d1", round_num=0, role="Arquitecto")

            with open(log_path, "r", encoding="utf-8") as file:
                rows = [json.loads(line) for line in file if line.strip()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "round_started")
        self.assertEqual(rows[0]["debate_id"], "d1")
        self.assertEqual(rows[0]["round_num"], 0)
        self.assertEqual(rows[0]["role"], "Arquitecto")
        self.assertIn("ts", rows[0])

    def test_queue_and_pull_interventions(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            queue_path = os.path.join(tmp_dir, "interventions.jsonl")
            cfg = AppConfig(
                sessions_file=os.path.join(tmp_dir, "sessions.json"),
                interventions_file=queue_path,
                enable_event_logging=False,
            )
            team = OpenCodeTeam(config=cfg)

            team.queue_intervention("Feedback A", debate_id="d1", action="feedback")
            team.queue_intervention("Parar", debate_id="d1", action="stop")
            team.queue_intervention("Feedback otro debate", debate_id="d2", action="feedback")

            pulled = team._pull_queued_interventions("d1")
            self.assertEqual(len(pulled), 2)
            self.assertEqual(pulled[0]["action"], "feedback")
            self.assertEqual(pulled[1]["action"], "stop")

            # Debe quedar en cola solo el evento de d2.
            remaining = team._pull_queued_interventions("d2")
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]["message"], "Feedback otro debate")

    def test_pull_queued_interventions_skips_invalid_lines(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            queue_path = os.path.join(tmp_dir, "interventions.jsonl")
            with open(queue_path, "w", encoding="utf-8") as file:
                file.write("not-json\n")
                file.write("{\"debate_id\":\"d1\",\"action\":\"feedback\",\"message\":\"ok\"}\n")

            cfg = AppConfig(
                sessions_file=os.path.join(tmp_dir, "sessions.json"),
                interventions_file=queue_path,
                enable_event_logging=False,
            )
            team = OpenCodeTeam(config=cfg)
            pulled = team._pull_queued_interventions("d1")
            self.assertEqual(len(pulled), 1)
            self.assertEqual(pulled[0]["message"], "ok")

    def test_load_role_prompts_renders_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            roles_path = Path(tmp_dir) / "roles.yaml"
            roles_path.write_text("ignored by fake yaml", encoding="utf-8")

            fake_yaml = types.SimpleNamespace(
                safe_load=lambda _raw: {
                    "default_model": "provider/default-model",
                    "default_response_format": "FORMATO_FIJO",
                    "profiles": {
                        "equipo_programacion": {
                            "global_instructions": "Contexto global",
                            "rules": ["Regla 1", "Regla 2"],
                        }
                    },
                    "roles": {
                        "Arquitecto": {
                            "model": "provider/architect-model",
                            "prompt": "Prompt base {default_response_format}",
                        },
                        "Backend_Dev": {
                            "prompt": "Prompt backend",
                        },
                    },
                }
            )

            with patch.dict("sys.modules", {"yaml": fake_yaml}):
                cfg = AppConfig(
                    sessions_file=os.path.join(tmp_dir, "sessions.json"),
                    role_prompts_file=str(roles_path),
                    enable_event_logging=False,
                )
                team = OpenCodeTeam(config=cfg)

            self.assertIn("Arquitecto", team.role_prompts)
            self.assertEqual(team.role_prompts["Arquitecto"], "Prompt base FORMATO_FIJO")
            self.assertEqual(team.role_prompts["Backend_Dev"], "Prompt backend")
            self.assertEqual(team.model_default, "provider/default-model")
            self.assertEqual(team.role_models["Arquitecto"], "provider/architect-model")
            self.assertEqual(team.models["Arquitecto"], "provider/architect-model")
            self.assertIn("equipo_programacion", team.discussion_profiles)

    def test_resolve_system_prompt_prefers_custom_then_role_then_default(self):
        cfg = AppConfig(enable_event_logging=False, role_prompts_file="")
        team = OpenCodeTeam(config=cfg)
        team.role_prompts = {"Arquitecto": "PROMPT_ROL"}

        self.assertEqual(team._resolve_system_prompt("Arquitecto", "CUSTOM"), "CUSTOM")
        self.assertEqual(team._resolve_system_prompt("Arquitecto", None), "PROMPT_ROL")
        fallback = team._resolve_system_prompt("Otro_Rol", None)
        self.assertIn("Eres Otro_Rol", fallback)


if __name__ == "__main__":
    unittest.main()
