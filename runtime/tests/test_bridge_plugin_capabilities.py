from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor, descriptor
from ai_bridge.protocol.command import CommandEnvelope


def _command(operation: str, arguments: dict | None = None):
    return CommandEnvelope.model_validate({
        "command_id": "plugin-cap-test-" + operation.replace(".", "-"),
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": operation,
        "arguments": arguments or {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })


def test_bridge_admin_exposes_plugin_restart_apply_as_l2_write():
    cap = next(
        item for item in descriptor().capabilities
        if item.name == "bridge.plugin.restart_apply"
    )
    assert cap.write is True
    assert str(cap.risk).endswith("L2")
    assert cap.manages_checkpoint is True


def test_bridge_plugin_status_uses_live_core_provider(tmp_path):
    executor = BridgeAdminExecutor(
        data_dir=tmp_path,
        plugin_status_provider=lambda: {"hosts": [{"id": "houdini", "live_sessions": [{"session_id": "HOU-LIVE"}]}]},
    )

    result = executor.execute(_command("bridge.plugin.status"))

    assert result.status.value == "success"
    assert result.result["hosts"][0]["live_sessions"][0]["session_id"] == "HOU-LIVE"


def test_bridge_plugin_restart_apply_calls_core_handler(tmp_path):
    seen = []
    executor = BridgeAdminExecutor(
        data_dir=tmp_path,
        plugin_restart_handler=lambda args: seen.append(dict(args)) or {"status": "applied"},
    )

    result = executor.execute(_command(
        "bridge.plugin.restart_apply",
        {
            "host_id": "houdini",
            "session_id": "HOU-LIVE",
            "workspace": "Bridge",
            "force_restart": True,
        },
    ))

    assert result.status.value == "success"
    assert result.result["status"] == "applied"
    assert seen == [{
        "host_id": "houdini",
        "session_id": "HOU-LIVE",
        "workspace": "Bridge",
        "force_restart": True,
    }]


def test_runtime_wires_plugin_capabilities_to_bridge_service():
    text = (SRC / "ai_bridge" / "app.py").read_text(encoding="utf-8")
    assert "plugin_status_provider=_plugin_status" in text
    assert "plugin_restart_handler=_plugin_restart" in text
    assert "service.restart_host_for_plugin_update" in text


def test_bridge_admin_exposes_bootstrap_inventory_as_read_only_l1():
    cap = next(item for item in descriptor().capabilities if item.name == "bridge.bootstrap.inventory")
    assert cap.write is False
    assert str(cap.risk).endswith("L1")


def test_bootstrap_inventory_is_allowlisted_and_excludes_runtime_secrets(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    system = root / "_System"
    system.mkdir(parents=True)
    (system / "supervisor.py").write_text("SUPERVISOR_VERSION = '0.1.2'\n", encoding="utf-8")
    (system / "helper.json").write_text('{"ok": true}\n', encoding="utf-8")
    (system / "supervisor_before_0.1.2.py").write_text("old", encoding="utf-8")
    (system / "github_token.txt").write_text("do-not-export", encoding="utf-8")
    (system / ".venv" / "Lib").mkdir(parents=True)
    (system / ".venv" / "Lib" / "ignored.py").write_text("bad", encoding="utf-8")
    (root / "AI_Bridge.bat").write_text("@echo off\n", encoding="utf-8")
    (root / "random_user_file.txt").write_text("not bootstrap", encoding="utf-8")

    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    result = executor.execute(_command("bridge.bootstrap.inventory", {"include_text": True}))
    assert result.status.value == "success"

    by_path = {item["path"]: item for item in result.result["files"]}
    assert "_System/supervisor.py" in by_path
    assert by_path["_System/supervisor.py"]["text"].startswith("SUPERVISOR_VERSION")
    assert "_System/helper.json" in by_path
    assert "_System/supervisor_before_0.1.2.py" not in by_path
    assert "AI_Bridge.bat" in by_path
    assert "_System/github_token.txt" not in by_path
    assert "_System/.venv/Lib/ignored.py" not in by_path
    assert "random_user_file.txt" not in by_path


def test_bootstrap_inventory_can_explicitly_include_backups(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    system = root / "_System"
    system.mkdir(parents=True)
    (system / "supervisor.py").write_text("current", encoding="utf-8")
    (system / "supervisor_before_0.1.2.py").write_text("old", encoding="utf-8")
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    result = executor.execute(_command(
        "bridge.bootstrap.inventory",
        {"include_text": True, "include_backups": True},
    ))
    by_path = {item["path"]: item for item in result.result["files"]}
    assert "_System/supervisor.py" in by_path
    assert "_System/supervisor_before_0.1.2.py" in by_path


def test_bridge_plugin_restart_apply_propagates_failed_handler_status(tmp_path):
    executor = BridgeAdminExecutor(
        data_dir=tmp_path,
        plugin_restart_handler=lambda args: {'status':'failed','reason':'CHECKPOINT_FAILED'},
    )
    result = executor.execute(_command(
        'bridge.plugin.restart_apply',
        {'host_id':'houdini','session_id':'HOU-LIVE','workspace':'Bridge'},
    ))
    assert result.status.value == 'failed'
    assert result.failure.code == 'PLUGIN_RESTART_NOT_APPLIED'
    assert result.result['status'] == 'failed'
    assert result.result['reason'] == 'CHECKPOINT_FAILED'


def test_bridge_force_recover_propagates_failed_handler_status(tmp_path):
    executor = BridgeAdminExecutor(
        data_dir=tmp_path,
        host_recover_handler=lambda args: {'status':'failed','reason':'HOST_EXECUTABLE_UNAVAILABLE'},
    )
    result = executor.execute(_command(
        'bridge.host.force_recover',
        {'host_id':'houdini','pid':123,'project_file':'E:/x.hip','workspace':'Bridge'},
    ))
    assert result.status.value == 'failed'
    assert result.failure.code == 'HOST_RECOVERY_NOT_COMPLETED'
    assert result.result['status'] == 'failed'


def test_bridge_force_recover_accepts_verified_success_status(tmp_path):
    executor = BridgeAdminExecutor(
        data_dir=tmp_path,
        host_recover_handler=lambda args: {'status':'restarted_checkpoint_safe','new_session_id':'HOU-NEW'},
    )
    result = executor.execute(_command(
        'bridge.host.force_recover',
        {'host_id':'houdini','pid':123,'project_file':'E:/x.hip','workspace':'Bridge'},
    ))
    assert result.status.value == 'success'
    assert result.result['status'] == 'restarted_checkpoint_safe'
