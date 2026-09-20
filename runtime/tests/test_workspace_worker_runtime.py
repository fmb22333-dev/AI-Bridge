from fastapi.testclient import TestClient

from ai_bridge.app import build_runtime


def test_build_runtime_registers_workspace_worker(tmp_path):
    app, service, _connection, _runtime_state = build_runtime(data_dir=tmp_path, port=18765)

    descriptor = service.adapter_registry.get("workspace")
    names = {cap.name for cap in descriptor.capabilities}

    assert "workspace.project.inspect" in names
    assert "workspace.directory.list" in names
    assert "workspace.file.create" in names
    assert "workspace.file.patch" in names
    assert "workspace.service.restart" in names
    assert getattr(app.state, "worker_supervisor", None) is not None


def test_runtime_lifespan_closes_worker_supervisor(tmp_path, monkeypatch):
    app, _service, _connection, _runtime_state = build_runtime(data_dir=tmp_path, port=18766)
    calls = []
    monkeypatch.setattr(app.state.worker_supervisor, "close", lambda: calls.append("closed"))

    with TestClient(app):
        pass

    assert calls == ["closed"]
