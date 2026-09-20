from __future__ import annotations

from ai_bridge.core.task_idle_notifier import TaskIdleNotifier
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus


def _command(command_id: str, *, adapter: str = "workspace", operation: str = "workspace.health"):
    return CommandEnvelope.model_validate(
        {
            "protocol": "bridge/1",
            "command_id": command_id,
            "workspace": "Bridge",
            "adapter": adapter,
            "operation": operation,
            "arguments": {},
            "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
            "risk": "L1",
        }
    )


def test_task_idle_snapshot_ignores_transport_control_plane(tmp_path):
    db = BridgeDB(tmp_path / "bridge.db")

    ping = _command(
        "cmd-ping",
        adapter="bridge_transport",
        operation="transport.ping",
    )
    db.accept_transport_command(ping)
    db.save_result(
        ExecutionResult(command_id=ping.command_id, status=ExecutionStatus.SUCCESS)
    )
    snapshot = db.task_idle_snapshot()
    assert snapshot["pending_count"] == 0
    assert snapshot["latest_terminal"] is None

    work = _command("cmd-work")
    db.accept_transport_command(work)
    assert db.task_idle_snapshot()["pending_count"] == 1

    db.save_result(
        ExecutionResult(command_id=work.command_id, status=ExecutionStatus.SUCCESS)
    )
    snapshot = db.task_idle_snapshot()
    assert snapshot["pending_count"] == 0
    assert snapshot["latest_terminal"]["command_id"] == "cmd-work"


class FakeDB:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def task_idle_snapshot(self):
        return self.snapshot


def _terminal(command_id="cmd-work", completed_at="1970-01-01 00:00:00"):
    return {
        "pending_count": 0,
        "latest_terminal": {
            "command_id": command_id,
            "operation": "workspace.health",
            "status": "success",
            "updated_at": completed_at,
        },
    }


def test_notifier_waits_five_minutes_and_notifies_once(tmp_path):
    now = [100.0]
    delivered = []

    notifier = TaskIdleNotifier(
        FakeDB(_terminal()),
        state_path=tmp_path / "notifier.json",
        idle_seconds=300,
        clock=lambda: now[0],
        sender=lambda: delivered.append("sent") or True,
    )
    assert notifier.check_once() is False

    now[0] = 301.0
    assert notifier.check_once() is True
    assert delivered == ["sent"]
    assert notifier.check_once() is False
    assert delivered == ["sent"]


def test_notifier_never_finishes_while_any_business_command_is_pending(tmp_path):
    now = [1000.0]
    delivered = []
    db = FakeDB(
        {
            "pending_count": 1,
            "latest_terminal": _terminal()["latest_terminal"],
        }
    )
    notifier = TaskIdleNotifier(
        db,
        state_path=tmp_path / "notifier.json",
        idle_seconds=300,
        clock=lambda: now[0],
        sender=lambda: delivered.append("sent") or True,
    )
    assert notifier.check_once() is False
    assert delivered == []

    db.snapshot = _terminal(
        command_id="cmd-last",
        completed_at="1970-01-01 00:15:00",
    )
    now[0] = 900.0
    assert notifier.check_once() is False
    now[0] = 1201.0
    assert notifier.check_once() is True
    assert delivered == ["sent"]


def test_notifier_suppresses_stale_history_on_runtime_start(tmp_path):
    delivered = []
    notifier = TaskIdleNotifier(
        FakeDB(_terminal()),
        state_path=tmp_path / "notifier.json",
        idle_seconds=300,
        clock=lambda: 1000.0,
        sender=lambda: delivered.append("sent") or True,
    )
    assert notifier.check_once() is False
    assert delivered == []
    assert notifier.check_once() is False
