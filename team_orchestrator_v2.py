import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests


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


@dataclass(frozen=True)
class AppConfig:
    base_url: str = field(default_factory=lambda: os.getenv("OPENCODE_BASE_URL", "http://localhost:4096").rstrip("/"))
    sessions_file: str = field(default_factory=lambda: os.getenv("OPENCODE_SESSIONS_FILE", "team_sessions.json"))
    max_wait_seconds: int = field(default_factory=lambda: _env_int("MAX_WAIT_SECONDS", 60))
    poll_interval_seconds: float = field(default_factory=lambda: _env_float("POLL_INTERVAL_SECONDS", 1.5))
    max_rounds_per_debate: int = field(default_factory=lambda: _env_int("MAX_ROUNDS_PER_DEBATE", 15))
    max_budget_eur: float = field(default_factory=lambda: _env_float("MAX_BUDGET_EUR", 0.50))
    max_context_chars: int = field(default_factory=lambda: _env_int("MAX_CONTEXT_CHARS", 12000))
    request_timeout_seconds: float = field(default_factory=lambda: _env_float("REQUEST_TIMEOUT_SECONDS", 20.0))
    eur_per_usd: float = field(default_factory=lambda: _env_float("EUR_PER_USD", 0.92))
    debate_log_file: str = field(default_factory=lambda: os.getenv("DEBATE_LOG_FILE", "debate_events.jsonl"))
    enable_event_logging: bool = field(default_factory=lambda: _env_bool("ENABLE_EVENT_LOGGING", True))
    max_log_text_chars: int = field(default_factory=lambda: _env_int("MAX_LOG_TEXT_CHARS", 4000))
    interventions_file: str = field(default_factory=lambda: os.getenv("INTERVENTIONS_FILE", "interventions_queue.jsonl"))
    role_prompts_file: str = field(default_factory=lambda: os.getenv("ROLE_PROMPTS_FILE", "roles.yaml"))

    # Groq llama-3.1-70b-versatile prices per token.
    groq_cost_per_input_token_usd: float = 0.59 / 1_000_000
    groq_cost_per_output_token_usd: float = 0.79 / 1_000_000


class OpenCodeTeam:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self.sessions: Dict[str, str] = self.load_sessions()
        self.total_input_chars = 0
        self.total_output_chars = 0
        self._metrics_lock = threading.Lock()
        self._debate_seq = 0
        self._log_warning_printed = False

        self.model_default = "groq/llama-3.1-70b-versatile"
        self.models = {
            "Arquitecto": "groq/llama-3.1-70b-versatile",
            "Critico_Dev": "groq/llama-3.1-70b-versatile",
            # Puedes ajustar modelo por rol: "Backend_Dev": "groq/llama-3.1-8b-instant"
        }
        self.role_prompts, self.role_models, self.discussion_profiles = self._load_role_definitions()
        if self.role_models:
            self.models.update(self.role_models)

    @staticmethod
    def _default_system_prompt(role: str) -> str:
        return (
            f"Eres {role} en un equipo pequeno de desarrollo software. "
            "Responde solo en tu rol, se tecnico, conciso y argumenta tus decisiones. "
            "Usa espanol si el mensaje esta en espanol. No salgas de tu rol."
        )

    def _resolve_system_prompt(self, role: str, custom_prompt: Optional[str]) -> str:
        if custom_prompt:
            return custom_prompt
        role_prompt = self.role_prompts.get(role)
        if role_prompt:
            return role_prompt
        return self._default_system_prompt(role)

    def _load_role_definitions(self) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, Dict[str, object]]]:
        path = self.config.role_prompts_file
        if not path:
            return {}, {}, {}
        if not os.path.exists(path):
            return {}, {}, {}

        try:
            import yaml  # type: ignore
        except ModuleNotFoundError:
            print("[!] ROLE_PROMPTS_FILE detectado pero PyYAML no instalado. Usa: pip install pyyaml")
            return {}, {}, {}

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file.read())
        except OSError as exc:
            print(f"[!] No se pudo leer {path}: {exc}")
            return {}, {}, {}
        except Exception as exc:
            print(f"[!] No se pudo parsear YAML de {path}: {exc}")
            return {}, {}, {}

        if not isinstance(data, dict):
            print(f"[!] {path} no tiene estructura valida (dict raiz).")
            return {}, {}, {}

        yaml_default_model = data.get("default_model")
        if isinstance(yaml_default_model, str) and yaml_default_model.strip():
            self.model_default = yaml_default_model.strip()

        default_response_format = str(data.get("default_response_format", "") or "").strip()
        roles_block = data.get("roles", {})
        if not isinstance(roles_block, dict):
            print(f"[!] {path} no contiene bloque 'roles' valido.")
            return {}, {}, {}

        profiles: Dict[str, Dict[str, object]] = {}
        profiles_block = data.get("profiles", {})
        if isinstance(profiles_block, dict):
            for profile_name, profile_cfg in profiles_block.items():
                if not isinstance(profile_cfg, dict):
                    continue
                profile: Dict[str, object] = {}
                global_instructions = profile_cfg.get("global_instructions")
                if isinstance(global_instructions, str) and global_instructions.strip():
                    profile["global_instructions"] = global_instructions.strip()
                rules = profile_cfg.get("rules")
                if isinstance(rules, list):
                    clean_rules = [str(rule).strip() for rule in rules if str(rule).strip()]
                    if clean_rules:
                        profile["rules"] = clean_rules
                description = profile_cfg.get("description")
                if isinstance(description, str) and description.strip():
                    profile["description"] = description.strip()
                if profile:
                    profiles[str(profile_name)] = profile

        prompts: Dict[str, str] = {}
        models: Dict[str, str] = {}
        for role, role_config in roles_block.items():
            if not isinstance(role_config, dict):
                continue

            role_name = str(role)
            model = role_config.get("model")
            if isinstance(model, str) and model.strip():
                models[role_name] = model.strip()

            prompt = role_config.get("prompt")
            if not isinstance(prompt, str):
                continue
            rendered_prompt = prompt
            if "{default_response_format}" in rendered_prompt:
                rendered_prompt = rendered_prompt.replace(
                    "{default_response_format}",
                    default_response_format,
                )
            rendered_prompt = rendered_prompt.strip()
            if not rendered_prompt:
                continue
            prompts[role_name] = rendered_prompt

        if prompts or models or profiles:
            print(
                f"[+] Definiciones de rol cargadas desde {path}: "
                f"prompts={len(prompts)}, models={len(models)}, profiles={len(profiles)}"
            )
        return prompts, models, profiles

    @staticmethod
    def normalize_roles(roles_list: List[str]) -> List[str]:
        return [role for role in dict.fromkeys(roles_list) if role]

    def _clip_for_log(self, text: str) -> str:
        if len(text) <= self.config.max_log_text_chars:
            return text
        clipped = text[: self.config.max_log_text_chars]
        remaining = len(text) - len(clipped)
        return f"{clipped}... [truncated {remaining} chars]"

    def _next_debate_id(self) -> str:
        with self._metrics_lock:
            self._debate_seq += 1
            debate_seq = self._debate_seq
        millis = int(time.time() * 1000)
        return f"debate-{millis}-{debate_seq}"

    def _log_event(self, event: str, **payload: object) -> None:
        if not self.config.enable_event_logging:
            return
        if not self.config.debate_log_file:
            return
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        row.update(payload)
        try:
            with open(self.config.debate_log_file, "a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            if not self._log_warning_printed:
                print(f"[!] No se pudo escribir log en {self.config.debate_log_file}: {exc}")
                self._log_warning_printed = True

    def queue_intervention(
        self,
        message: str,
        debate_id: Optional[str] = None,
        action: str = "feedback",
    ) -> None:
        if not self.config.interventions_file:
            raise ValueError("INTERVENTIONS_FILE no configurado.")
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "message": message,
        }
        if debate_id:
            payload["debate_id"] = debate_id
        with open(self.config.interventions_file, "a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _pull_queued_interventions(self, debate_id: str) -> List[Dict[str, str]]:
        queue_path = self.config.interventions_file
        if not queue_path or not os.path.exists(queue_path):
            return []

        matched: List[Dict[str, str]] = []
        remaining: List[str] = []
        with open(queue_path, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue

                target = str(item.get("debate_id", "")).strip()
                if target and target != debate_id:
                    remaining.append(line)
                    continue
                if not isinstance(item.get("action"), str):
                    item["action"] = "feedback"
                if not isinstance(item.get("message"), str):
                    item["message"] = ""
                matched.append(item)

        with open(queue_path, "w", encoding="utf-8") as file:
            if remaining:
                file.write("\n".join(remaining) + "\n")

        return matched

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.config.base_url}{path}"
        kwargs.setdefault("timeout", self.config.request_timeout_seconds)
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def load_sessions(self) -> Dict[str, str]:
        if not os.path.exists(self.config.sessions_file):
            return {}
        try:
            with open(self.config.sessions_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (json.JSONDecodeError, OSError):
            print(f"[!] No se pudo leer {self.config.sessions_file}. Se ignoran sesiones previas.")
            return {}

        if not isinstance(data, dict):
            print(f"[!] {self.config.sessions_file} no tiene formato valido. Se reinician sesiones.")
            return {}

        return {str(role): str(session_id) for role, session_id in data.items()}

    def save_sessions(self) -> None:
        with open(self.config.sessions_file, "w", encoding="utf-8") as file:
            json.dump(self.sessions, file, indent=2, ensure_ascii=False)

    def _get_messages(self, session_id: str) -> List[Dict]:
        response = self._request("GET", f"/sessions/{session_id}/messages")
        messages = response.json()
        if not isinstance(messages, list):
            raise RuntimeError(f"Respuesta invalida al leer mensajes de sesion {session_id}.")
        return messages

    def _is_session_valid(self, session_id: str) -> bool:
        try:
            self._get_messages(session_id)
            return True
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response else None
            if status in (404, 410):
                return False
            raise

    def create_agent(
        self,
        role: str,
        custom_prompt: Optional[str] = None,
        workspace_path: Optional[str] = None,
        force_recreate: bool = False,
    ) -> str:
        existing_id = self.sessions.get(role)
        if existing_id and not force_recreate:
            if self._is_session_valid(existing_id):
                return existing_id

            print(f"[!] Sesion invalida para {role}. Recreando.")
            self.sessions.pop(role, None)
            self.save_sessions()

        model = self.models.get(role, self.model_default)
        system_prompt = self._resolve_system_prompt(role, custom_prompt)

        payload = {
            "model": model,
            "system_prompt": system_prompt,
        }
        if workspace_path:
            payload["workspace_path"] = workspace_path

        response = self._request("POST", "/sessions", json=payload)
        session_id = response.json().get("id")
        if not session_id:
            raise RuntimeError(f"No se recibio id de sesion al crear agente {role}.")

        self.sessions[role] = session_id
        self.save_sessions()
        print(f"[+] Sesion creada para {role}: {session_id} (model: {model})")
        return session_id

    def send_message(self, session_id: str, content: str, allow_auto_summarize: bool = True) -> str:
        if not session_id:
            raise ValueError("session_id vacio en send_message.")

        if (
            allow_auto_summarize
            and len(content) > self.config.max_context_chars
            and session_id != self.sessions.get("Summarizer")
        ):
            content = self.summarize(content) + "\n[Contexto resumido automaticamente]"

        baseline_messages = self._get_messages(session_id)
        baseline_len = len(baseline_messages)

        payload = {"content": content, "role": "user"}
        self._request("POST", f"/sessions/{session_id}/messages", json=payload)

        start = time.monotonic()
        while time.monotonic() - start < self.config.max_wait_seconds:
            time.sleep(self.config.poll_interval_seconds)
            messages = self._get_messages(session_id)
            if len(messages) <= baseline_len:
                continue

            for message in messages[baseline_len:]:
                if message.get("role") != "assistant":
                    continue

                response_text = message.get("content", "").strip()
                with self._metrics_lock:
                    self.total_input_chars += len(content)
                    self.total_output_chars += len(response_text)
                return response_text

        raise TimeoutError(f"No hubo respuesta del agente en {self.config.max_wait_seconds} segundos.")

    def summarize(self, text: str) -> str:
        summary_role = "Summarizer"
        if summary_role in self.role_prompts:
            summary_session = self.create_agent(summary_role)
        else:
            summary_prompt = (
                "Eres un resumidor profesional. Resume el texto manteniendo "
                "puntos clave, decisiones y argumentos importantes."
            )
            summary_session = self.create_agent(summary_role, custom_prompt=summary_prompt)
        return self.send_message(summary_session, text, allow_auto_summarize=False)

    def parallel_responses(self, roles_list: List[str], prompt: str) -> Dict[str, str]:
        unique_roles = self.normalize_roles(roles_list)
        if not unique_roles:
            return {}

        results: Dict[str, str] = {}
        workers = min(5, len(unique_roles))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for role in unique_roles:
                session_id = self.create_agent(role)
                futures[executor.submit(self.send_message, session_id, prompt)] = role

            for future in as_completed(futures):
                role = futures[future]
                try:
                    results[role] = future.result()
                except Exception as exc:
                    results[role] = f"Error: {exc}"

        return results

    def _estimate_tokens(self) -> Tuple[int, int]:
        with self._metrics_lock:
            input_chars = self.total_input_chars
            output_chars = self.total_output_chars

        # Aproximacion comun: 1 token ~= 4 chars.
        return input_chars // 4, output_chars // 4

    def estimate_cost(self) -> Tuple[float, float]:
        input_tokens, output_tokens = self._estimate_tokens()
        cost_usd = (
            input_tokens * self.config.groq_cost_per_input_token_usd
            + output_tokens * self.config.groq_cost_per_output_token_usd
        )
        cost_eur = cost_usd * self.config.eur_per_usd
        return cost_usd, cost_eur

    def print_cost_summary(self) -> None:
        input_tokens, output_tokens = self._estimate_tokens()
        cost_usd, cost_eur = self.estimate_cost()

        print("\n[RESUMEN DE COSTE]")
        print(f"  Tokens input/output aprox: {input_tokens:,} / {output_tokens:,}")
        print(f"  Coste estimado Groq 70B: ~${cost_usd:.4f} (~{cost_eur:.2f} EUR)")
        if cost_eur > self.config.max_budget_eur:
            print("  [!] Presupuesto superado.")

    def run_debate(
        self,
        initial_task: str,
        sequence: List[str],
        parallel_groups: Optional[List[List[str]]] = None,
        interactive: bool = True,
        check_queued_interventions: bool = True,
        debate_id: Optional[str] = None,
    ) -> str:
        current = initial_task.strip()
        if not current:
            raise ValueError("La tarea inicial no puede estar vacia.")

        debate_id = debate_id or self._next_debate_id()
        debate_status = "completed"
        stop_reason = ""
        started_at = time.monotonic()

        self._log_event(
            "debate_started",
            debate_id=debate_id,
            task=self._clip_for_log(current),
            sequence=sequence,
            parallel_groups=parallel_groups or [],
            budget_eur=self.config.max_budget_eur,
            max_rounds=self.config.max_rounds_per_debate,
        )

        print("\n" + "=" * 60)
        print(f"DEBATE INICIADO [{debate_id}] - Tarea: {current}")
        print("=" * 60 + "\n")

        for round_num, role in enumerate(sequence):
            if round_num >= self.config.max_rounds_per_debate:
                print("[!] Limite de rondas alcanzado.")
                debate_status = "stopped"
                stop_reason = "max_rounds_reached"
                self._log_event(
                    "debate_stopped",
                    debate_id=debate_id,
                    round_num=round_num,
                    reason=stop_reason,
                )
                break

            _, current_cost_eur = self.estimate_cost()
            if current_cost_eur > self.config.max_budget_eur:
                print("[!] Presupuesto aproximado superado.")
                debate_status = "stopped"
                stop_reason = "budget_exceeded"
                self._log_event(
                    "debate_stopped",
                    debate_id=debate_id,
                    round_num=round_num,
                    reason=stop_reason,
                    current_cost_eur=current_cost_eur,
                )
                break

            session_id = self.create_agent(role)
            self._log_event(
                "round_started",
                debate_id=debate_id,
                round_num=round_num,
                role=role,
                context_chars=len(current),
                context=self._clip_for_log(current),
            )
            print(f"-> {role} recibe contexto ({len(current)} chars)...")

            try:
                response = self.send_message(session_id, current)
            except Exception as exc:
                print(f"Error en {role}: {exc}")
                debate_status = "error"
                stop_reason = "role_error"
                self._log_event(
                    "round_error",
                    debate_id=debate_id,
                    round_num=round_num,
                    role=role,
                    error=str(exc),
                )
                break

            preview = response[:500]
            suffix = "..." if len(response) > 500 else ""
            print(f"{role}:\n{preview}{suffix}\n")
            self._log_event(
                "round_response",
                debate_id=debate_id,
                round_num=round_num,
                role=role,
                response_chars=len(response),
                response=self._clip_for_log(response),
            )

            next_context = f"Respuesta de {role}: {response}\nSiguiente turno."

            if role != "Jefe_Sergio":
                if check_queued_interventions:
                    queued = self._pull_queued_interventions(debate_id)
                    for item in queued:
                        queued_action = str(item.get("action", "feedback")).strip().lower()
                        queued_message = str(item.get("message", "")).strip()
                        if queued_action == "stop":
                            print("[!] Detenido por intervencion en cola.")
                            debate_status = "stopped"
                            stop_reason = "queued_stop"
                            self._log_event(
                                "chief_action",
                                debate_id=debate_id,
                                round_num=round_num,
                                role=role,
                                action="queued_stop",
                            )
                            break
                        if queued_message:
                            next_context += (
                                f"\n\n[JEFE SERGIO INTERVIENE]\n{queued_message}\n[/JEFE SERGIO]\n"
                            )
                            print("[+] Feedback en cola aplicado.")
                            self._log_event(
                                "chief_action",
                                debate_id=debate_id,
                                round_num=round_num,
                                role=role,
                                action="queued_feedback",
                                feedback=self._clip_for_log(queued_message),
                            )
                    if stop_reason == "queued_stop":
                        break

                if interactive:
                    print("-" * 50)
                    action = input("Intervenir como Jefe? (Enter=continuar, f=feedback, p=parar): ").strip().lower()
                    if action in ("p", "parar", "stop"):
                        print("Debate detenido por el Jefe.")
                        debate_status = "stopped"
                        stop_reason = "chief_stop"
                        self._log_event(
                            "chief_action",
                            debate_id=debate_id,
                            round_num=round_num,
                            role=role,
                            action="stop",
                        )
                        break

                    if action in ("f", "feedback"):
                        feedback = input("Tu mensaje/feedback: ").strip()
                        if feedback:
                            next_context += (
                                f"\n\n[JEFE SERGIO INTERVIENE]\n{feedback}\n[/JEFE SERGIO]\n"
                            )
                            print("Feedback agregado al flujo.\n")
                            self._log_event(
                                "chief_action",
                                debate_id=debate_id,
                                round_num=round_num,
                                role=role,
                                action="feedback",
                                feedback=self._clip_for_log(feedback),
                            )
                    elif action:
                        self._log_event(
                            "chief_action",
                            debate_id=debate_id,
                            round_num=round_num,
                            role=role,
                            action="invalid_input",
                            raw_action=action,
                        )
                    else:
                        self._log_event(
                            "chief_action",
                            debate_id=debate_id,
                            round_num=round_num,
                            role=role,
                            action="continue",
                        )
                else:
                    self._log_event(
                        "chief_action",
                        debate_id=debate_id,
                        round_num=round_num,
                        role=role,
                        action="auto_continue",
                    )

            current = next_context

            if parallel_groups and round_num < len(parallel_groups):
                parallel_roles = self.normalize_roles(parallel_groups[round_num])
                if parallel_roles:
                    print("\n--- Paralelo ---")
                    self._log_event(
                        "parallel_started",
                        debate_id=debate_id,
                        round_num=round_num,
                        roles=parallel_roles,
                    )
                    parallel_prompt = (
                        f"Respuesta previa de {role}: {response[:600]}...\n"
                        "Responde desde tu rol."
                    )
                    parallel_results = self.parallel_responses(parallel_roles, parallel_prompt)
                    parallel_text = []

                    for parallel_role, parallel_response in parallel_results.items():
                        preview = parallel_response[:250]
                        suffix = "..." if len(parallel_response) > 250 else ""
                        print(f"  {parallel_role}: {preview}{suffix}")
                        parallel_text.append(f"{parallel_role}: {parallel_response}")

                    current += (
                        "\n\n[RESPUESTAS PARALELAS]\n"
                        + "\n".join(parallel_text)
                        + "\n[/RESPUESTAS PARALELAS]\n"
                    )
                    self._log_event(
                        "parallel_completed",
                        debate_id=debate_id,
                        round_num=round_num,
                        results={
                            role_name: self._clip_for_log(role_response)
                            for role_name, role_response in parallel_results.items()
                        },
                    )

        self.print_cost_summary()
        cost_usd, cost_eur = self.estimate_cost()
        duration_seconds = round(time.monotonic() - started_at, 3)
        self._log_event(
            "debate_finished",
            debate_id=debate_id,
            status=debate_status,
            reason=stop_reason,
            duration_seconds=duration_seconds,
            cost_usd=cost_usd,
            cost_eur=cost_eur,
        )
        print("\n=== DEBATE FINALIZADO ===")
        return debate_id


def bootstrap_team(team: OpenCodeTeam, roles: List[str]) -> None:
    for role in roles:
        team.create_agent(role)


DEFAULT_ROLES_SEQUENCE = [
    "Arquitecto",
    "Critico_Dev",
    "Backend_Dev",
    "Frontend_Dev",
    "DevOps_Dev",
    "Tester_Dev",
    "Security_Dev",
    "Jefe_Sergio",
]

DEFAULT_PARALLEL_GROUPS = [
    ["Frontend_Dev", "DevOps_Dev", "Security_Dev"],  # despues de Arquitecto
    ["Tester_Dev", "Critico_Dev"],  # despues del siguiente rol
]


def main() -> None:
    team = OpenCodeTeam()
    bootstrap_team(team, DEFAULT_ROLES_SEQUENCE)

    task = (
        input("Introduce la tarea inicial para el equipo:\n> ").strip()
        or "Disena la arquitectura completa para una SaaS de gestion de tareas "
        "para pymes vascas (Go backend, React frontend, Postgres)."
    )

    team.run_debate(task, DEFAULT_ROLES_SEQUENCE, parallel_groups=DEFAULT_PARALLEL_GROUPS)

    while True:
        extra = input("\nQuieres lanzar otro debate o continuar? (Enter para salir): ").strip()
        if not extra:
            break
        team.run_debate(extra, DEFAULT_ROLES_SEQUENCE, parallel_groups=DEFAULT_PARALLEL_GROUPS)


if __name__ == "__main__":
    main()
