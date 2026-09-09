from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor, descriptor
from ai_bridge.core.service import BridgeService
from ai_bridge.core.sessions import SessionInfo
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus, FailureInfo, FailureOrigin


PROJECT_A_HIP = "E:/Generic/ProjectA.hip"
PROJECT_B_HIP = "E:/Generic/ProjectB.hip"


def _index():
    return {
        "projects": {
            "project_a": {
                "display_name": "ProjectA",
                "current_hip": PROJECT_A_HIP,
                "host": "Houdini 21.0.440",
                "authority": {"ref": "main", "path": "PROJECT_A_CURRENT_STATE.md"},
                "recovery_rule": "trust current state",
            },
            "project_b": {
                "display_name": "ProjectB",
                "current_hip": PROJECT_B_HIP,
                "host": "Houdini 21.0.440",
                "authority": {"ref": "main", "path": "PROJECT_B_CURRENT_STATE.md"},
                "recovery_rule": "trust current state",
            },
        }
    }


def _service(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    workspaces = WorkspaceRegistry()
    workspaces.register("Bridge", root)
    return BridgeService(db=BridgeDB(tmp_path / "bridge.db"), workspaces=workspaces)


def _session(service, session_id, project_file, *, pid=100, adapter_version="0.5.18", stale=False):
    now = datetime.now(timezone.utc)
    seen = now - timedelta(seconds=60) if stale else now
    service.sessions.register(SessionInfo(
        session_id=session_id,
        adapter="houdini",
        adapter_version=adapter_version,
        host_version="21.0.440",
        pid=pid,
        project_file=project_file,
        registered_at=seen.isoformat(),
        last_seen_at=seen.isoformat(),
    ), preserve_timestamps=True)


def _command(command_id, project_file=PROJECT_A_HIP, *, operation="inspect.node"):
    return CommandEnvelope.model_validate({
        "command_id": command_id,
        "workspace": "Bridge",
        "adapter": "houdini",
        "session": "HOU-A",
        "project_file": project_file,
        "operation": operation,
        "arguments": {},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })


def _terminal(service, command_id, status="success", *, project_file=PROJECT_A_HIP, failure_code=None):
    command = _command(command_id, project_file)
    service.db.insert_command(command)
    failure = None
    if failure_code:
        failure = FailureInfo(origin=FailureOrigin.HOST, code=failure_code)
    result = ExecutionResult(
        command_id=command_id,
        status=ExecutionStatus(status),
        failure=failure,
        evidence_id="ev_" + command_id,
    )
    service.db.save_result(result)


def _resume_command(arguments=None):
    return CommandEnvelope.model_validate({
        "command_id": "resume-test",
        "workspace": "Bridge",
        "adapter": "bridge_admin",
        "operation": "bridge.project.resume",
        "arguments": arguments or {"project": "project_a"},
        "execution": {"verify": True, "checkpoint": "none", "dry_run": False},
        "risk": "L1",
    })


def _executor(tmp_path, service, monkeypatch, *, authority_text="# Authority\nNext Action: continue"):
    executor = BridgeAdminExecutor(
        data_dir=tmp_path / "data",
        project_resume_handler=lambda index, project, limit: service.project_resume_local(
            index=index, project=project, history_limit=limit
        ),
    )
    current = tmp_path / "Current"
    current.mkdir(parents=True)
    (current / "pyproject.toml").write_text(
        '[project]\nname="ai-bridge"\nversion = "0.2.6.32"\n',
        encoding="utf-8",
    )
    executor.current = current
    monkeypatch.setattr(executor, "_bus_source", lambda: {
        "repository": "example/user-bus",
        "branch": "main",
    })
    docs = {
        ("main", "PROJECT_STATE_INDEX.json"): {
            "text": json.dumps(_index()), "sha": "index-sha"
        },
        ("main", "PROJECT_A_CURRENT_STATE.md"): {
            "text": authority_text, "sha": "authority-sha"
        },
        ("main", "PROJECT_B_CURRENT_STATE.md"): {
            "text": "# Project B", "sha": "retarget-sha"
        },
    }
    monkeypatch.setattr(
        executor,
        "_fetch_repo_text",
        lambda repository, ref, path, timeout_seconds=5.0: docs[(ref, path)],
    )
    return executor


def test_bridge_project_resume_is_read_only_l1():
    cap = next(item for item in descriptor().capabilities if item.name == "bridge.project.resume")
    assert cap.write is False
    assert str(cap.risk).endswith("L1")


def test_explicit_project_matches_windows_path_alias(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", r"e:\Generic\ProjectA.hip")
    result = service.project_resume_local(index=_index(), project="project_a")
    assert result["project"]["key"] == "project_a"
    assert result["live"]["connected"] is True
    assert result["live"]["session_id"] == "HOU-A"


def test_single_live_project_can_be_inferred(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    result = service.project_resume_local(index=_index(), project=None)
    assert result["project"]["key"] == "project_a"


def test_multiple_live_projects_fail_closed(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    _session(service, "HOU-B", PROJECT_B_HIP, pid=200)
    result = service.project_resume_local(index=_index(), project=None)
    assert result["failure"]["code"] == "PROJECT_RESUME_AMBIGUOUS"
    assert set(result["candidates"]) == {"project_a", "project_b"}


def test_offline_project_returns_authority_target_but_not_safe(tmp_path):
    service = _service(tmp_path)
    result = service.project_resume_local(index=_index(), project="project_a")
    assert result["project"]["authority"]["path"] == "PROJECT_A_CURRENT_STATE.md"
    assert result["live"]["connected"] is False
    assert result["resume"]["safe_to_continue"] is False
    assert "PROJECT_HOST_OFFLINE" in result["resume"]["warnings"]


def test_last_terminal_and_success_are_recovered(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    _terminal(service, "cmd-success")
    result = service.project_resume_local(index=_index(), project="project_a")
    assert result["execution"]["last_terminal"]["command_id"] == "cmd-success"
    assert result["execution"]["last_success"]["command_id"] == "cmd-success"
    assert result["execution"]["last_failure"] is None


def test_terminal_failure_code_is_preserved(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    _terminal(service, "cmd-failed", "failed", failure_code="NODE_NOT_FOUND")
    result = service.project_resume_local(index=_index(), project="project_a")
    assert result["execution"]["last_failure"]["command_id"] == "cmd-failed"
    assert result["execution"]["last_failure"]["failure_code"] == "NODE_NOT_FOUND"


def test_inflight_command_blocks_safe_continue(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    service.db.insert_command(_command("cmd-inflight"))
    result = service.project_resume_local(index=_index(), project="project_a")
    assert result["execution"]["inflight"][0]["command_id"] == "cmd-inflight"
    assert result["execution"]["inflight"][0]["terminal"] is False
    assert result["resume"]["safe_to_continue"] is False


def test_prior_terminal_result_is_reused_without_reexecution(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    _terminal(service, "cmd-before-interruption")
    before = len(service.db.list_command_records())
    result = service.project_resume_local(index=_index(), project="project_a")
    after = len(service.db.list_command_records())
    assert before == after
    assert result["execution"]["last_terminal"]["command_id"] == "cmd-before-interruption"


def test_checkpoint_and_recovery_follow_logical_windows_project(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    service.checkpoints.write("cp-auto", {
        "checkpoint_id": "cp-auto",
        "original_hip": r"e:\GENERIC\ProjectA.hip",
        "checkpoint_path": "E:/Generic/backup/cp.hip",
        "verified": True,
    })
    service.recoveries.write("force-auto", {
        "recovery_id": "force-auto",
        "kind": "force_host_recovery",
        "status": "restarted_checkpoint_safe",
        "project_file": r"E:\Generic\ProjectA.hip",
        "new_session_id": "HOU-NEW",
    })
    service.recoveries.write("plugin-auto", {
        "recovery_id": "plugin-auto",
        "kind": "plugin_restart",
        "status": "applied",
        "project_file": PROJECT_A_HIP,
        "rollback_verified": True,
    })
    result = service.project_resume_local(index=_index(), project="project_a")
    assert result["recovery"]["latest_checkpoint"]["checkpoint_id"] == "cp-auto"
    assert result["recovery"]["latest_host_recovery"]["recovery_id"] == "force-auto"
    assert result["recovery"]["latest_plugin_restart"]["recovery_id"] == "plugin-auto"


def test_runtime_replacement_stale_session_is_not_authority(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-OLD", PROJECT_A_HIP, pid=100, stale=True)
    _session(service, "HOU-NEW", PROJECT_A_HIP, pid=200)
    result = service.project_resume_local(index=_index(), project="project_a")
    assert result["live"]["session_id"] == "HOU-NEW"
    assert result["live"]["matching_session_count"] == 1


def test_multiple_ai_histories_remain_distinct(tmp_path):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    _terminal(service, "channel-a-command")
    _terminal(service, "channel-b-command", "failed", failure_code="AUDIT_FAIL")
    records = service.db.list_command_records()
    ids = {row["command_id"] for row in records}
    assert {"channel-a-command", "channel-b-command"}.issubset(ids)
    result = service.project_resume_local(index=_index(), project="project_a")
    terminal_ids = {
        item["command_id"] for item in (
            result["execution"]["last_success"],
            result["execution"]["last_failure"],
        ) if item
    }
    assert terminal_ids == {"channel-a-command", "channel-b-command"}


def test_authority_content_digest_and_resume_token(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    _terminal(service, "cmd-success")
    executor = _executor(tmp_path, service, monkeypatch, authority_text="# Authority\nNext Action: iterate")
    result = executor.execute(_resume_command())
    assert result.status.value == "success"
    authority = result.result["project"]["authority"]
    assert authority["available"] is True
    assert authority["content"].startswith("# Authority")
    assert len(authority["digest"]) == 64
    token = result.result["resume"]["resume_token"]
    assert token["project"] == "project_a"
    assert token["last_terminal_command"] == "cmd-success"
    assert token["authority_digest"] == authority["digest"]
    assert token["runtime_version"] == "0.2.6.32"
    assert len(token["state_digest"]) == 64


def test_authority_fetch_failure_is_partial_and_fail_closed(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    executor = _executor(tmp_path, service, monkeypatch)
    original = executor._fetch_repo_text

    def fail_authority(repository, ref, path, timeout_seconds=5.0):
        if path == "PROJECT_A_CURRENT_STATE.md":
            raise TimeoutError("authority timeout")
        return original(repository, ref, path, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(executor, "_fetch_repo_text", fail_authority)
    result = executor.execute(_resume_command())
    assert result.status.value == "success"
    assert result.result["project"]["authority"]["available"] is False
    assert result.result["resume"]["safe_to_continue"] is False
    assert "PROJECT_AUTHORITY_UNAVAILABLE" in result.result["resume"]["warnings"]


def test_resume_token_stale_detection_returns_conflict(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    executor = _executor(tmp_path, service, monkeypatch)
    first = executor.execute(_resume_command())
    token = dict(first.result["resume"]["resume_token"])
    token["state_digest"] = "0" * 64
    second = executor.execute(_resume_command({
        "project": "project_a",
        "expected_resume_token": token,
    }))
    assert second.status.value == "conflict"
    assert second.failure.code == "RESUME_STATE_STALE"
    assert second.result["resume"]["safe_to_continue"] is False


def test_unknown_project_returns_specific_failure(tmp_path, monkeypatch):
    service = _service(tmp_path)
    executor = _executor(tmp_path, service, monkeypatch)
    result = executor.execute(_resume_command({"project": "no_such_project"}))
    assert result.status.value == "failed"
    assert result.failure.code == "PROJECT_RESUME_NOT_FOUND"


def test_project_index_fetch_failure_returns_specific_failure(tmp_path, monkeypatch):
    service = _service(tmp_path)
    executor = _executor(tmp_path, service, monkeypatch)
    monkeypatch.setattr(
        executor,
        "_fetch_repo_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("index timeout")),
    )
    result = executor.execute(_resume_command())
    assert result.status.value == "failed"
    assert result.failure.code == "PROJECT_INDEX_UNAVAILABLE"


def test_default_authority_budget_is_bounded_for_comment_fast_path(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _session(service, "HOU-A", PROJECT_A_HIP)
    executor = _executor(tmp_path, service, monkeypatch, authority_text="A" * 20000)
    result = executor.execute(_resume_command())
    authority = result.result["project"]["authority"]
    assert result.status.value == "success"
    assert authority["truncated"] is True
    assert authority["content_chars"] == 20000
    assert len(authority["content"]) < 5000
    assert "AI_BRIDGE_AUTHORITY_CONTENT_TRUNCATED" in authority["content"]
