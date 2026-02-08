import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import api_server
from api_server import _clean_rules, _compose_participant_prompt, _resolve_profile, app
from debate_memory import SQLiteDebateMemoryStore
from team_orchestrator_v2 import AppConfig, OpenCodeTeam


class ApiServerUtilsTests(unittest.TestCase):
    def test_clean_rules_deduplicates_and_strips(self):
        rules = _clean_rules([" Regla 1 ", "", "Regla 2", "Regla 1"])
        self.assertEqual(rules, ["Regla 1", "Regla 2"])

    def test_compose_participant_prompt_includes_global_context(self):
        prompt = _compose_participant_prompt(
            role_name="Arquitecto",
            base_prompt="PROMPT_ROL",
            profile_name="equipo_programacion",
            profile={
                "global_instructions": "INSTRUCCIONES_PERFIL",
                "rules": ["Regla perfil"],
            },
            global_instructions="INSTRUCCIONES_REQUEST",
            global_rules=["Regla request"],
        )

        self.assertIn("CONTEXTO GLOBAL DE LA MESA", prompt)
        self.assertIn("equipo_programacion", prompt)
        self.assertIn("INSTRUCCIONES_PERFIL", prompt)
        self.assertIn("INSTRUCCIONES_REQUEST", prompt)
        self.assertIn("Regla perfil", prompt)
        self.assertIn("Regla request", prompt)
        self.assertIn("INSTRUCCIONES ESPECIFICAS DEL ROL", prompt)
        self.assertIn("PROMPT_ROL", prompt)

    def test_compose_participant_prompt_without_globals(self):
        prompt = _compose_participant_prompt(
            role_name="Arquitecto",
            base_prompt="PROMPT_ROL",
            profile_name=None,
            profile={},
            global_instructions=None,
            global_rules=[],
        )
        self.assertNotIn("CONTEXTO GLOBAL DE LA MESA", prompt)
        self.assertIn("INSTRUCCIONES ESPECIFICAS DEL ROL", prompt)

    def test_resolve_profile_raises_when_missing(self):
        team = OpenCodeTeam(config=AppConfig(enable_event_logging=False))
        with self.assertRaises(HTTPException) as ctx:
            _resolve_profile(team, "perfil_inexistente")
        self.assertEqual(ctx.exception.status_code, 400)


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        api_server._memory_store = SQLiteDebateMemoryStore(str(Path(self._tmp.name) / "memory.db"))
        self.client = TestClient(app)
        with api_server._runtime_lock:
            api_server._runtime_debates.clear()

    def tearDown(self):
        self.client.close()
        self._tmp.cleanup()

    def test_create_debate_requires_roles(self):
        response = self.client.post("/debates", json={"task": "Debate sin roles", "roles": []})
        self.assertEqual(response.status_code, 422)

    def test_create_debate_rejects_unknown_sequence_role(self):
        payload = {
            "task": "Debate",
            "roles": [{"name": "A"}],
            "sequence": ["B"],
            "parallel_groups": [],
        }
        response = self.client.post("/debates", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("roles no definidos", response.json().get("detail", ""))

    def test_create_debate_rejects_duplicate_roles(self):
        payload = {
            "task": "Debate",
            "roles": [{"name": "A"}, {"name": "A"}],
            "sequence": ["A"],
            "parallel_groups": [],
        }
        response = self.client.post("/debates", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("nombres duplicados", response.json().get("detail", ""))

    def test_create_debate_rejects_missing_profile(self):
        payload = {
            "task": "Debate",
            "discussion_profile": "perfil_que_no_existe",
            "roles": [{"name": "Arquitecto"}],
            "sequence": ["Arquitecto"],
            "parallel_groups": [],
        }
        response = self.client.post("/debates", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("discussion_profile no encontrado", response.json().get("detail", ""))

    def test_list_discussion_profiles(self):
        response = self.client.get("/discussion-profiles")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("count", body)
        self.assertIn("items", body)
        self.assertGreaterEqual(body["count"], 1)

    @patch("api_server._run_debate_worker", return_value=None)
    def test_create_debate_success_queues_runtime(self, _mock_worker):
        payload = {
            "task": "Debate API",
            "discussion_profile": "equipo_programacion",
            "roles": [{"name": "Arquitecto"}],
            "sequence": ["Arquitecto"],
            "parallel_groups": [],
        }
        response = self.client.post("/debates", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertTrue(body["debate_id"].startswith("debate-"))

        with api_server._runtime_lock:
            runtime = api_server._runtime_debates.get(body["debate_id"])
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.status, "queued")

    @patch("api_server._run_debate_worker", return_value=None)
    def test_create_debate_persists_memory(self, _mock_worker):
        payload = {
            "task": "Debate con memoria",
            "roles": [{"name": "Arquitecto"}],
            "sequence": ["Arquitecto"],
            "parallel_groups": [],
        }
        created = self.client.post("/debates", json=payload).json()
        debate_id = created["debate_id"]

        memory = self.client.get(f"/debates/{debate_id}/memory")
        self.assertEqual(memory.status_code, 200)
        memory_payload = memory.json().get("memory", {})
        self.assertEqual(memory_payload.get("debate_id"), debate_id)
        self.assertEqual(memory_payload.get("status"), "queued")
        self.assertEqual(memory_payload.get("task"), "Debate con memoria")

    @patch("api_server._run_debate_worker", return_value=None)
    def test_get_debate_returns_runtime_when_no_events(self, _mock_worker):
        payload = {
            "task": "Debate API",
            "roles": [{"name": "Arquitecto"}],
            "sequence": ["Arquitecto"],
            "parallel_groups": [],
        }
        created = self.client.post("/debates", json=payload).json()
        debate_id = created["debate_id"]

        response = self.client.get(f"/debates/{debate_id}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["debate_id"], debate_id)
        self.assertEqual(body["status"], "queued")

    @patch("api_server._run_debate_worker", return_value=None)
    def test_list_debates_includes_created_item(self, _mock_worker):
        payload = {
            "task": "Debate API",
            "roles": [{"name": "Arquitecto"}],
            "sequence": ["Arquitecto"],
            "parallel_groups": [],
        }
        created = self.client.post("/debates", json=payload).json()
        debate_id = created["debate_id"]

        response = self.client.get("/debates")
        self.assertEqual(response.status_code, 200)
        items = response.json().get("items", [])
        self.assertTrue(any(item.get("debate_id") == debate_id for item in items))

    def test_import_snapshot_then_read_debate_and_events(self):
        snapshot = {
            "schema_version": "1.0",
            "debate": {
                "debate_id": "debate-import-1",
                "status": "completed",
                "reason": "",
                "created_at": "2026-01-01T00:00:00+00:00",
                "started_at": "2026-01-01T00:00:01+00:00",
                "finished_at": "2026-01-01T00:00:20+00:00",
                "rounds": 1,
                "cost_eur": 0.01,
                "error": "",
                "task": "Debate importado",
                "discussion_profile": "equipo_programacion",
                "global_instructions": "",
                "global_rules": [],
                "roles": [{"name": "Arquitecto"}],
                "sequence": ["Arquitecto"],
                "parallel_groups": [],
                "final_minutes": "Acta final importada",
                "summary": {
                    "status": "completed",
                    "reason": "",
                    "started_at": "2026-01-01T00:00:01+00:00",
                    "finished_at": "2026-01-01T00:00:20+00:00",
                    "rounds": 1,
                    "cost_eur": 0.01,
                },
            },
            "events": [
                {
                    "ts": "2026-01-01T00:00:01+00:00",
                    "event": "debate_started",
                    "debate_id": "debate-import-1",
                    "task": "Debate importado",
                },
                {
                    "ts": "2026-01-01T00:00:10+00:00",
                    "event": "round_response",
                    "debate_id": "debate-import-1",
                    "round_num": 0,
                    "role": "Arquitecto",
                    "response": "Respuesta importada",
                },
                {
                    "ts": "2026-01-01T00:00:20+00:00",
                    "event": "debate_finished",
                    "debate_id": "debate-import-1",
                    "status": "completed",
                    "reason": "",
                    "cost_eur": 0.01,
                },
            ],
        }

        imported = self.client.post("/memory/import", json={"snapshot": snapshot, "overwrite": True})
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json().get("status"), "imported")

        debate = self.client.get("/debates/debate-import-1")
        self.assertEqual(debate.status_code, 200)
        self.assertEqual(debate.json().get("status"), "completed")
        self.assertEqual(debate.json().get("rounds"), 1)

        events = self.client.get("/debates/debate-import-1/events")
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json().get("count"), 3)

        export_single = self.client.get("/debates/debate-import-1/export")
        self.assertEqual(export_single.status_code, 200)
        self.assertIn("debate", export_single.json())
        self.assertEqual(len(export_single.json().get("events", [])), 3)

        export_all = self.client.get("/memory/export?limit=10&include_events=true")
        self.assertEqual(export_all.status_code, 200)
        self.assertGreaterEqual(export_all.json().get("count", 0), 1)

    def test_export_debate_memory_not_found(self):
        response = self.client.get("/debates/debate-inexistente/export")
        self.assertEqual(response.status_code, 404)

    def test_get_debate_not_found(self):
        response = self.client.get("/debates/debate-inexistente")
        self.assertEqual(response.status_code, 404)

    def test_get_debate_events_not_found(self):
        response = self.client.get("/debates/debate-inexistente/events")
        self.assertEqual(response.status_code, 404)

    def test_intervention_feedback_requires_message(self):
        response = self.client.post(
            "/debates/debate-x/interventions",
            json={"action": "feedback", "message": ""},
        )
        self.assertEqual(response.status_code, 400)

    @patch("api_server._load_events_for_debate", return_value=[])
    def test_intervention_not_found(self, _mock_events):
        response = self.client.post(
            "/debates/debate-x/interventions",
            json={"action": "stop", "message": "stop"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

