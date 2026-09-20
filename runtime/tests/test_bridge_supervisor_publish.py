from __future__ import annotations

import json
from pathlib import Path

from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor, descriptor


def _seed(root: Path):
    system = root / "_System"
    system.mkdir(parents=True)
    (system / "supervisor.py").write_text(
        'SUPERVISOR_VERSION = "0.1.5"\nSUPERVISOR_UPDATE_PROTOCOL = "runtime_detached_worker_v1"\n',
        encoding="utf-8",
    )
    (system / "VERSION.txt").write_text("stale display 0.1.4\n", encoding="utf-8")
    current = root / "Runtime" / "Current"
    current.mkdir(parents=True)
    (current / "pyproject.toml").write_text('[project]\nversion = "0.2.6.70"\n', encoding="utf-8")


def test_bridge_admin_exposes_supervisor_publish_capability():
    cap = {item.name: item for item in descriptor().capabilities}["bridge.supervisor.publish"]
    assert cap.write is True
    assert cap.host_mutation is False
    assert str(cap.risk).endswith("L2")


def test_supervisor_publish_writes_versioned_sources_then_manifest(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    _seed(root)
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    monkeypatch.setattr(executor, "_update_source", lambda: {"repository": "owner/repo", "branch": "bridge-runtime"})
    monkeypatch.setattr(executor, "_token", lambda: "token")

    writes = {}
    class _Client:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr("ai_bridge.adapters.bridge_admin.httpx.Client", lambda *a, **k: _Client())
    def fake_put(client, repository, branch, path, raw, token, message):
        writes[path] = raw.decode("utf-8")
        return "commit-" + path.replace("/", "-")
    monkeypatch.setattr(executor, "_put", fake_put)

    def fake_fetch(repository, ref, path, timeout_seconds=8.0):
        if path == "supervisor-release.json" and path not in writes:
            return {"text": json.dumps({"version": "0.1.4"}), "sha": "old-manifest"}
        assert path in writes
        sha = "blob-supervisor" if path.endswith("supervisor.py") else "blob-version" if path.endswith("VERSION.txt") else "blob-manifest"
        return {"text": writes[path], "sha": sha}
    monkeypatch.setattr(executor, "_fetch_repo_text", fake_fetch)

    result = executor._publish_supervisor_release(
        notes="watchdog release", min_runtime_version="0.2.6.69"
    )

    assert result["published"] is True
    assert result["version"] == "0.1.5"
    source_prefix = "bootstrap/supervisor/0.1.5/_System/"
    assert source_prefix + "supervisor.py" in writes
    assert writes[source_prefix + "VERSION.txt"].startswith("AI Bridge Supervisor 0.1.5\n")
    manifest = json.loads(writes["supervisor-release.json"])
    assert manifest["version"] == "0.1.5"
    assert manifest["min_runtime_version"] == "0.2.6.69"
    assert manifest["upgrade_protocol"] == "runtime_detached_worker_v1"
    assert manifest["files"][0]["github_sha"] == "blob-supervisor"
    assert manifest["files"][1]["github_sha"] == "blob-version"


def test_supervisor_publish_refuses_release_downgrade(tmp_path, monkeypatch):
    root = tmp_path / "AI_Bridge"
    _seed(root)
    monkeypatch.setenv("AI_BRIDGE_ROOT", str(root))
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    monkeypatch.setattr(executor, "_update_source", lambda: {"repository": "owner/repo", "branch": "bridge-runtime"})
    monkeypatch.setattr(executor, "_token", lambda: "token")
    monkeypatch.setattr(
        executor, "_fetch_repo_text",
        lambda repository, ref, path, timeout_seconds=8.0: {"text": json.dumps({"version": "0.1.6"}), "sha": "newer"},
    )
    try:
        executor._publish_supervisor_release(notes="nope", min_runtime_version="0.2.6.69")
    except RuntimeError as exc:
        assert "older than published" in str(exc)
    else:
        raise AssertionError("expected downgrade rejection")
