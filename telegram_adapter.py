import json
import os
import time
from typing import Dict, List, Optional, Tuple

import requests

from team_orchestrator_v2 import DEFAULT_ROLES_SEQUENCE


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "y", "on"):
        return True
    if normalized in ("0", "false", "no", "n", "off"):
        return False
    return default


def _split_csv(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


class TelegramAdapter:
    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN es obligatorio")

        self.tg_base_url = f"https://api.telegram.org/bot{token}"
        self.api_base_url = os.getenv("ORCHESTRATOR_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.request_timeout = _env_float("TELEGRAM_REQUEST_TIMEOUT_SECONDS", 15.0)
        self.poll_timeout = _env_int("TELEGRAM_POLL_TIMEOUT_SECONDS", 25)
        self.loop_sleep = _env_float("TELEGRAM_LOOP_SLEEP_SECONDS", 1.0)

        self.bindings_file = os.getenv("TELEGRAM_BINDINGS_FILE", "telegram_bindings.json")
        self.offset_file = os.getenv("TELEGRAM_OFFSET_FILE", "telegram_offset.txt")
        self.events_poll_every_seconds = _env_float("TELEGRAM_EVENTS_POLL_SECONDS", 3.0)
        self.max_preview_chars = _env_int("TELEGRAM_EVENT_PREVIEW_CHARS", 300)
        self.max_events_per_cycle = _env_int("TELEGRAM_MAX_EVENTS_PER_CYCLE", 8)
        self.free_text_feedback = _env_bool("TELEGRAM_FREE_TEXT_FEEDBACK", True)

        self.default_profile = os.getenv("TELEGRAM_DEFAULT_PROFILE", "equipo_programacion").strip()
        sequence_override = _split_csv(os.getenv("TELEGRAM_DEFAULT_SEQUENCE", ""))
        self.default_sequence = sequence_override or list(DEFAULT_ROLES_SEQUENCE)

        roles_override = _split_csv(os.getenv("TELEGRAM_DEFAULT_ROLES", ""))
        self.default_roles = roles_override or list(self.default_sequence)

        allowed_users_raw = _split_csv(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))
        self.allowed_user_ids = {value for value in allowed_users_raw}

        self.session = requests.Session()
        self.offset = self._load_offset()
        self.bindings = self._load_bindings()
        self._last_events_poll_at = 0.0

    def _api_request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.api_base_url}{path}"
        kwargs.setdefault("timeout", self.request_timeout)
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def _tg_request(self, method: str, payload: Dict) -> Dict:
        response = self.session.post(
            f"{self.tg_base_url}/{method}",
            json=payload,
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API error en {method}: {body}")
        return body

    def _load_offset(self) -> int:
        if not os.path.exists(self.offset_file):
            return 0
        try:
            with open(self.offset_file, "r", encoding="utf-8") as file:
                raw = file.read().strip()
            return int(raw) if raw else 0
        except (OSError, ValueError):
            return 0

    def _save_offset(self) -> None:
        with open(self.offset_file, "w", encoding="utf-8") as file:
            file.write(str(self.offset))

    def _load_bindings(self) -> Dict[str, Dict[str, object]]:
        if not os.path.exists(self.bindings_file):
            return {}
        try:
            with open(self.bindings_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        chats = data.get("chats", {})
        if not isinstance(chats, dict):
            return {}
        normalized: Dict[str, Dict[str, object]] = {}
        for chat_id, payload in chats.items():
            if not isinstance(payload, dict):
                continue
            debate_id = str(payload.get("debate_id", "")).strip()
            if not debate_id:
                continue
            normalized[str(chat_id)] = {
                "debate_id": debate_id,
                "last_event_count": int(payload.get("last_event_count", 0)),
            }
        return normalized

    def _save_bindings(self) -> None:
        with open(self.bindings_file, "w", encoding="utf-8") as file:
            json.dump({"chats": self.bindings}, file, indent=2, ensure_ascii=False)

    def _bind_chat(self, chat_id: str, debate_id: str) -> None:
        self.bindings[chat_id] = {"debate_id": debate_id, "last_event_count": 0}
        self._save_bindings()

    def _unbind_chat(self, chat_id: str) -> None:
        if chat_id in self.bindings:
            self.bindings.pop(chat_id, None)
            self._save_bindings()

    def _chat_binding(self, chat_id: str) -> Optional[Dict[str, object]]:
        return self.bindings.get(chat_id)

    def _parse_command(self, text: str) -> Tuple[str, str]:
        clean = text.strip()
        if not clean.startswith("/"):
            return "", clean

        parts = clean.split(maxsplit=1)
        command = parts[0][1:]
        command = command.split("@", maxsplit=1)[0].strip().lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        return command, args

    def _send_message(self, chat_id: str, text: str) -> None:
        self._tg_request(
            "sendMessage",
            {
                "chat_id": int(chat_id),
                "text": text,
                "disable_web_page_preview": True,
            },
        )

    def _is_user_allowed(self, user_id: Optional[str]) -> bool:
        if not self.allowed_user_ids:
            return True
        if not user_id:
            return False
        return user_id in self.allowed_user_ids

    def _format_status(self, body: Dict[str, object]) -> str:
        return (
            f"Debate: {body.get('debate_id')}\n"
            f"Estado: {body.get('status')}\n"
            f"Rondas: {body.get('rounds')}\n"
            f"Coste EUR: {body.get('cost_eur')}\n"
            f"Reason: {body.get('reason')}"
        )

    def _format_event(self, event: Dict[str, object]) -> str:
        event_type = str(event.get("event", ""))
        ts = str(event.get("ts", ""))

        if event_type == "debate_started":
            task = str(event.get("task", ""))
            return f"[{ts}] Debate iniciado\nTarea: {task}"

        if event_type == "round_response":
            role = str(event.get("role", ""))
            round_num = event.get("round_num", "?")
            response = str(event.get("response", ""))
            preview = response[: self.max_preview_chars]
            suffix = "..." if len(response) > len(preview) else ""
            return f"[{ts}] Ronda {round_num} - {role}\n{preview}{suffix}"

        if event_type == "parallel_completed":
            results = event.get("results", {})
            count = len(results) if isinstance(results, dict) else 0
            return f"[{ts}] Bloque paralelo completado ({count} respuestas)."

        if event_type == "debate_finished":
            return (
                f"[{ts}] Debate finalizado\n"
                f"Estado: {event.get('status')}\n"
                f"Coste EUR: {event.get('cost_eur')}\n"
                f"Duracion: {event.get('duration_seconds')} s"
            )

        if event_type == "round_error":
            return f"[{ts}] Error de ronda: {event.get('error')}"

        return ""

    def _handle_command(self, chat_id: str, user_id: Optional[str], text: str) -> None:
        if not self._is_user_allowed(user_id):
            self._send_message(chat_id, "No autorizado para controlar debates.")
            return

        command, args = self._parse_command(text)
        binding = self._chat_binding(chat_id)
        bound_debate_id = str(binding.get("debate_id")) if binding else ""

        if command in ("help", "start"):
            self._send_message(
                chat_id,
                "Comandos:\n"
                "/startdebate <tarea>\n"
                "/bind <debate_id>\n"
                "/unbind\n"
                "/status [debate_id]\n"
                "/debates\n"
                "/profiles\n"
                "/feedback <mensaje>\n"
                "/stop\n"
                "Texto libre (si hay binding) => feedback automatico",
            )
            return

        if command == "profiles":
            response = self._api_request("GET", "/discussion-profiles")
            body = response.json()
            items = body.get("items", [])
            if not items:
                self._send_message(chat_id, "No hay profiles disponibles.")
                return
            lines = ["Profiles disponibles:"]
            for item in items:
                lines.append(f"- {item.get('name')}: {item.get('description')} (rules={item.get('rules_count')})")
            self._send_message(chat_id, "\n".join(lines))
            return

        if command == "debates":
            response = self._api_request("GET", "/debates", params={"limit": 10})
            body = response.json()
            items = body.get("items", [])
            if not items:
                self._send_message(chat_id, "No hay debates en runtime.")
                return
            lines = ["Debates recientes:"]
            for item in items:
                lines.append(f"- {item.get('debate_id')} | {item.get('status')}")
            self._send_message(chat_id, "\n".join(lines))
            return

        if command == "startdebate":
            if not args:
                self._send_message(chat_id, "Uso: /startdebate <tarea>")
                return
            payload = {
                "task": args,
                "discussion_profile": self.default_profile or None,
                "roles": [{"name": role_name} for role_name in self.default_roles],
                "sequence": list(self.default_sequence),
                "parallel_groups": [],
                "bootstrap": True,
                "check_queued_interventions": True,
            }
            response = self._api_request("POST", "/debates", json=payload)
            body = response.json()
            debate_id = str(body.get("debate_id"))
            self._bind_chat(chat_id, debate_id)
            self._send_message(chat_id, f"Debate creado y enlazado: {debate_id}")
            return

        if command == "bind":
            if not args:
                self._send_message(chat_id, "Uso: /bind <debate_id>")
                return
            debate_id = args.strip()
            response = self._api_request("GET", f"/debates/{debate_id}")
            if response.status_code == 200:
                self._bind_chat(chat_id, debate_id)
                self._send_message(chat_id, f"Sala enlazada a {debate_id}")
                return

        if command == "unbind":
            self._unbind_chat(chat_id)
            self._send_message(chat_id, "Binding eliminado para esta sala.")
            return

        if command == "status":
            target = args.strip() or bound_debate_id
            if not target:
                self._send_message(chat_id, "No hay debate enlazado. Usa /bind o /startdebate.")
                return
            response = self._api_request("GET", f"/debates/{target}")
            self._send_message(chat_id, self._format_status(response.json()))
            return

        if command == "feedback":
            if not bound_debate_id:
                self._send_message(chat_id, "No hay debate enlazado. Usa /bind o /startdebate.")
                return
            if not args:
                self._send_message(chat_id, "Uso: /feedback <mensaje>")
                return
            self._api_request(
                "POST",
                f"/debates/{bound_debate_id}/interventions",
                json={"action": "feedback", "message": args},
            )
            self._send_message(chat_id, "Feedback encolado.")
            return

        if command == "stop":
            if not bound_debate_id:
                self._send_message(chat_id, "No hay debate enlazado. Usa /bind o /startdebate.")
                return
            self._api_request(
                "POST",
                f"/debates/{bound_debate_id}/interventions",
                json={"action": "stop", "message": "Stop desde Telegram"},
            )
            self._send_message(chat_id, "Solicitud de parada encolada.")
            return

        if command:
            self._send_message(chat_id, f"Comando no reconocido: /{command}. Usa /help")
            return

        # Texto libre como feedback opcional.
        if self.free_text_feedback and bound_debate_id and text.strip():
            self._api_request(
                "POST",
                f"/debates/{bound_debate_id}/interventions",
                json={"action": "feedback", "message": text.strip()},
            )
            self._send_message(chat_id, "Feedback encolado (texto libre).")

    def _extract_text_payload(self, update: Dict) -> Optional[Tuple[str, Optional[str], str]]:
        message = update.get("message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat", {})
        from_user = message.get("from", {})

        chat_id = chat.get("id")
        text = message.get("text")
        user_id = from_user.get("id")

        if chat_id is None or not isinstance(text, str):
            return None

        return str(chat_id), str(user_id) if user_id is not None else None, text

    def process_updates(self) -> None:
        body = self._tg_request(
            "getUpdates",
            {
                "offset": self.offset,
                "timeout": self.poll_timeout,
                "allowed_updates": ["message"],
            },
        )
        updates = body.get("result", [])

        for update in updates:
            update_id = int(update.get("update_id", 0))
            self.offset = max(self.offset, update_id + 1)

            payload = self._extract_text_payload(update)
            if payload is None:
                continue

            chat_id, user_id, text = payload
            try:
                self._handle_command(chat_id, user_id, text)
            except Exception as exc:
                self._send_message(chat_id, f"Error procesando comando: {exc}")

        self._save_offset()

    def poll_bound_events(self) -> None:
        now = time.monotonic()
        if now - self._last_events_poll_at < self.events_poll_every_seconds:
            return
        self._last_events_poll_at = now

        bindings_changed = False
        for chat_id, binding in list(self.bindings.items()):
            debate_id = str(binding.get("debate_id", ""))
            if not debate_id:
                continue

            try:
                response = self._api_request(
                    "GET",
                    f"/debates/{debate_id}/events",
                    params={"limit": 5000, "reverse": False},
                )
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response else None
                if status == 404:
                    self._send_message(chat_id, f"Debate no encontrado y desenlazado: {debate_id}")
                    self._unbind_chat(chat_id)
                    bindings_changed = True
                continue
            except Exception:
                continue

            body = response.json()
            events = body.get("events", [])
            if not isinstance(events, list):
                continue

            last_event_count = int(binding.get("last_event_count", 0))
            if last_event_count < 0:
                last_event_count = 0
            if len(events) <= last_event_count:
                continue

            new_events = events[last_event_count:]
            sent = 0
            for event in new_events:
                message = self._format_event(event)
                if not message:
                    continue
                self._send_message(chat_id, message)
                sent += 1
                if sent >= self.max_events_per_cycle:
                    remaining = len(new_events) - sent
                    if remaining > 0:
                        self._send_message(chat_id, f"... {remaining} eventos adicionales omitidos en este ciclo")
                    break

            binding["last_event_count"] = len(events)
            bindings_changed = True

        if bindings_changed:
            self._save_bindings()

    def run_forever(self) -> None:
        print("[telegram-adapter] iniciado")
        print(f"[telegram-adapter] API base: {self.api_base_url}")
        while True:
            try:
                self.process_updates()
                self.poll_bound_events()
            except KeyboardInterrupt:
                print("\n[telegram-adapter] detenido por usuario")
                break
            except Exception as exc:
                print(f"[telegram-adapter] error: {exc}")
                time.sleep(2)

            time.sleep(self.loop_sleep)


def main() -> None:
    adapter = TelegramAdapter()
    adapter.run_forever()


if __name__ == "__main__":
    main()
