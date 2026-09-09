from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.adapters import bridge_admin
from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor, descriptor
from ai_bridge.deployment import supervisor_upgrade_worker as worker


def _seed_runtime(root: Path, version: str = "0.2.6.48"):
    current = root / "Runtime" / "Current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "src").mkdir(exist_ok=True)
    (current / "pyproject.toml").write_text(
        f'[project]\nname="ai-bridge"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    return current


def _seed_supervisor(root: Path, version: str = "0.1.2"):
    system = root / "_System"
    system.mkdir(parents=True, exist_ok=True)
    source = f'SUPERVISOR_VERSION = "{version}"\n'
    (system / "supervisor.py").write_text(source, encoding="utf-8")
    (system / "VERSION.txt").write_text(
        f"AI Bridge Supervisor {version}\n",
        encoding="utf-8",
    )
    return source


def _release_docs(target_version: str = "0.1.3"):
    supervisor_text = (
        f'SUPERVISOR_VERSION = "{target_version}"\n'
        'SUPERVISOR_UPDATE_PROTOCOL = "runtime_detached_worker_v1"\n'
    )
    version_text = f"AI Bridge Supervisor {target_version}\n"
    manifest = {
        "schema_version": "1.0",
        "version": target_version,
        "min_runtime_version": "0.2.6.48",
        "files": [
            {
                "target": "_System/supervisor.py",
                "source_path": f"bootstrap/supervisor/{target_version}/_System/supervisor.py",
                "github_sha": "git-supervisor",
            },
            {
                "target": "_System/VERSION.txt",
                "source_path": f"bootstrap/supervisor/{target_version}/_System/VERSION.txt",
                "github_sha": "git-version",
            },
        ],
    }
    return manifest, supervisor_text, version_text


def test_bridge_admin_exposes_supervisor_upgrade_capabilities():
    by_name = {cap.name: cap for cap in descriptor().capabilities}
    assert by_name["bridge.supervisor.status"].write is False
    assert str(by_name["bridge.supervisor.status"].risk).endswith("L1")
    assert by_name["bridge.supervisor.upgrade"].write is True
    assert str(by_name["bridge.supervisor.upgrade"].risk).endswith("L2")
    assert by_name["bridge.supervisor.upgrade"].rollback is True
    assert by_name["bridge.supervisor.upgrade"].manages_checkpoint is True


def test_runtime_stages_verified_supervisor_release_and_schedules_worker(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    _seed_runtime(root)
    _seed_supervisor(root)
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")

    manifest, supervisor_text, version_text = _release_docs()
    docs = {
        "supervisor-release.json": {
            "text": json.dumps(manifest),
            "sha": "manifest-sha",
        },
        "bootstrap/supervisor/0.1.3/_System/supervisor.py": {
            "text": supervisor_text,
            "sha": "git-supervisor",
        },
        "bootstrap/supervisor/0.1.3/_System/VERSION.txt": {
            "text": version_text,
            "sha": "git-version",
        },
    }

    monkeypatch.setattr(
        executor,
        "_update_source",
        lambda: {"repository": "owner/repo", "branch": "main"},
    )
    monkeypatch.setattr(
        executor,
        "_fetch_repo_text",
        lambda repository, ref, path, timeout_seconds=8.0: docs[path],
    )
    monkeypatch.setattr(
        bridge_admin.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=4321),
    )

    result = executor._stage_supervisor_upgrade(
        {"expected_current_version": "0.1.2", "delay_seconds": 2}
    )
    assert result["status"] == "scheduled"
    assert result["target_version"] == "0.1.3"
    assert result["current_version"] == "0.1.2"
    assert result["worker_pid"] == 4321

    request = json.loads(Path(result["request_path"]).read_text(encoding="utf-8"))
    assert request["target_version"] == "0.1.3"
    assert request["runtime_version"] == "0.2.6.48"
    assert len(request["files"]) == 2
    staged = Path(request["files"][0]["staged"])
    assert staged.read_text(encoding="utf-8") == supervisor_text
    assert request["files"][0]["sha256"] == hashlib.sha256(
        supervisor_text.encode("utf-8")
    ).hexdigest()


def test_runtime_rejects_supervisor_release_outside_version_authority(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    _seed_runtime(root)
    _seed_supervisor(root)
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")

    manifest, _, _ = _release_docs()
    manifest["files"][0]["source_path"] = "bootstrap/supervisor/0.1.2/_System/supervisor.py"
    monkeypatch.setattr(
        executor,
        "_update_source",
        lambda: {"repository": "owner/repo", "branch": "main"},
    )
    monkeypatch.setattr(
        executor,
        "_fetch_repo_text",
        lambda repository, ref, path, timeout_seconds=8.0: {
            "text": json.dumps(manifest),
            "sha": "manifest",
        },
    )

    with pytest.raises(RuntimeError, match="outside version authority"):
        executor._stage_supervisor_upgrade({})


def _worker_file(tmp_path: Path, *, old: str = "old", new: str = "new"):
    target = tmp_path / "_System" / "supervisor.py"
    staged = tmp_path / "upgrade" / "supervisor.py"
    backup = tmp_path / "backup" / "supervisor.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(old, encoding="utf-8")
    backup.write_text(old, encoding="utf-8")
    staged.write_text(new, encoding="utf-8")
    return {
        "target": str(target),
        "staged": str(staged),
        "backup": str(backup),
        "target_existed": True,
        "sha256": hashlib.sha256(new.encode("utf-8")).hexdigest(),
    }


def test_worker_successfully_replaces_and_verifies_supervisor(tmp_path):
    root = tmp_path / "AI_Bridge"
    data = tmp_path / "data"
    item = _worker_file(root)
    result_path = data / "result.json"

    result = worker.perform(
        {
            "root": str(root),
            "data_dir": str(data),
            "recovery_id": "r1",
            "current_version": "0.1.2",
            "target_version": "0.1.3",
            "files": [item],
            "result_path": str(result_path),
        },
        launch_supervisor=lambda root: 777,
        wait_for_exit=lambda pid_file, timeout: None,
        wait_for_version=lambda status_file, version, timeout: {
            "supervisor_version": version,
            "state": "running",
        },
    )
    assert result["status"] == "upgraded"
    assert Path(item["target"]).read_text(encoding="utf-8") == "new"
    assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "upgraded"


def test_worker_rolls_back_when_target_health_fails(tmp_path):
    root = tmp_path / "AI_Bridge"
    data = tmp_path / "data"
    item = _worker_file(root)
    calls = {"launch": 0}

    def launch(root):
        calls["launch"] += 1
        return 800 + calls["launch"]

    def wait_version(status_file, version, timeout):
        if version == "0.1.3":
            raise worker.SupervisorUpgradeError("synthetic target health failure")
        return {"supervisor_version": "0.1.2", "state": "running"}

    result = worker.perform(
        {
            "root": str(root),
            "data_dir": str(data),
            "recovery_id": "r2",
            "current_version": "0.1.2",
            "target_version": "0.1.3",
            "files": [item],
            "result_path": str(data / "result.json"),
        },
        launch_supervisor=launch,
        wait_for_exit=lambda pid_file, timeout: None,
        wait_for_version=wait_version,
    )
    assert result["status"] == "rolled_back"
    assert result["rollback_performed"] is True
    assert Path(item["target"]).read_text(encoding="utf-8") == "old"


def test_worker_windows_first_generation_relaunches_before_perform(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(
        json.dumps(
            {
                "root": str(tmp_path / "AI_Bridge"),
                "data_dir": str(tmp_path / "data"),
                "recovery_id": "tree-fix",
                "current_version": "0.1.2",
                "target_version": "0.1.4",
                "files": [{"placeholder": True}],
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )

    calls = {"spawn": 0, "perform": 0}

    def fake_spawn(path):
        assert Path(path) == request_path
        calls["spawn"] += 1
        return 4455

    def fake_perform(payload):
        calls["perform"] += 1
        return {}

    monkeypatch.setattr(worker.os, "name", "nt")
    monkeypatch.delenv(worker.DETACHED_WORKER_ENV, raising=False)
    monkeypatch.setattr(worker, "_spawn_detached_generation", fake_spawn)
    monkeypatch.setattr(worker, "perform", fake_perform)
    monkeypatch.setattr(sys, "argv", ["supervisor_upgrade_worker", "--request", str(request_path)])

    assert worker.main() == 0
    assert calls == {"spawn": 1, "perform": 0}


def test_worker_windows_detached_generation_runs_perform(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    request = {
        "root": str(tmp_path / "AI_Bridge"),
        "data_dir": str(tmp_path / "data"),
        "recovery_id": "tree-fix-child",
        "current_version": "0.1.2",
        "target_version": "0.1.4",
        "files": [{"placeholder": True}],
        "result_path": str(tmp_path / "result.json"),
    }
    request_path.write_text(json.dumps(request), encoding="utf-8")
    captured = {}

    monkeypatch.setattr(worker.os, "name", "nt")
    monkeypatch.setenv(worker.DETACHED_WORKER_ENV, "1")
    monkeypatch.setattr(worker, "perform", lambda payload: captured.setdefault("request", payload))
    monkeypatch.setattr(sys, "argv", ["supervisor_upgrade_worker", "--request", str(request_path)])

    assert worker.main() == 0
    assert captured["request"]["recovery_id"] == "tree-fix-child"
