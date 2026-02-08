import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteDebateMemoryStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path or "api_memory.db")
        self._lock = threading.Lock()
        self._ensure_parent_dir()
        self._init_db()

    def _ensure_parent_dir(self) -> None:
        path = Path(self.db_path)
        parent = path.parent
        if parent and str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS debates (
                    debate_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS debate_events (
                    debate_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (debate_id, idx)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS output_events (
                    debate_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (debate_id, idx)
                )
                """
            )
            conn.commit()

    def clear_all(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM debate_events")
                conn.execute("DELETE FROM output_events")
                conn.execute("DELETE FROM debates")
                conn.commit()

    def upsert_debate(self, debate: Dict[str, object]) -> None:
        debate_id = str(debate.get("debate_id", "")).strip()
        if not debate_id:
            raise ValueError("debate_id es obligatorio para persistir memoria.")
        payload_json = json.dumps(debate, ensure_ascii=False)
        updated_at = _now_iso()

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO debates (debate_id, payload_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(debate_id)
                    DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
                    """,
                    (debate_id, payload_json, updated_at),
                )
                conn.commit()

    def get_debate(self, debate_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT payload_json FROM debates WHERE debate_id = ?",
                    (debate_id,),
                ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def list_debates(self, limit: int = 50) -> List[Dict[str, object]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT payload_json FROM debates ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        debates: List[Dict[str, object]] = []
        for row in rows:
            try:
                item = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                debates.append(item)
        return debates

    def save_events(self, debate_id: str, events: List[Dict[str, object]]) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM debate_events WHERE debate_id = ?", (debate_id,))
                for idx, event in enumerate(events):
                    event_json = json.dumps(event, ensure_ascii=False)
                    conn.execute(
                        """
                        INSERT INTO debate_events (debate_id, idx, event_json)
                        VALUES (?, ?, ?)
                        """,
                        (debate_id, idx, event_json),
                    )
                conn.commit()

    def get_events(self, debate_id: str, limit: int = 5000, reverse: bool = False) -> List[Dict[str, object]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT event_json FROM debate_events WHERE debate_id = ? ORDER BY idx ASC",
                    (debate_id,),
                ).fetchall()

        events: List[Dict[str, object]] = []
        for row in rows:
            try:
                event = json.loads(str(row["event_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        selected = events[-limit:]
        if reverse:
            selected = list(reversed(selected))
        return selected

    def save_output_events(self, debate_id: str, events: List[Dict[str, object]]) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM output_events WHERE debate_id = ?", (debate_id,))
                for idx, event in enumerate(events):
                    event_json = json.dumps(event, ensure_ascii=False)
                    conn.execute(
                        """
                        INSERT INTO output_events (debate_id, idx, event_json)
                        VALUES (?, ?, ?)
                        """,
                        (debate_id, idx, event_json),
                    )
                conn.commit()

    def get_output_events(self, debate_id: str, limit: int = 5000, reverse: bool = False) -> List[Dict[str, object]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT event_json FROM output_events WHERE debate_id = ? ORDER BY idx ASC",
                    (debate_id,),
                ).fetchall()

        events: List[Dict[str, object]] = []
        for row in rows:
            try:
                event = json.loads(str(row["event_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        selected = events[-limit:]
        if reverse:
            selected = list(reversed(selected))
        return selected

    def export_debate(
        self,
        debate_id: str,
        include_events: bool = True,
        include_output_events: bool = True,
    ) -> Optional[Dict[str, object]]:
        debate = self.get_debate(debate_id)
        if not debate:
            return None

        payload: Dict[str, object] = {
            "schema_version": "1.0",
            "exported_at": _now_iso(),
            "debate": debate,
        }
        if include_events:
            payload["events"] = self.get_events(debate_id, limit=100_000, reverse=False)
        if include_output_events:
            payload["output_events"] = self.get_output_events(debate_id, limit=100_000, reverse=False)
        return payload

    def export_many(
        self,
        limit: int = 50,
        include_events: bool = False,
        include_output_events: bool = False,
    ) -> Dict[str, object]:
        debates = self.list_debates(limit=limit)
        snapshots: List[Dict[str, object]] = []
        for debate in debates:
            debate_id = str(debate.get("debate_id", "")).strip()
            if not debate_id:
                continue
            snapshot = {
                "schema_version": "1.0",
                "exported_at": _now_iso(),
                "debate": debate,
            }
            if include_events:
                snapshot["events"] = self.get_events(debate_id, limit=100_000, reverse=False)
            if include_output_events:
                snapshot["output_events"] = self.get_output_events(debate_id, limit=100_000, reverse=False)
            snapshots.append(snapshot)

        return {
            "schema_version": "1.0",
            "exported_at": _now_iso(),
            "count": len(snapshots),
            "items": snapshots,
        }

    def import_snapshot(self, snapshot: Dict[str, object], overwrite: bool = False) -> Dict[str, str]:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot invalido.")

        debate = snapshot.get("debate")
        if not isinstance(debate, dict):
            raise ValueError("snapshot.debate es obligatorio y debe ser objeto.")

        debate_id = str(debate.get("debate_id", "")).strip()
        if not debate_id:
            raise ValueError("snapshot.debate.debate_id es obligatorio.")

        existing = self.get_debate(debate_id)
        if existing and not overwrite:
            return {"debate_id": debate_id, "status": "skipped_exists"}

        events_raw = snapshot.get("events", [])
        events: List[Dict[str, object]] = []
        if isinstance(events_raw, list):
            for item in events_raw:
                if isinstance(item, dict):
                    events.append(item)

        output_events_raw = snapshot.get("output_events", [])
        output_events: List[Dict[str, object]] = []
        if isinstance(output_events_raw, list):
            for item in output_events_raw:
                if isinstance(item, dict):
                    output_events.append(item)

        self.upsert_debate(debate)
        if events:
            self.save_events(debate_id, events)
        if output_events:
            self.save_output_events(debate_id, output_events)
        return {"debate_id": debate_id, "status": "imported"}
