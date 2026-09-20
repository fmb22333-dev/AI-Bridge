from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_bridge.deployment.github_provision import (
    BUS_ISSUE_MARKER,
    GitHubBusProvisionRequest,
    GitHubBusProvisioner,
    GitHubProvisionError,
)


def test_clean_bus_provision_creates_repo_index_and_issue_one():
    writes = {}
    created_repo = {"full_name": "alice/ai-bridge-bus", "default_branch": "main"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/user":
            return httpx.Response(200, json={"login": "alice"})
        if request.method == "GET" and path == "/repos/alice/ai-bridge-bus":
            return httpx.Response(404)
        if request.method == "POST" and path == "/user/repos":
            body = json.loads(request.content)
            assert body["private"] is True
            assert body["has_issues"] is True
            return httpx.Response(201, json=created_repo)
        if request.method == "GET" and path == "/repos/alice/ai-bridge-bus/contents/PROJECT_STATE_INDEX.json":
            if "PROJECT_STATE_INDEX.json" not in writes:
                return httpx.Response(404)
            raw = writes["PROJECT_STATE_INDEX.json"]
            return httpx.Response(200, json={"sha": "sha-index", "content": base64.b64encode(raw).decode()})
        if request.method == "GET" and path == "/repos/alice/ai-bridge-bus/contents":
            return httpx.Response(200, json=[{"name": "README.md"}])
        if request.method == "GET" and path == "/repos/alice/ai-bridge-bus/contents/AI_BRIDGE_READ_FIRST.md":
            return httpx.Response(404)
        if request.method == "PUT" and path.startswith("/repos/alice/ai-bridge-bus/contents/"):
            name = path.rsplit("/", 1)[-1]
            body = json.loads(request.content)
            writes[name] = base64.b64decode(body["content"])
            return httpx.Response(201, json={"content": {"sha": "new"}})
        if request.method == "GET" and path == "/repos/alice/ai-bridge-bus/issues/1":
            return httpx.Response(404)
        if request.method == "POST" and path == "/repos/alice/ai-bridge-bus/issues":
            body = json.loads(request.content)
            assert BUS_ISSUE_MARKER in body["body"]
            return httpx.Response(201, json={"number": 1, "title": body["title"], "body": body["body"]})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provisioner = GitHubBusProvisioner(
        "token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provisioner.provision(
        GitHubBusProvisionRequest(
            repository="alice/ai-bridge-bus",
            bridge_id="ALICE-123",
            runtime_source_repository="upstream/ai-bridge-runtime",
        )
    )

    assert result["created_repository"] is True
    index = json.loads(writes["PROJECT_STATE_INDEX.json"])
    assert index["projects"] == {}
    assert index["repository"] == "alice/ai-bridge-bus"
    assert index["bridge"]["runtime_source"]["repository"] == "upstream/ai-bridge-runtime"
    assert index["bridge"]["live_runtime_status"]["semantics"] == "last_published_durable_state"
    assert index["bridge"]["live_runtime_status"]["live_probe"] == {
        "adapter": "bridge_transport",
        "operation": "transport.ping",
    }
    authority = index["bridge"]["realtime_transport_authority"]
    assert authority["canonical"]["repository"] == "upstream/ai-bridge-runtime"
    assert authority["canonical"]["path"] == "AI_BRIDGE_PRIMARY_REALTIME_TRANSPORT.md"
    assert "高速通道" in authority["aliases"]
    assert index["bridge"]["presence_version_authority"]["runtime_version_source"] == "active Runtime pyproject.toml"
    read_first = writes["AI_BRIDGE_READ_FIRST.md"].decode("utf-8")
    assert "高速通道" in read_first
    assert "transport.ping" in read_first
    assert "pyproject.toml" in read_first
    assert "AUTO_UV" not in writes["PROJECT_STATE_INDEX.json"].decode("utf-8")


def test_existing_project_bus_is_not_overwritten():
    existing = {
        "schema_version": "1.0",
        "index_role": "single_machine_entrypoint",
        "projects": {"auto_uv": {"authority": "something"}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/user":
            return httpx.Response(200, json={"login": "alice"})
        if request.method == "GET" and path == "/repos/alice/ai-bridge-bus":
            return httpx.Response(200, json={"full_name": "alice/ai-bridge-bus", "default_branch": "main"})
        if request.method == "GET" and path.endswith("/contents/PROJECT_STATE_INDEX.json"):
            raw = json.dumps(existing).encode()
            return httpx.Response(200, json={"sha": "old", "content": base64.b64encode(raw).decode()})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provisioner = GitHubBusProvisioner(
        "token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(GitHubProvisionError, match="registered projects"):
        provisioner.provision(
            GitHubBusProvisionRequest(
                repository="alice/ai-bridge-bus",
                bridge_id="ALICE-123",
                runtime_source_repository="upstream/ai-bridge-runtime",
            )
        )
