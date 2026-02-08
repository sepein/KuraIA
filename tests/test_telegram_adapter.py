import os
import tempfile
import unittest
from unittest.mock import patch

from telegram_adapter import TelegramAdapter


class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class TelegramAdapterTests(unittest.TestCase):
    def _build_adapter(self, extra_env=None) -> TelegramAdapter:
        tmp_dir = tempfile.mkdtemp()
        env = {
            "TELEGRAM_BOT_TOKEN": "dummy-token",
            "TELEGRAM_BINDINGS_FILE": os.path.join(tmp_dir, "bindings.json"),
            "TELEGRAM_OFFSET_FILE": os.path.join(tmp_dir, "offset.txt"),
            "TELEGRAM_FREE_TEXT_FEEDBACK": "false",
        }
        if extra_env:
            env.update(extra_env)
        with patch.dict(os.environ, env, clear=False):
            adapter = TelegramAdapter()
        return adapter

    def test_parse_command_with_mention(self):
        adapter = self._build_adapter()
        command, args = adapter._parse_command("/status@MyBot debate-1")
        self.assertEqual(command, "status")
        self.assertEqual(args, "debate-1")

    def test_parse_non_command(self):
        adapter = self._build_adapter()
        command, args = adapter._parse_command("hola equipo")
        self.assertEqual(command, "")
        self.assertEqual(args, "hola equipo")

    def test_format_round_response_event(self):
        adapter = self._build_adapter()
        event = {
            "event": "round_response",
            "ts": "2026-02-08T10:00:00+00:00",
            "round_num": 2,
            "role": "Arquitecto",
            "response": "Respuesta de prueba",
        }
        msg = adapter._format_event(event)
        self.assertIn("Ronda 2 - Arquitecto", msg)
        self.assertIn("Respuesta de prueba", msg)

    def test_is_user_allowed_restricted(self):
        adapter = self._build_adapter({"TELEGRAM_ALLOWED_USER_IDS": "1,2"})
        self.assertTrue(adapter._is_user_allowed("1"))
        self.assertFalse(adapter._is_user_allowed("9"))

    def test_extract_text_payload(self):
        adapter = self._build_adapter()
        payload = adapter._extract_text_payload(
            {
                "message": {
                    "chat": {"id": 123},
                    "from": {"id": 999},
                    "text": "hola",
                }
            }
        )
        self.assertEqual(payload, ("123", "999", "hola"))

    def test_handle_feedback_without_binding(self):
        adapter = self._build_adapter()
        sent = []
        adapter._send_message = lambda _chat, text: sent.append(text)
        adapter._api_request = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no debe llamar api"))

        adapter._handle_command("10", "1", "/feedback mensaje")
        self.assertTrue(any("No hay debate enlazado" in msg for msg in sent))

    def test_handle_profiles_command(self):
        adapter = self._build_adapter()
        sent = []
        adapter._send_message = lambda _chat, text: sent.append(text)
        adapter._api_request = lambda method, path, **kwargs: _FakeResponse(
            200,
            {
                "count": 1,
                "items": [
                    {"name": "equipo_programacion", "description": "desc", "rules_count": 3}
                ],
            },
        )

        adapter._handle_command("10", "1", "/profiles")
        self.assertTrue(any("equipo_programacion" in msg for msg in sent))

    def test_handle_bind_command(self):
        adapter = self._build_adapter()
        sent = []
        adapter._send_message = lambda _chat, text: sent.append(text)

        def fake_api(method, path, **kwargs):
            self.assertEqual(method, "GET")
            self.assertTrue(path.startswith("/debates/"))
            return _FakeResponse(200, {"debate_id": "d1", "status": "running"})

        adapter._api_request = fake_api
        adapter._handle_command("10", "1", "/bind d1")

        binding = adapter._chat_binding("10")
        self.assertIsNotNone(binding)
        self.assertEqual(binding.get("debate_id"), "d1")
        self.assertTrue(any("Sala enlazada" in msg for msg in sent))


if __name__ == "__main__":
    unittest.main()
