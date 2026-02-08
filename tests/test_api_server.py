import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import api_server
from api_server import _clean_rules, _compose_participant_prompt, _resolve_profile, app
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


class _DummyThread:
    started_count = 0

    def __init__(self, target=None, args=None, daemon=None):
        self.target = target
        self.args = args or ()
        self.daemon = daemon

    def start(self):
        _DummyThread.started_count += 1


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        with api_server._runtime_lock:
            api_server._runtime_debates.clear()
        _DummyThread.started_count = 0

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

    @patch("api_server.threading.Thread", _DummyThread)
    def test_create_debate_success_queues_runtime(self):
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
        self.assertEqual(_DummyThread.started_count, 1)

        with api_server._runtime_lock:
            runtime = api_server._runtime_debates.get(body["debate_id"])
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.status, "queued")

    @patch("api_server.threading.Thread", _DummyThread)
    def test_get_debate_returns_runtime_when_no_events(self):
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

    @patch("api_server.threading.Thread", _DummyThread)
    def test_list_debates_includes_created_item(self):
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
