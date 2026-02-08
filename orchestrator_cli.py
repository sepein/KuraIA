import json
import os
from typing import Dict, List, Optional, Tuple

try:
    import typer
except ModuleNotFoundError as exc:
    raise SystemExit("Typer no esta instalado. Ejecuta: pip install typer") from exc

from team_orchestrator_v2 import (
    AppConfig,
    DEFAULT_PARALLEL_GROUPS,
    DEFAULT_ROLES_SEQUENCE,
    OpenCodeTeam,
    bootstrap_team,
)

app = typer.Typer(help="CLI de OpenCode Team Orchestrator")
export_app = typer.Typer(help="Exportacion de debates")
app.add_typer(export_app, name="export")


def _load_jsonl(path: str) -> List[Dict]:
    if not path or not os.path.exists(path):
        return []
    rows: List[Dict] = []
    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _group_events_by_debate(events: List[Dict]) -> Tuple[Dict[str, List[Dict]], List[str]]:
    grouped: Dict[str, List[Dict]] = {}
    order: List[str] = []
    for event in events:
        debate_id = str(event.get("debate_id", "")).strip()
        if not debate_id:
            continue
        if debate_id not in grouped:
            grouped[debate_id] = []
            order.append(debate_id)
        grouped[debate_id].append(event)
    return grouped, order


def _summarize_debate(debate_id: str, events: List[Dict]) -> Dict[str, object]:
    summary: Dict[str, object] = {
        "debate_id": debate_id,
        "task": "",
        "started_at": "",
        "finished_at": "",
        "status": "running",
        "reason": "",
        "rounds": 0,
        "cost_eur": None,
    }
    for event in events:
        event_type = event.get("event")
        if event_type == "debate_started":
            summary["task"] = event.get("task", "")
            summary["started_at"] = event.get("ts", "")
        elif event_type == "round_response":
            summary["rounds"] = int(summary["rounds"]) + 1
        elif event_type == "debate_stopped":
            summary["status"] = "stopped"
            summary["reason"] = event.get("reason", "")
        elif event_type == "debate_finished":
            summary["status"] = event.get("status", "completed")
            summary["reason"] = event.get("reason", "")
            summary["finished_at"] = event.get("ts", "")
            summary["cost_eur"] = event.get("cost_eur")
    return summary


def _load_debate_summaries(log_file: str) -> Tuple[List[Dict[str, object]], Dict[str, List[Dict]]]:
    events = _load_jsonl(log_file)
    grouped, order = _group_events_by_debate(events)
    summaries: List[Dict[str, object]] = []
    for debate_id in order:
        summaries.append(_summarize_debate(debate_id, grouped[debate_id]))
    return summaries, grouped


def _markdown_block(title: str, text: str) -> List[str]:
    safe_text = text.strip() or "(vacio)"
    return [f"### {title}", "", "```text", safe_text, "```", ""]


def _render_markdown(summary: Dict[str, object], events: List[Dict]) -> str:
    lines: List[str] = [
        f"# Debate {summary['debate_id']}",
        "",
        f"- Estado: {summary.get('status', '')}",
        f"- Rondas con respuesta: {summary.get('rounds', 0)}",
        f"- Coste EUR estimado: {summary.get('cost_eur', 'n/a')}",
        f"- Inicio: {summary.get('started_at', '')}",
        f"- Fin: {summary.get('finished_at', '')}",
        "",
    ]

    task = str(summary.get("task", "")).strip()
    if task:
        lines.extend(_markdown_block("Tarea inicial", task))

    for event in events:
        event_type = event.get("event")
        if event_type == "round_response":
            round_num = event.get("round_num", "?")
            role = event.get("role", "")
            lines.append(f"## Ronda {round_num} - {role}")
            lines.append("")
            lines.extend(_markdown_block("Respuesta", str(event.get("response", ""))))
        elif event_type == "chief_action":
            action = event.get("action", "")
            feedback = str(event.get("feedback", "")).strip()
            lines.append(f"- Chief action: `{action}`")
            if feedback:
                lines.append(f"  Feedback: {feedback}")
        elif event_type == "parallel_completed":
            lines.append("### Respuestas paralelas")
            lines.append("")
            results = event.get("results", {})
            if isinstance(results, dict):
                for role, response in results.items():
                    lines.append(f"- **{role}**: {response}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


@app.command()
def start(
    task: str = typer.Argument(..., help="Tarea inicial del debate."),
    no_interactive: bool = typer.Option(False, "--no-interactive", help="Desactiva prompts en caliente."),
    no_parallel: bool = typer.Option(False, "--no-parallel", help="Desactiva grupos paralelos."),
    skip_bootstrap: bool = typer.Option(False, "--skip-bootstrap", help="No crear/revalidar sesiones al inicio."),
) -> None:
    """Inicia un debate."""
    team = OpenCodeTeam()
    if not skip_bootstrap:
        bootstrap_team(team, DEFAULT_ROLES_SEQUENCE)

    parallel_groups = None if no_parallel else DEFAULT_PARALLEL_GROUPS
    debate_id = team.run_debate(
        task,
        DEFAULT_ROLES_SEQUENCE,
        parallel_groups=parallel_groups,
        interactive=not no_interactive,
        check_queued_interventions=True,
    )
    typer.echo(f"Debate finalizado: {debate_id}")


@app.command()
def intervene(
    message: str = typer.Argument("", help="Feedback para inyectar al debate activo."),
    debate_id: Optional[str] = typer.Option(None, "--debate-id", help="Debate objetivo. Si se omite, aplica al siguiente turno disponible."),
    stop: bool = typer.Option(False, "--stop", help="Solicita parada del debate en el siguiente checkpoint."),
) -> None:
    """Encola una intervencion del jefe para debates en ejecucion."""
    if not stop and not message.strip():
        raise typer.BadParameter("Debes indicar mensaje o usar --stop.")

    team = OpenCodeTeam()
    action = "stop" if stop else "feedback"
    payload_message = message.strip() if message.strip() else "STOP solicitado por CLI"
    team.queue_intervention(payload_message, debate_id=debate_id, action=action)
    target = debate_id or "ANY"
    typer.echo(f"Intervencion encolada. action={action} debate={target}")


@app.command()
def status() -> None:
    """Muestra estado resumido del sistema."""
    config = AppConfig()
    team = OpenCodeTeam(config=config)
    summaries, _ = _load_debate_summaries(config.debate_log_file)

    typer.echo(f"BASE_URL: {config.base_url}")
    typer.echo(f"Sesiones guardadas: {len(team.sessions)} ({config.sessions_file})")
    typer.echo(f"Log debates: {config.debate_log_file}")

    if not summaries:
        typer.echo("No hay debates registrados.")
        return

    running = [item for item in summaries if item.get("status") == "running"]
    if running:
        typer.echo(f"Debates en curso (estimado): {len(running)}")
        for item in running[-3:]:
            typer.echo(f"- {item['debate_id']} task={item.get('task', '')}")

    last = summaries[-1]
    typer.echo("Ultimo debate:")
    typer.echo(f"- id: {last['debate_id']}")
    typer.echo(f"- estado: {last.get('status')}")
    typer.echo(f"- rondas: {last.get('rounds')}")
    typer.echo(f"- coste_eur: {last.get('cost_eur')}")


@app.command()
def history(limit: int = typer.Option(10, "--limit", min=1, help="Numero maximo de debates.")) -> None:
    """Lista historial de debates."""
    config = AppConfig()
    summaries, _ = _load_debate_summaries(config.debate_log_file)
    if not summaries:
        typer.echo("No hay historial.")
        return

    typer.echo(f"Mostrando {min(limit, len(summaries))} debates mas recientes:")
    for item in summaries[-limit:][::-1]:
        typer.echo(
            f"- {item['debate_id']} | status={item.get('status')} | rounds={item.get('rounds')} | "
            f"cost_eur={item.get('cost_eur')}"
        )


@export_app.command("last")
def export_last(
    format: str = typer.Option("md", "--format", help="Formato de salida: md"),
    output: Optional[str] = typer.Option(None, "--output", help="Ruta de salida."),
) -> None:
    """Exporta el ultimo debate."""
    fmt = format.strip().lower()
    if fmt != "md":
        raise typer.BadParameter("Solo se soporta --format md por ahora.")

    config = AppConfig()
    summaries, grouped = _load_debate_summaries(config.debate_log_file)
    if not summaries:
        raise typer.BadParameter("No hay debates para exportar.")

    last = summaries[-1]
    debate_id = str(last["debate_id"])
    events = grouped.get(debate_id, [])
    content = _render_markdown(last, events)

    output_path = output or f"debate_{debate_id}.md"
    with open(output_path, "w", encoding="utf-8") as file:
        file.write(content)

    typer.echo(f"Exportado: {output_path}")


if __name__ == "__main__":
    app()
