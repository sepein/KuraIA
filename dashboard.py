import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

try:
    import altair as alt
except ModuleNotFoundError:
    alt = None

import streamlit as st

from team_orchestrator_v2 import AppConfig, OpenCodeTeam


def load_jsonl(path: str) -> List[Dict]:
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


def group_events_by_debate(events: List[Dict]) -> Tuple[Dict[str, List[Dict]], List[str]]:
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


def summarize_debate(debate_id: str, events: List[Dict]) -> Dict[str, object]:
    summary: Dict[str, object] = {
        "debate_id": debate_id,
        "task": "",
        "started_at": "",
        "finished_at": "",
        "status": "running",
        "reason": "",
        "rounds": 0,
        "cost_eur": None,
        "duration_seconds": None,
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
            summary["duration_seconds"] = event.get("duration_seconds")

    return summary


def as_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def filter_summaries_by_date(
    summaries: List[Dict[str, object]],
    start_date: date,
    end_date: date,
) -> List[Dict[str, object]]:
    filtered: List[Dict[str, object]] = []
    for summary in summaries:
        started_at = parse_iso_timestamp(summary.get("started_at"))
        finished_at = parse_iso_timestamp(summary.get("finished_at"))
        point = started_at or finished_at
        if point is None:
            continue
        point_date = point.date()
        if start_date <= point_date <= end_date:
            filtered.append(summary)
    return filtered


def estimate_round_cost_rows(events: List[Dict], config: AppConfig) -> List[Dict[str, object]]:
    context_chars_by_round_role: Dict[Tuple[int, str], int] = {}
    rows: List[Dict[str, object]] = []

    for event in events:
        event_type = str(event.get("event", ""))
        if event_type == "round_started":
            round_num = int(event.get("round_num", 0))
            role = str(event.get("role", ""))
            context_chars_by_round_role[(round_num, role)] = int(as_float(event.get("context_chars")))
            continue
        if event_type != "round_response":
            continue

        round_num = int(event.get("round_num", 0))
        role = str(event.get("role", ""))
        context_chars = context_chars_by_round_role.get((round_num, role), 0)
        response_chars = int(as_float(event.get("response_chars")))
        if response_chars == 0:
            response_chars = len(str(event.get("response", "")))

        input_tokens = context_chars // 4
        output_tokens = response_chars // 4
        cost_usd = (
            input_tokens * config.groq_cost_per_input_token_usd
            + output_tokens * config.groq_cost_per_output_token_usd
        )
        cost_eur = cost_usd * config.eur_per_usd

        rows.append(
            {
                "round_num": round_num,
                "role": role,
                "input_chars": context_chars,
                "output_chars": response_chars,
                "cost_eur": round(cost_eur, 6),
            }
        )

    rows.sort(key=lambda item: (int(item["round_num"]), str(item["role"])))
    return rows


def build_chart_rows(summaries: List[Dict[str, object]], max_points: int = 30) -> List[Dict[str, object]]:
    if not summaries:
        return []

    trimmed = summaries[-max_points:]
    rows: List[Dict[str, object]] = []
    for item in trimmed:
        debate_id = str(item.get("debate_id", ""))
        rows.append(
            {
                "debate_id": debate_id,
                "cost_eur": as_float(item.get("cost_eur")),
                "duration_seconds": as_float(item.get("duration_seconds")),
                "rounds": as_float(item.get("rounds")),
            }
        )
    return rows


def render_metric_charts(summaries: List[Dict[str, object]]) -> None:
    rows = build_chart_rows(summaries, max_points=30)
    if not rows:
        return

    st.markdown("**Graficos (ultimos 30 debates)**")

    if alt is None:
        st.warning("Altair no esta disponible. Mostrando tabla en lugar de graficos.")
        st.dataframe(rows, use_container_width=True)
        return

    col1, col2 = st.columns(2)

    with col1:
        cost_chart = (
            alt.Chart(alt.Data(values=rows))
            .mark_bar()
            .encode(
                x=alt.X("debate_id:N", sort=None, title="Debate"),
                y=alt.Y("cost_eur:Q", title="Coste EUR"),
                tooltip=["debate_id", "cost_eur", "rounds", "duration_seconds"],
            )
            .properties(height=260)
        )
        st.altair_chart(cost_chart, use_container_width=True)

    with col2:
        duration_chart = (
            alt.Chart(alt.Data(values=rows))
            .mark_line(point=True)
            .encode(
                x=alt.X("debate_id:N", sort=None, title="Debate"),
                y=alt.Y("duration_seconds:Q", title="Duracion (s)"),
                tooltip=["debate_id", "duration_seconds", "cost_eur", "rounds"],
            )
            .properties(height=260)
        )
        st.altair_chart(duration_chart, use_container_width=True)

    rounds_chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_area(opacity=0.35)
        .encode(
            x=alt.X("debate_id:N", sort=None, title="Debate"),
            y=alt.Y("rounds:Q", title="Rondas"),
            tooltip=["debate_id", "rounds", "cost_eur", "duration_seconds"],
        )
        .properties(height=220)
    )
    st.altair_chart(rounds_chart, use_container_width=True)


def render_overview(config: AppConfig, summaries: List[Dict[str, object]], events: List[Dict]) -> None:
    st.subheader("Overview")

    if not summaries:
        st.info("No hay debates registrados todavia.")
        return

    total = len(summaries)
    completed = sum(1 for item in summaries if item.get("status") == "completed")
    stopped = sum(1 for item in summaries if item.get("status") == "stopped")
    errors = sum(1 for item in summaries if item.get("status") == "error")
    running = sum(1 for item in summaries if item.get("status") == "running")
    total_cost = sum(as_float(item.get("cost_eur")) for item in summaries)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Debates", total)
    c2.metric("Completed", completed)
    c3.metric("Stopped", stopped)
    c4.metric("Error", errors)
    c5.metric("Running", running)
    c6.metric("Coste EUR", f"{total_cost:.3f}")

    event_counter = Counter(str(evt.get("event", "")) for evt in events)
    st.caption(f"Log file: {config.debate_log_file} | Eventos totales: {len(events)}")

    render_metric_charts(summaries)

    st.markdown("**Eventos por tipo**")
    st.json(dict(sorted(event_counter.items())))

    st.markdown("**Ultimos debates**")
    st.dataframe(list(reversed(summaries)), use_container_width=True)


def render_debate_detail(
    config: AppConfig,
    summaries: List[Dict[str, object]],
    grouped_events: Dict[str, List[Dict]],
) -> None:
    st.subheader("Detalle de debate")

    if not summaries:
        st.info("Sin debates para mostrar.")
        return

    debate_ids = [str(item["debate_id"]) for item in reversed(summaries)]
    selected_debate = st.selectbox("Debate", debate_ids)

    summary = next(item for item in summaries if str(item["debate_id"]) == selected_debate)
    events = grouped_events.get(selected_debate, [])

    st.markdown("**Resumen**")
    st.json(summary)

    round_cost_rows = estimate_round_cost_rows(events, config)
    if round_cost_rows:
        total_round_cost = sum(as_float(item.get("cost_eur")) for item in round_cost_rows)
        st.markdown("**Coste estimado por ronda (aprox)**")
        st.metric("Total estimado por rondas (EUR)", f"{total_round_cost:.4f}")
        st.dataframe(round_cost_rows, use_container_width=True)

        if alt is not None:
            round_cost_chart = (
                alt.Chart(alt.Data(values=round_cost_rows))
                .mark_bar()
                .encode(
                    x=alt.X("round_num:Q", title="Ronda"),
                    y=alt.Y("cost_eur:Q", title="Coste EUR"),
                    color=alt.Color("role:N", title="Rol"),
                    tooltip=["round_num", "role", "input_chars", "output_chars", "cost_eur"],
                )
                .properties(height=260)
            )
            st.altair_chart(round_cost_chart, use_container_width=True)

    st.markdown("**Timeline**")
    for event in events:
        event_type = str(event.get("event", ""))
        ts = str(event.get("ts", ""))

        if event_type == "round_response":
            round_num = event.get("round_num", "?")
            role = event.get("role", "")
            st.markdown(f"- `{ts}` round {round_num} `{role}`")
            with st.expander(f"Respuesta ronda {round_num} - {role}"):
                st.code(str(event.get("response", "")), language="text")
        elif event_type == "chief_action":
            action = event.get("action", "")
            feedback = str(event.get("feedback", "")).strip()
            st.markdown(f"- `{ts}` chief_action `{action}`")
            if feedback:
                st.caption(feedback)
        elif event_type == "parallel_completed":
            st.markdown(f"- `{ts}` parallel_completed")
            results = event.get("results", {})
            if isinstance(results, dict):
                with st.expander("Respuestas paralelas"):
                    for role, response in results.items():
                        st.markdown(f"**{role}**")
                        st.code(str(response), language="text")
        else:
            st.markdown(f"- `{ts}` {event_type}")


def render_interventions(config: AppConfig, summaries: List[Dict[str, object]]) -> None:
    st.subheader("Intervenciones")
    st.caption(f"Queue file: {config.interventions_file}")

    queue_rows = load_jsonl(config.interventions_file)
    if queue_rows:
        st.markdown("**Cola pendiente**")
        st.dataframe(list(reversed(queue_rows)), use_container_width=True)
    else:
        st.info("No hay intervenciones en cola.")

    debate_options = [""] + [str(item["debate_id"]) for item in reversed(summaries)]

    with st.form("queue_intervention"):
        action = st.selectbox("Action", ["feedback", "stop"])
        debate_id = st.selectbox("Debate objetivo (opcional)", debate_options)
        message = st.text_area("Mensaje", value="", height=120)
        submitted = st.form_submit_button("Encolar")

    if submitted:
        if action == "feedback" and not message.strip():
            st.error("Para feedback, el mensaje no puede estar vacio.")
        else:
            payload_message = message.strip() if message.strip() else "STOP solicitado desde dashboard"
            team = OpenCodeTeam(config=config)
            team.queue_intervention(payload_message, debate_id=debate_id or None, action=action)
            st.success("Intervencion encolada.")
            st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="OpenCode Team Dashboard",
        page_icon="O",
        layout="wide",
    )
    st.title("OpenCode Team Orchestrator - Dashboard")

    config = AppConfig()

    with st.sidebar:
        st.header("Config")
        log_path = st.text_input("Debate log file", value=config.debate_log_file)
        queue_path = st.text_input("Interventions file", value=config.interventions_file)
        use_date_filter = st.checkbox("Filtrar por fechas", value=False)
        default_end = date.today()
        default_start = default_end - timedelta(days=30)
        start_date = st.date_input("Desde", value=default_start)
        end_date = st.date_input("Hasta", value=default_end)
        auto_refresh = st.checkbox("Auto refresh", value=False)
        refresh_seconds = st.slider("Refresh cada (segundos)", min_value=3, max_value=120, value=15, step=1)
        refresh = st.button("Recargar")

    if refresh:
        st.rerun()

    events = load_jsonl(log_path)
    grouped, order = group_events_by_debate(events)
    summaries = [summarize_debate(debate_id, grouped[debate_id]) for debate_id in order]

    if use_date_filter:
        if start_date > end_date:
            st.sidebar.error("Rango invalido: 'Desde' debe ser menor o igual que 'Hasta'.")
            summaries = []
            grouped = {}
            events = []
        else:
            filtered_summaries = filter_summaries_by_date(summaries, start_date, end_date)
            allowed_ids = {str(item["debate_id"]) for item in filtered_summaries}
            summaries = filtered_summaries
            grouped = {debate_id: grouped[debate_id] for debate_id in order if debate_id in allowed_ids}
            events = [event for event in events if str(event.get("debate_id", "")) in allowed_ids]

    override_config = AppConfig(
        base_url=config.base_url,
        sessions_file=config.sessions_file,
        max_wait_seconds=config.max_wait_seconds,
        poll_interval_seconds=config.poll_interval_seconds,
        max_rounds_per_debate=config.max_rounds_per_debate,
        max_budget_eur=config.max_budget_eur,
        max_context_chars=config.max_context_chars,
        request_timeout_seconds=config.request_timeout_seconds,
        eur_per_usd=config.eur_per_usd,
        debate_log_file=log_path,
        enable_event_logging=config.enable_event_logging,
        max_log_text_chars=config.max_log_text_chars,
        interventions_file=queue_path,
        groq_cost_per_input_token_usd=config.groq_cost_per_input_token_usd,
        groq_cost_per_output_token_usd=config.groq_cost_per_output_token_usd,
    )

    tab_overview, tab_detail, tab_interventions = st.tabs(["Overview", "Debate", "Interventions"])

    with tab_overview:
        render_overview(override_config, summaries, events)

    with tab_detail:
        render_debate_detail(override_config, summaries, grouped)

    with tab_interventions:
        render_interventions(override_config, summaries)

    if auto_refresh:
        st.caption(f"Auto refresh activo ({refresh_seconds}s)")
        time.sleep(refresh_seconds)
        st.rerun()


if __name__ == "__main__":
    main()
