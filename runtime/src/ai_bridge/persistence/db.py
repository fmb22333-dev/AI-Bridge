import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


class CommandIdentityConflict(RuntimeError):
    pass


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

    @staticmethod
    def _encode_command(command) -> str:
        payload = command.model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode_command_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if data.get("request_json"):
            data["request"] = json.loads(data.pop("request_json"))
        if data.get("result_json"):
            data["result"] = json.loads(data.pop("result_json"))
        else:
            data.pop("result_json", None)
        return data

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transport_ingress_receipts (
                    transport_key TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    ingress_kind TEXT NOT NULL,
                    routing_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    published_at TEXT,
                    PRIMARY KEY (transport_key, receipt_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transport_ingress_command
                ON transport_ingress_receipts (transport_key, command_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transport_contents_indexes (
                    transport_key TEXT PRIMARY KEY,
                    index_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get_transport_contents_index(self, transport_key: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT index_json FROM transport_contents_indexes WHERE transport_key=?",
                (transport_key,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["index_json"]))
        if not isinstance(payload, dict):
            return None
        return {str(name): str(sha) for name, sha in payload.items()}

    def set_transport_contents_index(self, transport_key: str, index: dict[str, str]) -> None:
        encoded = json.dumps(
            {str(name): str(sha) for name, sha in index.items()},
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO transport_contents_indexes (transport_key, index_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(transport_key) DO UPDATE SET
                    index_json=excluded.index_json, updated_at=CURRENT_TIMESTAMP""",
                (transport_key, encoded),
            )

    def command_exists(self, command_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, result_json FROM commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if row is None:
                return False
            return not (row["status"] == "transport_accepted" and row["result_json"] is None)

    def accept_transport_command(self, command) -> dict[str, Any]:
        encoded = self._encode_command(command)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM commands WHERE command_id=?",
                (command.command_id,),
            ).fetchone()
            if row is None:
                try:
                    conn.execute(
                        """INSERT INTO commands
                        (command_id, workspace_id, adapter, session_id, operation, request_json, status)
                        VALUES (?, ?, ?, ?, ?, ?, 'transport_accepted')""",
                        (
                            command.command_id,
                            command.workspace,
                            command.adapter,
                            command.session,
                            command.operation,
                            encoded,
                        ),
                    )
                except sqlite3.IntegrityError:
                    pass
                row = conn.execute(
                    "SELECT * FROM commands WHERE command_id=?",
                    (command.command_id,),
                ).fetchone()
            if row is None:
                raise RuntimeError("transport command acceptance was not persisted")
            if str(row["request_json"]) != encoded:
                raise CommandIdentityConflict(command.command_id)
            return self._decode_command_row(row)

    def claim_transport_command(self, command) -> str:
        encoded = self._encode_command(command)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT request_json, status, result_json FROM commands WHERE command_id=?",
                (command.command_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO commands
                    (command_id, workspace_id, adapter, session_id, operation, request_json, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'received')""",
                    (
                        command.command_id,
                        command.workspace,
                        command.adapter,
                        command.session,
                        command.operation,
                        encoded,
                    ),
                )
                return "claimed"
            if str(row["request_json"]) != encoded:
                raise CommandIdentityConflict(command.command_id)
            if row["result_json"] is not None:
                return "terminal"
            if str(row["status"] or "") != "transport_accepted":
                return "already_claimed"
            cursor = conn.execute(
                """UPDATE commands
                SET status='received', updated_at=CURRENT_TIMESTAMP
                WHERE command_id=? AND status='transport_accepted' AND result_json IS NULL""",
                (command.command_id,),
            )
            if cursor.rowcount == 1:
                return "claimed"
            row = conn.execute(
                "SELECT request_json, status, result_json FROM commands WHERE command_id=?",
                (command.command_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("transport command disappeared during claim")
            if str(row["request_json"]) != encoded:
                raise CommandIdentityConflict(command.command_id)
            return "terminal" if row["result_json"] is not None else "already_claimed"

    def insert_command(self, command, *, status: str = "received") -> None:
        encoded = self._encode_command(command)
        with self._lock, self._connect() as conn:
            try:
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
                        encoded,
                        status,
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT request_json, status, result_json FROM commands WHERE command_id=?",
                    (command.command_id,),
                ).fetchone()
                if row is not None and str(row["request_json"]) != encoded:
                    raise CommandIdentityConflict(command.command_id)
                if (
                    row is not None
                    and row["status"] == "transport_accepted"
                    and row["result_json"] is None
                    and status == "received"
                ):
                    cursor = conn.execute(
                        """UPDATE commands
                        SET status='received', updated_at=CURRENT_TIMESTAMP
                        WHERE command_id=? AND status='transport_accepted' AND result_json IS NULL""",
                        (command.command_id,),
                    )
                    if cursor.rowcount == 1:
                        return
                raise

    def record_ingress_receipt(
        self,
        transport_key: str,
        command_id: str,
        receipt_id: str,
        ingress_kind: str,
        routing: dict[str, Any] | None = None,
    ) -> None:
        routing_json = json.dumps(routing or {}, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT command_id, ingress_kind FROM transport_ingress_receipts
                WHERE transport_key=? AND receipt_id=?""",
                (transport_key, receipt_id),
            ).fetchone()
            if row is not None:
                if str(row["command_id"]) != str(command_id):
                    raise CommandIdentityConflict(command_id)
                if str(row["ingress_kind"]) != str(ingress_kind):
                    raise RuntimeError("INGRESS_RECEIPT_KIND_CONFLICT")
                conn.execute(
                    """UPDATE transport_ingress_receipts
                    SET routing_json=?, observed_at=CURRENT_TIMESTAMP
                    WHERE transport_key=? AND receipt_id=?""",
                    (routing_json, transport_key, receipt_id),
                )
                return
            conn.execute(
                """INSERT INTO transport_ingress_receipts
                (transport_key, receipt_id, command_id, ingress_kind, routing_json)
                VALUES (?, ?, ?, ?, ?)""",
                (transport_key, receipt_id, command_id, ingress_kind, routing_json),
            )

    def list_ingress_receipts(self, transport_key: str, command_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT transport_key, receipt_id, command_id, ingress_kind,
                          routing_json, observed_at, published_at
                FROM transport_ingress_receipts
                WHERE transport_key=? AND command_id=?
                ORDER BY ingress_kind ASC, receipt_id ASC""",
                (transport_key, command_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["routing"] = json.loads(item.pop("routing_json") or "{}")
            result.append(item)
        return result

    def mark_ingress_receipt_published(self, transport_key: str, receipt_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE transport_ingress_receipts
                SET published_at=CURRENT_TIMESTAMP
                WHERE transport_key=? AND receipt_id=?""",
                (transport_key, receipt_id),
            )

    def list_pending_terminal_receipts(
        self,
        transport_key: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.transport_key, r.receipt_id, r.command_id, r.ingress_kind,
                          r.routing_json, r.observed_at, r.published_at,
                          c.request_json, c.result_json
                FROM transport_ingress_receipts AS r
                JOIN commands AS c ON c.command_id = r.command_id
                WHERE r.transport_key=?
                  AND r.published_at IS NULL
                  AND c.result_json IS NOT NULL
                ORDER BY r.observed_at ASC, r.receipt_id ASC
                LIMIT ?""",
                (transport_key, limit),
            ).fetchall()
        pending: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["routing"] = json.loads(item.pop("routing_json") or "{}")
            item["request"] = json.loads(item.pop("request_json"))
            item["result"] = json.loads(item.pop("result_json"))
            pending.append(item)
        return pending

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
        """Return recent command rows with decoded request/result payloads."""
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM commands ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            records.append(self._decode_command_row(row))
        return records

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if row is None:
                return None
            return self._decode_command_row(row)