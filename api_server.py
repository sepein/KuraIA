import json
import os
import threading
import hashlib
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from debate_memory import SQLiteDebateMemoryStore
from team_orchestrator_v2 import (
    AppConfig,
    OpenCodeTeam,
)

app = FastAPI(
    title="OpenCode Team Orchestrator API",
    version="0.3.0",
    description="API generica para debates multi-rol sobre OpenCode.",
    docs_url="/swagger",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


class RoleDefinition(BaseModel):
    name: str = Field(..., min_length=1)
    model: Optional[str] = None
    prompt: Optional[str] = None


class DebateCreateRequest(BaseModel):
    task: str = Field(..., min_length=1)
    roles: List[RoleDefinition] = Field(..., min_length=1)
    sequence: Optional[List[str]] = None
    parallel_groups: Optional[List[List[str]]] = None
    discussion_profile: Optional[str] = None
    global_instructions: Optional[str] = None
    global_rules: List[str] = Field(default_factory=list)
    minutes_mode: Literal["programmatic", "agent", "auto"] = "auto"
    bootstrap: bool = True
    check_queued_interventions: bool = True


class DebateCreateResponse(BaseModel):
    debate_id: str
    status: Literal["queued", "running", "completed", "stopped", "error"]


class InterventionRequest(BaseModel):
    action: Literal["feedback", "stop"] = "feedback"
    message: Optional[str] = None


class MemoryImportRequest(BaseModel):
    snapshot: Dict[str, object]
    overwrite: bool = False


@dataclass
class DebateRuntime:
    debate_id: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: str = ""


_runtime_lock = threading.Lock()
_runtime_debates: Dict[str, DebateRuntime] = {}
_config = AppConfig()
_memory_store = SQLiteDebateMemoryStore(os.getenv("API_MEMORY_DB_FILE", "api_memory.db"))
_minutes_role_name = os.getenv("MINUTES_ROLE_NAME", "Secretario_Actas")
_default_minutes_role_prompt = (
    "Eres Secretario_Actas. Tu trabajo es redactar actas ejecutivas cortas y precisas. "
    "No uses estilo literario ni frases largas. "
    "Resalta decisiones, desacuerdos relevantes, riesgos y acciones concretas con responsable. "
    "No inventes datos y marca incertidumbre cuando falte informacion."
)
_output_events_enabled = str(os.getenv("OUTPUT_EVENTS_ENABLED", "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
_default_output_roles = f"{_config.chief_role_name},{_minutes_role_name}"
_output_events_allowed_roles = {
    role.strip()
    for role in (os.getenv("OUTPUT_EVENTS_ALLOWED_ROLES", _default_output_roles)).split(",")
    if role.strip()
}
_task_tag = "#tarea"


@app.get("/docs", include_in_schema=False)
def docs_redirect() -> RedirectResponse:
    return RedirectResponse(url="/swagger")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_events_for_debate(debate_id: str) -> List[Dict]:
    path = Path(_config.debate_log_file)
    if not path.exists():
        return []

    events: List[Dict] = []
    with path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if str(item.get("debate_id", "")) == debate_id:
                events.append(item)
    return events


def _summarize_events(events: List[Dict]) -> Dict[str, object]:
    summary: Dict[str, object] = {
        "status": "queued",
        "reason": "",
        "started_at": "",
        "finished_at": "",
        "rounds": 0,
        "cost_eur": None,
    }

    if not events:
        return summary

    summary["status"] = "running"

    for event in events:
        event_type = str(event.get("event", ""))
        if event_type == "debate_started":
            summary["started_at"] = event.get("ts", "")
        elif event_type == "round_response":
            summary["rounds"] = int(summary["rounds"]) + 1
        elif event_type == "debate_stopped":
            summary["status"] = "stopped"
            summary["reason"] = str(event.get("reason", ""))
        elif event_type == "debate_finished":
            summary["status"] = str(event.get("status", "completed"))
            summary["reason"] = str(event.get("reason", ""))
            summary["finished_at"] = event.get("ts", "")
            summary["cost_eur"] = event.get("cost_eur")

    return summary


def _prepare_roles(request: DebateCreateRequest) -> List[RoleDefinition]:
    names = [role.name for role in request.roles]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="roles contiene nombres duplicados")
    return request.roles


def _prepare_sequence(request: DebateCreateRequest, roles: List[RoleDefinition]) -> List[str]:
    role_names = {role.name for role in roles}
    sequence = request.sequence or [role.name for role in roles]
    if not sequence:
        raise HTTPException(status_code=400, detail="sequence no puede estar vacia")

    unknown = [role for role in sequence if role not in role_names]
    if unknown:
        raise HTTPException(status_code=400, detail=f"sequence incluye roles no definidos: {unknown}")
    return sequence


def _clean_rules(values: List[str]) -> List[str]:
    cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    return list(dict.fromkeys(cleaned))


def _resolve_profile(team: OpenCodeTeam, profile_name: Optional[str]) -> Dict[str, object]:
    if not profile_name:
        return {}
    profile = team.discussion_profiles.get(profile_name)
    if profile is None:
        raise HTTPException(status_code=400, detail=f"discussion_profile no encontrado: {profile_name}")
    return profile


def _compose_participant_prompt(
    role_name: str,
    base_prompt: str,
    profile_name: Optional[str],
    profile: Dict[str, object],
    global_instructions: Optional[str],
    global_rules: List[str],
) -> str:
    sections: List[str] = []
    profile_instructions = str(profile.get("global_instructions", "")).strip()
    profile_rules = profile.get("rules")
    profile_rules_clean = [str(rule).strip() for rule in profile_rules] if isinstance(profile_rules, list) else []
    combined_rules = _clean_rules(profile_rules_clean + global_rules)

    if profile_name or profile_instructions or global_instructions or combined_rules:
        sections.append("CONTEXTO GLOBAL DE LA MESA:")
        if profile_name:
            sections.append(f"- Tipo de mesa: {profile_name}")
        if profile_instructions:
            sections.append(f"- Instrucciones base del perfil: {profile_instructions}")
        if global_instructions and global_instructions.strip():
            sections.append(f"- Instrucciones globales de esta ejecucion: {global_instructions.strip()}")
        if combined_rules:
            sections.append("- Reglas globales obligatorias:")
            for idx, rule in enumerate(combined_rules, start=1):
                sections.append(f"  {idx}. {rule}")
        sections.append(f"- Participante actual: {role_name}")
        sections.append(
            "- No rompas tu rol. Aplica el contexto global sin contradecir restricciones de seguridad o calidad."
        )

    sections.append("INSTRUCCIONES ESPECIFICAS DEL ROL:")
    sections.append(base_prompt.strip())

    return "\n".join(sections).strip()


def _role_to_dict(role: RoleDefinition) -> Dict[str, object]:
    return {
        "name": role.name,
        "model": role.model,
        "prompt": role.prompt,
    }


def _update_memory_record(debate_id: str, **fields: object) -> None:
    current = _memory_store.get_debate(debate_id) or {"debate_id": debate_id}
    current.update(fields)
    _memory_store.upsert_debate(current)


def _build_final_minutes(task: str, summary: Dict[str, object], events: List[Dict]) -> str:
    lines: List[str] = [
        "ACTA FINAL DE LA MESA",
        "",
        f"Tarea: {task.strip()}",
        f"Estado: {summary.get('status', '')}",
        f"Motivo cierre: {summary.get('reason', '')}",
        f"Rondas con respuesta: {summary.get('rounds', 0)}",
        f"Coste EUR estimado: {summary.get('cost_eur')}",
        "",
        "Puntos clave por turno:",
    ]

    has_rounds = False
    for event in events:
        if event.get("event") != "round_response":
            continue
        has_rounds = True
        role = str(event.get("role", "")).strip() or "rol"
        response = str(event.get("response", "")).strip()
        preview = response[:280] + ("..." if len(response) > 280 else "")
        lines.append(f"- {role}: {preview}")

    if not has_rounds:
        lines.append("- Sin respuestas registradas.")

    interventions = [
        item for item in events if item.get("event") == "chief_action" and str(item.get("action", "")).strip()
    ]
    lines.append("")
    lines.append("Intervenciones del conductor:")
    if not interventions:
        lines.append("- No hubo intervenciones del conductor.")
    else:
        for item in interventions:
            action = str(item.get("action", "")).strip()
            feedback = str(item.get("feedback", "")).strip()
            if feedback:
                feedback = feedback[:180] + ("..." if len(feedback) > 180 else "")
                lines.append(f"- {action}: {feedback}")
            else:
                lines.append(f"- {action}")

    return "\n".join(lines).strip()


def _build_minutes_context(task: str, summary: Dict[str, object], events: List[Dict]) -> str:
    lines: List[str] = [
        f"TAREA: {task.strip()}",
        f"ESTADO: {summary.get('status', '')}",
        f"MOTIVO_CIERRE: {summary.get('reason', '')}",
        f"RONDAS: {summary.get('rounds', 0)}",
        f"COSTE_EUR: {summary.get('cost_eur')}",
        "",
        "INTERVENCIONES_RELEVANTES:",
    ]

    round_events = [event for event in events if event.get("event") == "round_response"]
    if not round_events:
        lines.append("- Sin respuestas de participantes.")
    else:
        for event in round_events[:20]:
            role = str(event.get("role", "")).strip() or "rol"
            response = str(event.get("response", "")).strip()
            preview = response[:600] + ("..." if len(response) > 600 else "")
            lines.append(f"- {role}: {preview}")

    chief_events = [
        event for event in events if event.get("event") == "chief_action" and str(event.get("action", "")).strip()
    ]
    lines.append("")
    lines.append("INTERVENCIONES_CONDUCTOR:")
    if not chief_events:
        lines.append("- No hubo intervenciones del conductor.")
    else:
        for event in chief_events[:20]:
            action = str(event.get("action", "")).strip()
            feedback = str(event.get("feedback", "")).strip()
            if feedback:
                feedback = feedback[:300] + ("..." if len(feedback) > 300 else "")
                lines.append(f"- {action}: {feedback}")
            else:
                lines.append(f"- {action}")

    return "\n".join(lines).strip()


def _build_final_minutes_with_agent(
    team: OpenCodeTeam,
    task: str,
    summary: Dict[str, object],
    events: List[Dict],
) -> str:
    context = _build_minutes_context(task, summary, events)
    instruction = (
        "Redacta un acta ejecutiva breve. "
        "No escribas en estilo literario. "
        "Se directo, profesional y concreto.\n\n"
        "Formato obligatorio:\n"
        "1) DECISIONES CLAVE (max 5 bullets)\n"
        "2) PUNTOS DESTACADOS POR PARTICIPANTE (solo lo mas relevante)\n"
        "3) RIESGOS Y DESACUERDOS (si existen)\n"
        "4) ACCIONES SIGUIENTES (accion + responsable)\n\n"
        "Reglas:\n"
        "- Maximo 900 palabras.\n"
        "- No inventes nada fuera del contexto.\n"
        "- Si falta dato, dilo de forma explicita.\n\n"
        f"CONTEXTO:\n{context}"
    )

    custom_prompt = None if _minutes_role_name in team.role_prompts else _default_minutes_role_prompt
    session_id = team.create_agent(_minutes_role_name, custom_prompt=custom_prompt)
    return team.send_message(session_id, instruction).strip()


def _resolve_final_minutes(
    team: OpenCodeTeam,
    request: DebateCreateRequest,
    summary: Dict[str, object],
    events: List[Dict],
) -> Tuple[str, str]:
    programmatic_minutes = _build_final_minutes(request.task, summary, events)
    mode = request.minutes_mode

    if mode == "programmatic":
        return programmatic_minutes, "programmatic"

    if mode in ("agent", "auto"):
        try:
            agent_minutes = _build_final_minutes_with_agent(team, request.task, summary, events)
            if agent_minutes:
                return agent_minutes, "agent"
        except Exception:
            if mode == "agent":
                fallback = (
                    "ACTA GENERADA EN FALLBACK PROGRAMATICO (fallo al generar por agente).\n\n"
                    f"{programmatic_minutes}"
                )
                return fallback, "programmatic_fallback"

    return programmatic_minutes, "programmatic"


def _normalize_task_action(raw_action: str) -> Optional[str]:
    action = str(raw_action or "").strip().lower()
    action_map = {
        "crear": "create",
        "create": "create",
        "alta": "create",
        "nueva": "create",
        "nuevo": "create",
        "add": "create",
        "modificar": "update",
        "actualizar": "update",
        "editar": "update",
        "update": "update",
        "borrar": "delete",
        "eliminar": "delete",
        "delete": "delete",
        "remove": "delete",
    }
    return action_map.get(action)


def _parse_task_command(command: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    text = str(command or "").strip()
    if not text.lower().startswith(_task_tag):
        return None

    rest = text[len(_task_tag):].strip()
    if not rest:
        return None

    action = ""
    payload: Dict[str, Any] = {}
    remainder = ""
    if " " in rest:
        action, remainder = rest.split(" ", 1)
        remainder = remainder.strip()
    else:
        action = rest.strip()

    normalized_action = _normalize_task_action(action)
    if not normalized_action:
        return None

    if remainder.startswith("{") and remainder.endswith("}"):
        try:
            loaded = json.loads(remainder)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            payload = {str(key): value for key, value in loaded.items()}
    else:
        raw_tokens = shlex.split(remainder) if remainder else []
        free_text_parts: List[str] = []
        for token in raw_tokens:
            if "=" in token:
                key, value = token.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    payload[key] = value
            else:
                free_text_parts.append(token)

        if free_text_parts:
            if normalized_action == "create" and "title" not in payload and "titulo" not in payload:
                payload["title"] = " ".join(free_text_parts).strip()
            else:
                payload["text"] = " ".join(free_text_parts).strip()

    if not payload and normalized_action == "create":
        payload["title"] = ""

    return normalized_action, payload


def _extract_task_commands_from_text(text: str) -> List[str]:
    commands: List[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pos = line.lower().find(_task_tag)
        if pos < 0:
            continue
        commands.append(line[pos:].strip())
    return commands


def _output_event_key(
    debate_id: str,
    source_event: str,
    source_role: str,
    source_ts: str,
    command: str,
) -> str:
    base = "|".join(
        [
            str(debate_id).strip(),
            str(source_event).strip(),
            str(source_role).strip(),
            str(source_ts).strip(),
            str(command).strip(),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _build_output_events_for_debate(
    debate_id: str,
    events: List[Dict[str, object]],
    final_minutes: str = "",
) -> List[Dict[str, object]]:
    if not _output_events_enabled:
        return []

    candidates: List[Dict[str, str]] = []
    for event in events:
        event_type = str(event.get("event", "")).strip()
        source_ts = str(event.get("ts", "")).strip()
        if event_type == "round_response":
            candidates.append(
                {
                    "source_event": "round_response",
                    "source_role": str(event.get("role", "")).strip(),
                    "source_ts": source_ts,
                    "text": str(event.get("response", "")),
                }
            )
        elif event_type == "chief_action":
            feedback = str(event.get("feedback", "")).strip()
            if feedback:
                candidates.append(
                    {
                        "source_event": "chief_action",
                        "source_role": "conductor",
                        "source_ts": source_ts,
                        "text": feedback,
                    }
                )

    if final_minutes.strip():
        candidates.append(
            {
                "source_event": "final_minutes",
                "source_role": _minutes_role_name,
                "source_ts": _now_iso(),
                "text": final_minutes,
            }
        )

    output_events: List[Dict[str, object]] = []
    seen_keys: set[str] = set()
    for candidate in candidates:
        source_role = str(candidate.get("source_role", "")).strip()
        if _output_events_allowed_roles and source_role not in _output_events_allowed_roles:
            continue

        text = str(candidate.get("text", ""))
        for command in _extract_task_commands_from_text(text):
            parsed = _parse_task_command(command)
            if parsed is None:
                continue
            action, payload = parsed
            event_key = _output_event_key(
                debate_id=debate_id,
                source_event=str(candidate.get("source_event", "")),
                source_role=source_role,
                source_ts=str(candidate.get("source_ts", "")),
                command=command,
            )
            if event_key in seen_keys:
                continue
            seen_keys.add(event_key)

            output_events.append(
                {
                    "output_event_id": f"oe-{uuid4().hex}",
                    "debate_id": debate_id,
                    "ts": _now_iso(),
                    "type": "task_command",
                    "entity": "task",
                    "action": action,
                    "payload": payload,
                    "trigger": _task_tag,
                    "raw_command": command,
                    "source_event": str(candidate.get("source_event", "")),
                    "source_role": source_role,
                    "source_ts": str(candidate.get("source_ts", "")),
                    "idempotency_key": event_key,
                }
            )

    return output_events


def _ensure_output_events(
    debate_id: str,
    events: List[Dict[str, object]],
    final_minutes: str,
) -> List[Dict[str, object]]:
    existing = _memory_store.get_output_events(debate_id, limit=100_000, reverse=False)
    if existing:
        return existing

    generated = _build_output_events_for_debate(debate_id, events, final_minutes=final_minutes)
    if generated:
        _memory_store.save_output_events(debate_id, generated)
    return generated


def _run_debate_worker(
    debate_id: str,
    request: DebateCreateRequest,
    roles: List[RoleDefinition],
    sequence: List[str],
) -> None:
    with _runtime_lock:
        runtime = _runtime_debates[debate_id]
        runtime.status = "running"
        runtime.started_at = _now_iso()

    _update_memory_record(
        debate_id,
        status="running",
        started_at=runtime.started_at,
    )

    try:
        team = OpenCodeTeam(config=AppConfig())
        profile = team.discussion_profiles.get(request.discussion_profile or "", {})
        global_rules = _clean_rules(request.global_rules)

        for role in roles:
            base_prompt = role.prompt or team._resolve_system_prompt(role.name, None)
            composed_prompt = _compose_participant_prompt(
                role_name=role.name,
                base_prompt=base_prompt,
                profile_name=request.discussion_profile,
                profile=profile if isinstance(profile, dict) else {},
                global_instructions=request.global_instructions,
                global_rules=global_rules,
            )
            team.role_prompts[role.name] = composed_prompt
            if role.model:
                team.models[role.name] = role.model

        if request.bootstrap:
            for role_name in sequence:
                team.create_agent(role_name)

        parallel_groups = request.parallel_groups if request.parallel_groups is not None else []
        team.run_debate(
            request.task,
            sequence,
            parallel_groups=parallel_groups,
            interactive=False,
            check_queued_interventions=request.check_queued_interventions,
            debate_id=debate_id,
        )

        events = _load_events_for_debate(debate_id)
        if events:
            _memory_store.save_events(debate_id, events)
        summary = _summarize_events(events)

        with _runtime_lock:
            runtime = _runtime_debates[debate_id]
            runtime.status = str(summary.get("status") or "completed")
            runtime.finished_at = _now_iso()

        final_minutes, final_minutes_source = _resolve_final_minutes(team, request, summary, events)
        output_events = _build_output_events_for_debate(
            debate_id=debate_id,
            events=events,
            final_minutes=final_minutes,
        )
        _memory_store.save_output_events(debate_id, output_events)
        _update_memory_record(
            debate_id,
            status=runtime.status,
            finished_at=runtime.finished_at,
            reason=str(summary.get("reason", "")),
            rounds=int(summary.get("rounds", 0)),
            cost_eur=summary.get("cost_eur"),
            summary=summary,
            final_minutes=final_minutes,
            final_minutes_source=final_minutes_source,
            output_events_count=len(output_events),
            error="",
        )

    except Exception as exc:
        with _runtime_lock:
            runtime = _runtime_debates[debate_id]
            runtime.status = "error"
            runtime.error = str(exc)
            runtime.finished_at = _now_iso()

        _update_memory_record(
            debate_id,
            status="error",
            finished_at=runtime.finished_at,
            error=str(exc),
        )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/discussion-profiles")
def list_discussion_profiles() -> Dict[str, object]:
    team = OpenCodeTeam(config=AppConfig(enable_event_logging=False))
    profiles = team.discussion_profiles
    return {
        "count": len(profiles),
        "items": [
            {
                "name": name,
                "description": profile.get("description", ""),
                "rules_count": len(profile.get("rules", [])) if isinstance(profile.get("rules"), list) else 0,
            }
            for name, profile in sorted(profiles.items(), key=lambda item: item[0])
        ],
    }


@app.post("/debates", response_model=DebateCreateResponse)
def create_debate(request: DebateCreateRequest) -> DebateCreateResponse:
    roles = _prepare_roles(request)
    sequence = _prepare_sequence(request, roles)
    validation_team = OpenCodeTeam(config=AppConfig(enable_event_logging=False))
    _resolve_profile(validation_team, request.discussion_profile)

    debate_id = f"debate-{uuid4().hex}"
    runtime = DebateRuntime(
        debate_id=debate_id,
        status="queued",
        created_at=_now_iso(),
    )

    with _runtime_lock:
        _runtime_debates[debate_id] = runtime

    _memory_store.upsert_debate(
        {
            "debate_id": debate_id,
            "status": "queued",
            "reason": "",
            "created_at": runtime.created_at,
            "started_at": "",
            "finished_at": "",
            "rounds": 0,
            "cost_eur": None,
            "error": "",
            "task": request.task,
            "discussion_profile": request.discussion_profile or "",
            "global_instructions": request.global_instructions or "",
            "global_rules": _clean_rules(request.global_rules),
            "roles": [_role_to_dict(role) for role in roles],
            "sequence": sequence,
            "parallel_groups": request.parallel_groups if request.parallel_groups is not None else [],
            "minutes_mode": request.minutes_mode,
            "final_minutes": "",
            "final_minutes_source": "pending",
            "output_events_count": 0,
            "summary": {},
        }
    )

    worker = threading.Thread(
        target=_run_debate_worker,
        args=(debate_id, request, roles, sequence),
        daemon=True,
    )
    worker.start()

    return DebateCreateResponse(debate_id=debate_id, status="queued")


@app.get("/debates/{debate_id}")
def get_debate(debate_id: str) -> Dict[str, object]:
    with _runtime_lock:
        runtime = _runtime_debates.get(debate_id)

    persisted = _memory_store.get_debate(debate_id)
    events = _memory_store.get_events(debate_id, limit=5000, reverse=False)
    if not events:
        events = _load_events_for_debate(debate_id)
        if events:
            _memory_store.save_events(debate_id, events)

    if runtime is None and persisted is None and not events:
        raise HTTPException(status_code=404, detail="debate_id no encontrado")

    summary = _summarize_events(events) if events else dict((persisted or {}).get("summary", {}))

    status = runtime.status if runtime else str((persisted or {}).get("status", summary.get("status", "unknown")))
    reason = str(summary.get("reason", "") or (persisted or {}).get("reason", ""))
    created_at = runtime.created_at if runtime else str((persisted or {}).get("created_at", ""))
    started_at = str(
        summary.get("started_at", "")
        or (runtime.started_at if runtime else "")
        or (persisted or {}).get("started_at", "")
    )
    finished_at = str(
        summary.get("finished_at", "")
        or (runtime.finished_at if runtime else "")
        or (persisted or {}).get("finished_at", "")
    )
    rounds = int(summary.get("rounds", (persisted or {}).get("rounds", 0) or 0))
    cost_eur = summary.get("cost_eur") if summary.get("cost_eur") is not None else (persisted or {}).get("cost_eur")
    error = runtime.error if runtime else str((persisted or {}).get("error", ""))
    output_events_count = int((persisted or {}).get("output_events_count", 0) or 0)

    return {
        "debate_id": debate_id,
        "status": status,
        "reason": reason,
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "rounds": rounds,
        "cost_eur": cost_eur,
        "error": error,
        "output_events_count": output_events_count,
    }


@app.get("/debates/{debate_id}/events")
def get_debate_events(
    debate_id: str,
    limit: int = Query(200, ge=1, le=5000),
    reverse: bool = Query(False),
) -> Dict[str, object]:
    events = _memory_store.get_events(debate_id, limit=limit, reverse=reverse)
    if not events:
        events = _load_events_for_debate(debate_id)
        if events:
            _memory_store.save_events(debate_id, events)
            events = events[-limit:]
            if reverse:
                events = list(reversed(events))

    if not events:
        raise HTTPException(status_code=404, detail="No hay eventos para ese debate_id")

    return {
        "debate_id": debate_id,
        "count": len(events),
        "events": events,
    }


@app.get("/debates/{debate_id}/memory")
def get_debate_memory(debate_id: str) -> Dict[str, object]:
    record = _memory_store.get_debate(debate_id)
    if not record:
        raise HTTPException(status_code=404, detail="No hay memoria persistida para ese debate_id")

    events_count = len(_memory_store.get_events(debate_id, limit=100_000, reverse=False))
    output_events = _ensure_output_events(
        debate_id=debate_id,
        events=_memory_store.get_events(debate_id, limit=100_000, reverse=False),
        final_minutes=str(record.get("final_minutes", "")),
    )
    if int(record.get("output_events_count", 0) or 0) != len(output_events):
        _update_memory_record(debate_id, output_events_count=len(output_events))
        record = _memory_store.get_debate(debate_id) or record
    return {
        "debate_id": debate_id,
        "memory": record,
        "events_count": events_count,
        "output_events_count": len(output_events),
    }


@app.get("/debates/{debate_id}/export")
def export_debate_memory(
    debate_id: str,
    include_events: bool = Query(True),
    include_output_events: bool = Query(True),
) -> Dict[str, object]:
    snapshot = _memory_store.export_debate(
        debate_id,
        include_events=include_events,
        include_output_events=include_output_events,
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="No hay memoria para exportar en ese debate_id")
    return snapshot


@app.get("/debates/{debate_id}/output-events")
def get_debate_output_events(
    debate_id: str,
    limit: int = Query(200, ge=1, le=5000),
    reverse: bool = Query(False),
) -> Dict[str, object]:
    record = _memory_store.get_debate(debate_id)
    if not record:
        raise HTTPException(status_code=404, detail="debate_id no encontrado")

    events = _memory_store.get_events(debate_id, limit=100_000, reverse=False)
    output_events = _ensure_output_events(
        debate_id=debate_id,
        events=events,
        final_minutes=str(record.get("final_minutes", "")),
    )
    if int(record.get("output_events_count", 0) or 0) != len(output_events):
        _update_memory_record(debate_id, output_events_count=len(output_events))

    selected = output_events[-limit:]
    if reverse:
        selected = list(reversed(selected))

    return {
        "debate_id": debate_id,
        "count": len(selected),
        "events": selected,
    }


@app.get("/memory/export")
def export_memory(
    limit: int = Query(50, ge=1, le=1000),
    include_events: bool = Query(False),
    include_output_events: bool = Query(False),
) -> Dict[str, object]:
    return _memory_store.export_many(
        limit=limit,
        include_events=include_events,
        include_output_events=include_output_events,
    )


@app.post("/memory/import")
def import_memory(request: MemoryImportRequest) -> Dict[str, str]:
    try:
        result = _memory_store.import_snapshot(request.snapshot, overwrite=request.overwrite)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    debate_id = result.get("debate_id", "")
    if debate_id and result.get("status") == "imported":
        record = _memory_store.get_debate(debate_id) or {}
        output_events = _ensure_output_events(
            debate_id=debate_id,
            events=_memory_store.get_events(debate_id, limit=100_000, reverse=False),
            final_minutes=str(record.get("final_minutes", "")),
        )
        _update_memory_record(debate_id, output_events_count=len(output_events))
    return result


@app.post("/debates/{debate_id}/interventions")
def enqueue_intervention(debate_id: str, request: InterventionRequest) -> Dict[str, str]:
    if request.action == "feedback" and not (request.message or "").strip():
        raise HTTPException(status_code=400, detail="message es obligatorio para action=feedback")

    with _runtime_lock:
        runtime = _runtime_debates.get(debate_id)

    if runtime is None:
        if _memory_store.get_debate(debate_id) is None and not _load_events_for_debate(debate_id):
            raise HTTPException(status_code=404, detail="debate_id no encontrado")

    team = OpenCodeTeam(config=AppConfig())
    message = (request.message or "").strip() or "STOP solicitado por API"
    team.queue_intervention(message, debate_id=debate_id, action=request.action)

    return {
        "debate_id": debate_id,
        "status": "queued",
        "action": request.action,
    }


@app.get("/debates")
def list_debates(limit: int = Query(50, ge=1, le=500)) -> Dict[str, object]:
    persisted = _memory_store.list_debates(limit=max(limit * 3, limit))
    by_id: Dict[str, Dict[str, object]] = {}
    for item in persisted:
        debate_id = str(item.get("debate_id", "")).strip()
        if debate_id:
            by_id[debate_id] = item

    with _runtime_lock:
        runtimes = list(_runtime_debates.values())

    for runtime in runtimes:
        existing = by_id.get(runtime.debate_id, {"debate_id": runtime.debate_id})
        existing.update(
            {
                "debate_id": runtime.debate_id,
                "status": runtime.status,
                "created_at": runtime.created_at,
                "started_at": runtime.started_at,
                "finished_at": runtime.finished_at,
                "error": runtime.error,
            }
        )
        by_id[runtime.debate_id] = existing

    items = sorted(
        by_id.values(),
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )[:limit]

    return {
        "count": len(items),
        "items": [
            {
                "debate_id": str(item.get("debate_id", "")),
                "status": str(item.get("status", "unknown")),
                "created_at": item.get("created_at", ""),
                "started_at": item.get("started_at", ""),
                "finished_at": item.get("finished_at", ""),
                "error": item.get("error", ""),
                "output_events_count": int(item.get("output_events_count", 0) or 0),
            }
            for item in items
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
