import unittest
from datetime import date

from dashboard import (
    build_chart_rows,
    filter_summaries_by_date,
    group_events_by_debate,
    parse_iso_timestamp,
    summarize_debate,
    estimate_round_cost_rows,
)
from team_orchestrator_v2 import AppConfig


class DashboardUtilsTests(unittest.TestCase):
    def test_parse_iso_timestamp_accepts_z_suffix(self):
        ts = parse_iso_timestamp("2026-02-08T12:00:00Z")
        self.assertIsNotNone(ts)
        self.assertEqual(ts.year, 2026)

    def test_parse_iso_timestamp_invalid_returns_none(self):
        self.assertIsNone(parse_iso_timestamp("nope"))
        self.assertIsNone(parse_iso_timestamp(None))

    def test_group_events_by_debate(self):
        events = [
            {"debate_id": "d1", "event": "debate_started"},
            {"debate_id": "d2", "event": "debate_started"},
            {"debate_id": "d1", "event": "round_response"},
            {"event": "without_id"},
        ]
        grouped, order = group_events_by_debate(events)
        self.assertEqual(order, ["d1", "d2"])
        self.assertEqual(len(grouped["d1"]), 2)
        self.assertEqual(len(grouped["d2"]), 1)

    def test_summarize_debate(self):
        events = [
            {"event": "debate_started", "ts": "2026-02-08T10:00:00+00:00", "task": "T1"},
            {"event": "round_response"},
            {"event": "round_response"},
            {
                "event": "debate_finished",
                "status": "completed",
                "reason": "",
                "ts": "2026-02-08T10:10:00+00:00",
                "cost_eur": 0.12,
                "duration_seconds": 123,
            },
        ]
        summary = summarize_debate("d1", events)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["rounds"], 2)
        self.assertEqual(summary["cost_eur"], 0.12)
        self.assertEqual(summary["duration_seconds"], 123)

    def test_filter_summaries_by_date(self):
        summaries = [
            {"debate_id": "d1", "started_at": "2026-02-01T10:00:00+00:00", "finished_at": ""},
            {"debate_id": "d2", "started_at": "2026-03-01T10:00:00+00:00", "finished_at": ""},
        ]
        filtered = filter_summaries_by_date(summaries, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["debate_id"], "d1")

    def test_estimate_round_cost_rows(self):
        cfg = AppConfig(enable_event_logging=False)
        events = [
            {"event": "round_started", "round_num": 0, "role": "Arquitecto", "context_chars": 400},
            {"event": "round_response", "round_num": 0, "role": "Arquitecto", "response_chars": 800},
        ]
        rows = estimate_round_cost_rows(events, cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["round_num"], 0)
        self.assertEqual(rows[0]["role"], "Arquitecto")
        self.assertGreater(rows[0]["cost_eur"], 0)

    def test_build_chart_rows_limits_points(self):
        summaries = [{"debate_id": f"d{i}", "cost_eur": 0.1, "duration_seconds": 10, "rounds": 2} for i in range(40)]
        rows = build_chart_rows(summaries, max_points=30)
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[0]["debate_id"], "d10")


if __name__ == "__main__":
    unittest.main()
