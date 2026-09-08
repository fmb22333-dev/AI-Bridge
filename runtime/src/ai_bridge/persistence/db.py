import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


class BridgeDB:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    session_id TEXT,
                    operation TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    status TEXT NOT NULL,
                    evidence_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transport_publications (
                    transport_key TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (transport_key, command_id)
                )
                """
            )

    def command_exists(self, command_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM commands WHERE command_id=?", (command_id,)).fetchone()
            return row is not None

    def insert_command(self, command, *, status: str = "received") -> None:
        payload = command.model_dump(mode="json")
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO commands
                (command_id, workspace_id, adapter, session_id, operation, request_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    command.command_id,
                    command.workspace,
                    command.adapter,
                    command.session,
                    command.operation,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    status,
                ),
            )

    def save_result(self, result) -> None:
        payload = result.model_dump(mode="json")
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE commands
                SET result_json=?, status=?, evidence_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE command_id=?""",
                (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    result.status.value,
                    result.evidence_id,
                    result.command_id,
                ),
            )

    def is_published(self, transport_key: str, command_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM transport_publications WHERE transport_key=? AND command_id=?",
                (transport_key, command_id),
            ).fetchone()
            return row is not None

    def mark_published(self, transport_key: str, command_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO transport_publications (transport_key, command_id) VALUES (?, ?)",
                (transport_key, command_id),
            )

    def list_commands(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT command_id, workspace_id, adapter, session_id, operation, status, evidence_id, created_at, updated_at FROM commands ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_command_records(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM commands ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            if data.get("request_json"):
                data["request"] = json.loads(data.pop("request_json"))
            if data.get("result_json"):
                data["result"] = json.loads(data.pop("result_json"))
            else:
                data.pop("result_json", None)
            records.append(data)
        return records

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if row is None:
                return None
            data = dict(row)
            if data.get("request_json"):
                data["request"] = json.loads(data.pop("request_json"))
            if data.get("result_json"):
                data["result"] = json.loads(data.pop("result_json"))
            else:
                data.pop("result_json", None)
            return data
