from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx


API_VERSION = "2026-03-10"
PRODUCT_REPOSITORY = "fmb22333-dev/AI-Bridge"
PRODUCT_REF = "main"
BUS_ISSUE_TITLE = "AI Bridge Bus"
BUS_ISSUE_MARKER = "<!-- AI_BRIDGE_BUS_ISSUE_V1 -->"
BUS_README_MARKER = "<!-- AI_BRIDGE_BUS_README_V1 -->"


class GitHubProvisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubBusProvisionRequest:
    repository: str
    bridge_id: str
    runtime_source_repository: str = PRODUCT_REPOSITORY
    runtime_source_ref: str = PRODUCT_REF
    private: bool = True


def _clean_repository(value: str) -> tuple[str | None, str]:
    text = str(value or "").strip().strip("/")
    if text.startswith("https://github.com/"):
        text = text[len("https://github.com/"):]
    if text.endswith(".git"):
        text = text[:-4]
    parts = [part for part in text.split("/") if part]
    if len(parts) == 1:
        return None, parts[0]
    if len(parts) == 2:
        return parts[0], parts[1]
    raise GitHubProvisionError("Repository must be name or owner/name")


def _render_index(*, repository: str, branch: str, bridge_id: str,
                  runtime_source_repository: str, runtime_source_ref: str) -> dict:
    spec = lambda path: {
        "repository": runtime_source_repository,
        "ref": runtime_source_ref,
        "path": path,
    }
    return {
        "schema_version": "1.0",
        "index_role": "single_machine_entrypoint",
        "repository": repository,
        "entrypoint": {"ref": branch, "path": "PROJECT_STATE_INDEX.json"},
        "authority_policy": {
            "read_this_index_first": True,
            "normal_recovery_must_not_repository_search": True,
            "precedence": [
                "live_runtime_status",
                "project_current_state",
                "normative_bridge_specs",
                "historical_handoff_or_chat",
            ],
            "volatile_runtime_versions_must_not_be_taken_from_static_docs": True,
            "on_missing_pointer": "only then use targeted repository search",
        },
        "bridge": {
            "live_runtime_status": {
                "ref": branch,
                "path": f".ai-bridge/status/{bridge_id}.json",
            },
            "read_first": {"ref": branch, "path": "AI_BRIDGE_READ_FIRST.md"},
            "runtime_source": {
                "repository": runtime_source_repository,
                "ref": runtime_source_ref,
            },
            "normative_specs": {
                "execution": spec("specs/AI_BRIDGE_EXECUTION_SPEC.md"),
                "learning_policy": spec("specs/AI_BRIDGE_LEARNING_POLICY.md"),
                "deployment": spec("specs/AI_BRIDGE_DEPLOYMENT_SPEC.md"),
                "ai_protocol": spec("specs/AI_AGENT_PROTOCOL.md"),
            },
        },
        "projects": {},
        "recovery_profiles": {
            "bridge_modify": [
                f"fetch {branch}:PROJECT_STATE_INDEX.json",
                "fetch live runtime status",
                "fetch normative Bridge specifications from runtime_source",
                "inspect only source files relevant to the requested modification",
            ]
        },
    }


def _read_first(repository: str, branch: str) -> str:
    return f"""# AI Bridge — READ THIS FIRST

This repository is a clean per-install AI Bridge Bus.

Mandatory entrypoint:
- `{branch}:PROJECT_STATE_INDEX.json`

Read that file before any Bridge or project operation.

Use the authority order and pointers declared by the index. Do not infer volatile
runtime/session/workspace state from static documentation. Do not replay mutations
merely because a previous chat response is missing; recover durable state first.

This Bus starts with `projects={{}}`. Do not infer or import projects from the
shared Runtime/product repository.

Repository: `{repository}`
"""


def _bus_readme(repository: str) -> str:
    return f"""{BUS_README_MARKER}
# AI Bridge Bus

## AI / Agent — Start Here

Before searching this repository or performing any Bridge/project operation:

1. Read `PROJECT_STATE_INDEX.json`.
2. Follow the pointers and authority order declared there.
3. Do not perform repository-wide discovery unless the index cannot resolve the required information.

`PROJECT_STATE_INDEX.json` is the canonical machine entrypoint.

This repository is a per-install AI Bridge Bus. Runtime, adapters, protocol, and distributable generic knowledge come from the shared product repository referenced by the index.

Repository: `{repository}`
"""


class GitHubBusProvisioner:
    """Create/repair a clean per-install GitHub Bus.

    Credentials are supplied at runtime and are never written to repository files.
    The initial automatic-create path intentionally supports only the authenticated
    personal account; organization/external-owner repositories use a separate
    existing-repository/manual authorization flow until explicitly implemented.
    """

    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        token = str(token or "").strip()
        if not token:
            raise GitHubProvisionError("GitHub token is required")
        self.token = token
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20.0, connect=8.0),
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            message = str((response.json() or {}).get("message") or "").strip()
        except Exception:
            message = response.text.strip()[:400]
        return f"HTTP {response.status_code}" + (f": {message}" if message else "")

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        return self.client.request(method, url, headers=self.headers, **kwargs)

    def authenticated_login(self) -> str:
        response = self._request("GET", "https://api.github.com/user")
        if response.status_code != 200:
            raise GitHubProvisionError("GitHub identity check failed: " + self._detail(response))
        login = str(response.json().get("login") or "").strip()
        if not login:
            raise GitHubProvisionError("GitHub identity response did not include login")
        return login

    def _repo(self, repository: str) -> dict | None:
        response = self._request("GET", f"https://api.github.com/repos/{repository}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubProvisionError("Repository lookup failed: " + self._detail(response))
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _create_user_repo(self, name: str, *, private: bool) -> dict:
        response = self._request(
            "POST",
            "https://api.github.com/user/repos",
            json={
                "name": name,
                "description": "AI Bridge per-install command/state bus",
                "private": bool(private),
                "has_issues": True,
                "auto_init": True,
            },
        )
        if response.status_code != 201:
            raise GitHubProvisionError(
                "Repository creation failed. One-click creation requires repository Administration: write permission. "
                + self._detail(response)
            )
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _root_items(self, repository: str, branch: str) -> list[dict]:
        response = self._request(
            "GET", f"https://api.github.com/repos/{repository}/contents", params={"ref": branch}
        )
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise GitHubProvisionError("Repository contents check failed: " + self._detail(response))
        data = response.json()
        return data if isinstance(data, list) else []

    def _read_json_file(self, repository: str, branch: str, path: str) -> dict | None:
        response = self._request(
            "GET", f"https://api.github.com/repos/{repository}/contents/{path}", params={"ref": branch}
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubProvisionError(f"Unable to inspect {path}: " + self._detail(response))
        payload = response.json()
        try:
            raw = base64.b64decode(str(payload.get("content") or "").replace("\n", ""))
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise GitHubProvisionError(f"Unable to decode existing {path}: {exc}") from exc
        return data if isinstance(data, dict) else None

    def _ensure_existing_repo_is_safe(self, repository: str, branch: str) -> None:
        existing_index = self._read_json_file(repository, branch, "PROJECT_STATE_INDEX.json")
        if existing_index is not None:
            if existing_index.get("index_role") != "single_machine_entrypoint":
                raise GitHubProvisionError("Existing PROJECT_STATE_INDEX.json is not an AI Bridge index")
            projects = existing_index.get("projects")
            if isinstance(projects, dict) and projects:
                raise GitHubProvisionError(
                    "Existing AI Bridge repository already contains registered projects; clean provisioning will not overwrite it"
                )
            return

        allowed = {"README.md", "LICENSE", ".gitignore"}
        root_names = {str(item.get("name") or "") for item in self._root_items(repository, branch)}
        unexpected = sorted(name for name in root_names if name and name not in allowed)
        if unexpected:
            raise GitHubProvisionError(
                "Existing repository is not empty/clean; refusing to overwrite: " + ", ".join(unexpected[:10])
            )

    def _put_text(self, repository: str, branch: str, path: str, text: str, message: str) -> None:
        url = f"https://api.github.com/repos/{repository}/contents/{path}"
        existing = self._request("GET", url, params={"ref": branch})
        sha = None
        if existing.status_code == 200:
            sha = str(existing.json().get("sha") or "") or None
        elif existing.status_code != 404:
            raise GitHubProvisionError(f"Unable to inspect {path}: " + self._detail(existing))

        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        response = self._request("PUT", url, json=body)
        if response.status_code not in (200, 201):
            raise GitHubProvisionError(f"Unable to write {path}: " + self._detail(response))

    def _ensure_bus_readme(self, repository: str, branch: str) -> None:
        url = f"https://api.github.com/repos/{repository}/contents/README.md"
        existing = self._request("GET", url, params={"ref": branch})
        current = ""
        if existing.status_code == 200:
            try:
                current = base64.b64decode(
                    str(existing.json().get("content") or "").replace("\n", "")
                ).decode("utf-8")
            except Exception as exc:
                raise GitHubProvisionError("Unable to decode existing README.md") from exc
        elif existing.status_code != 404:
            raise GitHubProvisionError("Unable to inspect README.md: " + self._detail(existing))

        if BUS_README_MARKER in current:
            return
        bootstrap = _bus_readme(repository).rstrip() + "\n"
        merged = bootstrap if not current.strip() else bootstrap + "\n---\n\n" + current.lstrip()
        self._put_text(repository, branch, "README.md", merged, "Add AI Bridge Bus AI entrypoint")

    def _ensure_issue_one(self, repository: str) -> dict:
        response = self._request("GET", f"https://api.github.com/repos/{repository}/issues/1")
        if response.status_code == 200:
            issue = response.json()
            body = str(issue.get("body") or "")
            title = str(issue.get("title") or "")
            if BUS_ISSUE_MARKER not in body and title != BUS_ISSUE_TITLE:
                raise GitHubProvisionError("Issue #1 is already occupied by a non-Bridge issue")
            return issue
        if response.status_code != 404:
            raise GitHubProvisionError("Issue #1 check failed: " + self._detail(response))

        created = self._request(
            "POST",
            f"https://api.github.com/repos/{repository}/issues",
            json={
                "title": BUS_ISSUE_TITLE,
                "body": BUS_ISSUE_MARKER
                + "\nDedicated AI Bridge transport issue. Dynamic AI channels and compatibility mailbox comments live here. Do not delete while this Bus is active.",
            },
        )
        if created.status_code != 201:
            raise GitHubProvisionError(
                "Issue creation failed. GitHub Issues: write permission is required. "
                + self._detail(created)
            )
        issue = created.json()
        if int(issue.get("number") or 0) != 1:
            raise GitHubProvisionError(
                f"Dedicated Bus requires Issue #1, but GitHub created Issue #{issue.get('number')}"
            )
        return issue

    def provision(self, request: GitHubBusProvisionRequest) -> dict:
        login = self.authenticated_login()
        owner, name = _clean_repository(request.repository)
        owner = owner or login
        if owner.casefold() != login.casefold():
            raise GitHubProvisionError(
                "One-click repository creation currently supports the authenticated personal account only; "
                "organization/external-owner repositories use the existing-repository flow"
            )

        repository = f"{owner}/{name}"
        repo = self._repo(repository)
        created = repo is None
        if repo is None:
            repo = self._create_user_repo(name, private=request.private)
            repository = str(repo.get("full_name") or repository)

        branch = str(repo.get("default_branch") or "main")
        self._ensure_existing_repo_is_safe(repository, branch)

        index = _render_index(
            repository=repository,
            branch=branch,
            bridge_id=request.bridge_id,
            runtime_source_repository=request.runtime_source_repository,
            runtime_source_ref=request.runtime_source_ref,
        )
        self._put_text(
            repository,
            branch,
            "PROJECT_STATE_INDEX.json",
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            "Initialize clean AI Bridge state index",
        )
        self._put_text(
            repository,
            branch,
            "AI_BRIDGE_READ_FIRST.md",
            _read_first(repository, branch),
            "Initialize AI Bridge read-first entrypoint",
        )
        self._ensure_bus_readme(repository, branch)
        issue = self._ensure_issue_one(repository)

        return {
            "ok": True,
            "created_repository": created,
            "repository": repository,
            "branch": branch,
            "bridge_id": request.bridge_id,
            "runtime_source_repository": request.runtime_source_repository,
            "runtime_source_ref": request.runtime_source_ref,
            "issue_number": int(issue.get("number") or 1),
            "projects": {},
        }
