from __future__ import annotations

from types import SimpleNamespace

from ai_bridge.runtime_version import runtime_version
from ai_bridge.transport import remote_controller as remote_controller_module
from ai_bridge.transport import supabase_extension as _supabase_extension  # noqa: F401
from ai_bridge.transport.remote_controller import RemoteController


class _Secrets:
    def get(self, _key):
        return "token"


class _Sessions:
    def list(self):
        return []


class _Workspaces:
    def list(self):
        return []


def _service():
    return SimpleNamespace(
        sessions=_Sessions(),
        workspaces=_Workspaces(),
        emergency_stop=SimpleNamespace(write_blocked=False),
    )


def test_runtime_version_reads_pyproject_authority(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ai-bridge"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    assert runtime_version(tmp_path) == "9.8.7"


def test_supabase_presence_preserves_dynamic_runtime_version(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_controller_module, "runtime_version", lambda: "9.8.7")
    controller = RemoteController(
        service=_service(),
        data_dir=tmp_path,
        runtime_state={},
        secret_store=_Secrets(),
    )
    presence = controller._presence_core("bridge-test", None)

    assert presence["bridge_version"] == "9.8.7"
    assert presence["realtime_command_primary"] == "supabase"
    assert presence["realtime_command_capabilities"]["workspace_lane_scope"] == "workspace_id"
    assert presence["realtime_command_capabilities"]["same_workspace_serial"] is True
