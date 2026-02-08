import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import api_server
from api_server import (
    DebateCreateRequest,
    _build_minutes_context,
    _build_output_events_for_debate,
    _clean_rules,
    _compose_participant_prompt,
    _parse_task_command,
    _resolve_final_minutes,
    _resolve_profile,
    app,
)
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

    def test_build_minutes_context_contains_relevant_sections(self):
        events = [
            {"event": "round_response", "role": "Arquitecto", "response": "Propuesta principal"},
            {"event": "chief_action", "action": "feedback", "feedback": "Enfocar en MVP"},
        ]
        summary = {"status": "completed", "reason": "", "rounds": 1, "cost_eur": 0.01}
        context = _build_minutes_context("Tarea X", summary, events)

        self.assertIn("INTERVENCIONES_RELEVANTES", context)
        self.assertIn("Arquitecto", context)
        self.assertIn("INTERVENCIONES_CONDUCTOR", context)
        self.assertIn("Enfocar en MVP", context)

    def test_resolve_final_minutes_programmatic_mode(self):
        request = DebateCreateRequest(task="Tarea X", roles=[{"name": "Arquitecto"}], minutes_mode="programmatic")
        summary = {"status": "completed", "reason": "", "rounds": 0, "cost_eur": 0.0}
        events = []
        team = OpenCodeTeam(config=AppConfig(enable_event_logging=False))

        minutes, source = _resolve_final_minutes(team, request, summary, events)
        self.assertEqual(source, "programmatic")
        self.assertIn("ACTA FINAL DE LA MESA", minutes)

    def test_resolve_final_minutes_agent_mode_fallback(self):
        class _FailingTeam:
            role_prompts = {}

            def create_agent(self, *args, **kwargs):
                raise RuntimeError("forced-error")

        request = DebateCreateRequest(task="Tarea X", roles=[{"name": "Arquitecto"}], minutes_mode="agent")
        summary = {"status": "completed", "reason": "", "rounds": 0, "cost_eur": 0.0}
        events = []

        minutes, source = _resolve_final_minutes(_FailingTeam(), request, summary, events)
        self.assertEqual(source, "programmatic_fallback")
        self.assertIn("FALLBACK PROGRAMATICO", minutes)

    def test_parse_task_command_key_value(self):
        parsed = _parse_task_command('#tarea crear title="Implementar login" owner=Backend_Dev priority=alta')
        self.assertIsNotNone(parsed)
        action, payload = parsed
        self.assertEqual(action, "create")
        self.assertEqual(payload.get("title"), "Implementar login")
        self.assertEqual(payload.get("owner"), "Backend_Dev")

    def test_parse_task_command_with_json_payload(self):
        parsed = _parse_task_command('#tarea modificar {"id":"TASK-42","state":"in_progress"}')
        self.assertIsNotNone(parsed)
        action, payload = parsed
        self.assertEqual(action, "update")
        self.assertEqual(payload.get("id"), "TASK-42")

    def test_build_output_events_for_debate_filters_and_maps(self):
        events = [
            {
                "event": "round_response",
                "ts": "2026-01-01T00:00:10+00:00",
                "role": "Moderador",
                "response": '#tarea crear title="Preparar backlog" owner=Backend_Dev',
            },
            {
                "event": "round_response",
                "ts": "2026-01-01T00:00:12+00:00",
                "role": "Arquitecto",
                "response": '#tarea crear title="No debe emitirse por filtro de rol"',
            },
        ]
        output_events = _build_output_events_for_debate(
            debate_id="debate-x",
            events=events,
            final_minutes="",
        )
        self.assertEqual(len(output_events), 1)
        item = output_events[0]
        self.assertEqual(item.get("entity"), "task")
        self.assertEqual(item.get("action"), "create")
        self.assertEqual(item.get("source_role"), "Moderador")


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

    def test_swagger_and_docs_redirect(self):
        redirect = self.client.get("/docs", follow_redirects=False)
        self.assertIn(redirect.status_code, (301, 302, 307, 308))
        self.assertEqual(redirect.headers.get("location"), "/swagger")

        swagger = self.client.get("/swagger")
        self.assertEqual(swagger.status_code, 200)

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
            "minutes_mode": "agent",
        }
        created = self.client.post("/debates", json=payload).json()
        debate_id = created["debate_id"]

        memory = self.client.get(f"/debates/{debate_id}/memory")
        self.assertEqual(memory.status_code, 200)
        memory_payload = memory.json().get("memory", {})
        self.assertEqual(memory_payload.get("debate_id"), debate_id)
        self.assertEqual(memory_payload.get("status"), "queued")
        self.assertEqual(memory_payload.get("task"), "Debate con memoria")
        self.assertEqual(memory_payload.get("minutes_mode"), "agent")
        self.assertEqual(memory_payload.get("final_minutes_source"), "pending")

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
                    "role": "Moderador",
                    "response": '#tarea crear title="Configurar CI" owner=DevOps_Dev',
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

        output_events = self.client.get("/debates/debate-import-1/output-events")
        self.assertEqual(output_events.status_code, 200)
        self.assertGreaterEqual(output_events.json().get("count", 0), 1)
        first = (output_events.json().get("events") or [{}])[0]
        self.assertEqual(first.get("entity"), "task")
        self.assertEqual(first.get("action"), "create")
        self.assertEqual(first.get("payload", {}).get("title"), "Configurar CI")

        export_single = self.client.get("/debates/debate-import-1/export")
        self.assertEqual(export_single.status_code, 200)
        self.assertIn("debate", export_single.json())
        self.assertEqual(len(export_single.json().get("events", [])), 3)
        self.assertGreaterEqual(len(export_single.json().get("output_events", [])), 1)

        export_all = self.client.get("/memory/export?limit=10&include_events=true&include_output_events=true")
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

    def test_get_output_events_not_found(self):
        response = self.client.get("/debates/debate-inexistente/output-events")
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

