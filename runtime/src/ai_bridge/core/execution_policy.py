from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock


MIN_BUDGET_SECONDS = 1.0
MAX_BUDGET_SECONDS = 7200.0
DEFAULT_BUDGET_SECONDS = 120.0
DEFAULT_AUTO_RECOVER = False


def _validated_seconds(value) -> float:
    seconds = float(value)
    if seconds < MIN_BUDGET_SECONDS or seconds > MAX_BUDGET_SECONDS:
        raise ValueError(
            f"execution budget must be between {MIN_BUDGET_SECONDS:g} and {MAX_BUDGET_SECONDS:g} seconds"
        )
    return seconds


def _project_key(project_file: str) -> str:
    raw = str(project_file or "").strip()
    if not raw:
        return ""
    expanded = os.path.abspath(os.path.expanduser(raw))
    return os.path.normcase(expanded)


@dataclass(frozen=True)
class BudgetResolution:
    seconds: float
    source: str
    project_file: str | None
    auto_recover: bool
    auto_recover_source: str

    def as_dict(self) -> dict:
        return {
            "seconds": self.seconds,
            "source": self.source,
            "project_file": self.project_file,
            "auto_recover": self.auto_recover,
            "auto_recover_source": self.auto_recover_source,
        }


class ExecutionPolicyStore:
    """Persisted host execution policy.

    Budget precedence:
      AI explicit -> exact project -> workspace -> Bridge default.

    Auto-recovery precedence:
      AI explicit -> workspace -> Bridge default.

    Project backup filenames are intentionally not interpreted by Core.
    Workspace scope is the stable fallback across renamed/recovered project files.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        default_budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        default_auto_recover: bool = DEFAULT_AUTO_RECOVER,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = RLock()
        self._default_budget_seconds = _validated_seconds(default_budget_seconds)
        self._default_auto_recover = bool(default_auto_recover)
        self._projects: dict[str, dict] = {}
        self._workspaces: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._default_budget_seconds = _validated_seconds(
                data.get("default_budget_seconds", self._default_budget_seconds)
            )
            self._default_auto_recover = bool(
                data.get("default_auto_recover", self._default_auto_recover)
            )

            parsed_projects = {}
            projects = data.get("projects", {})
            if isinstance(projects, dict):
                for key, item in projects.items():
                    if not isinstance(item, dict):
                        continue
                    display = str(item.get("project_file") or key)
                    parsed_projects[_project_key(display)] = {
                        "project_file": display,
                        "budget_seconds": _validated_seconds(item.get("budget_seconds")),
                    }
            self._projects = parsed_projects

            parsed_workspaces = {}
            workspaces = data.get("workspaces", {})
            if isinstance(workspaces, dict):
                for workspace_id, item in workspaces.items():
                    workspace = str(workspace_id).strip()
                    if not workspace:
                        continue
                    if isinstance(item, (int, float)):
                        parsed_workspaces[workspace] = {
                            "budget_seconds": _validated_seconds(item),
                            "auto_recover": None,
                        }
                    elif isinstance(item, dict):
                        parsed_workspaces[workspace] = {
                            "budget_seconds": (
                                _validated_seconds(item["budget_seconds"])
                                if item.get("budget_seconds") is not None
                                else None
                            ),
                            "auto_recover": (
                                bool(item["auto_recover"])
                                if item.get("auto_recover") is not None
                                else None
                            ),
                        }
            self._workspaces = parsed_workspaces
        except Exception:
            self._projects = {}
            self._workspaces = {}

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.1",
            "default_budget_seconds": self._default_budget_seconds,
            "default_auto_recover": self._default_auto_recover,
            "projects": self._projects,
            "workspaces": self._workspaces,
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    @property
    def default_budget_seconds(self) -> float:
        with self._lock:
            return self._default_budget_seconds

    @property
    def default_auto_recover(self) -> bool:
        with self._lock:
            return self._default_auto_recover

    def set_default(self, seconds: float, *, auto_recover: bool | None = None) -> dict:
        with self._lock:
            self._default_budget_seconds = _validated_seconds(seconds)
            if auto_recover is not None:
                self._default_auto_recover = bool(auto_recover)
            self._save()
            return {
                "budget_seconds": self._default_budget_seconds,
                "auto_recover": self._default_auto_recover,
            }

    def set_project(self, project_file: str, seconds: float) -> dict:
        key = _project_key(project_file)
        if not key:
            raise ValueError("project_file is required")
        item = {
            "project_file": str(project_file),
            "budget_seconds": _validated_seconds(seconds),
        }
        with self._lock:
            self._projects[key] = item
            self._save()
        return dict(item)

    def remove_project(self, project_file: str) -> bool:
        key = _project_key(project_file)
        with self._lock:
            existed = key in self._projects
            self._projects.pop(key, None)
            if existed:
                self._save()
            return existed

    def set_workspace(
        self,
        workspace_id: str,
        seconds: float,
        *,
        auto_recover: bool | None = None,
    ) -> dict:
        workspace = str(workspace_id or "").strip()
        if not workspace:
            raise ValueError("workspace_id is required")
        with self._lock:
            existing = dict(self._workspaces.get(workspace) or {})
            existing["budget_seconds"] = _validated_seconds(seconds)
            if auto_recover is not None:
                existing["auto_recover"] = bool(auto_recover)
            else:
                existing.setdefault("auto_recover", None)
            self._workspaces[workspace] = existing
            self._save()
            return {
                "workspace_id": workspace,
                "budget_seconds": existing["budget_seconds"],
                "auto_recover": existing.get("auto_recover"),
            }

    def remove_workspace(self, workspace_id: str) -> bool:
        workspace = str(workspace_id or "").strip()
        with self._lock:
            existed = workspace in self._workspaces
            self._workspaces.pop(workspace, None)
            if existed:
                self._save()
            return existed

    def workspace_policy(self, workspace_id: str | None) -> dict | None:
        workspace = str(workspace_id or "").strip()
        if not workspace:
            return None
        with self._lock:
            item = self._workspaces.get(workspace)
            return None if item is None else dict(item)

    def project_policy(self, project_file: str | None) -> dict | None:
        key = _project_key(project_file or "")
        if not key:
            return None
        with self._lock:
            item = self._projects.get(key)
            return None if item is None else dict(item)

    def resolve(self, command) -> BudgetResolution | None:
        if not getattr(command, "session", None):
            return None

        execution = getattr(command, "execution", None)
        explicit_budget = getattr(execution, "budget_seconds", None)
        project_file = getattr(command, "project_file", None)
        project = self.project_policy(project_file)
        workspace = self.workspace_policy(getattr(command, "workspace", None))

        if explicit_budget is not None:
            seconds = _validated_seconds(explicit_budget)
            budget_source = "ai_explicit"
        elif project is not None:
            seconds = float(project["budget_seconds"])
            budget_source = "project_default"
        elif workspace is not None and workspace.get("budget_seconds") is not None:
            seconds = float(workspace["budget_seconds"])
            budget_source = "workspace_default"
        else:
            seconds = self.default_budget_seconds
            budget_source = "bridge_default"

        explicit_recovery = getattr(execution, "auto_recover", None)
        if explicit_recovery is not None:
            auto_recover = bool(explicit_recovery)
            recovery_source = "ai_explicit"
        elif workspace is not None and workspace.get("auto_recover") is not None:
            auto_recover = bool(workspace["auto_recover"])
            recovery_source = "workspace_default"
        else:
            auto_recover = self.default_auto_recover
            recovery_source = "bridge_default"

        return BudgetResolution(
            seconds=seconds,
            source=budget_source,
            project_file=project_file,
            auto_recover=auto_recover,
            auto_recover_source=recovery_source,
        )

    def status(
        self,
        *,
        active_projects: list[str] | None = None,
        active_workspaces: list[str] | None = None,
    ) -> dict:
        active_projects = [str(x) for x in (active_projects or []) if str(x).strip()]
        active_workspaces = [str(x) for x in (active_workspaces or []) if str(x).strip()]
        with self._lock:
            projects = [dict(item) for item in self._projects.values()]
            workspaces = {key: dict(value) for key, value in self._workspaces.items()}
            default_budget = self._default_budget_seconds
            default_recovery = self._default_auto_recover

        resolved_projects = []
        for project_file in active_projects:
            override = self.project_policy(project_file)
            resolved_projects.append({
                "project_file": project_file,
                "budget_seconds": float(override["budget_seconds"]) if override else None,
                "source": "project_default" if override else "none",
                "has_override": override is not None,
            })

        resolved_workspaces = []
        for workspace_id in active_workspaces:
            item = workspaces.get(workspace_id) or {}
            budget = item.get("budget_seconds")
            recovery = item.get("auto_recover")
            resolved_workspaces.append({
                "workspace_id": workspace_id,
                "budget_seconds": float(budget) if budget is not None else default_budget,
                "budget_source": "workspace_default" if budget is not None else "bridge_default",
                "auto_recover": bool(recovery) if recovery is not None else default_recovery,
                "auto_recover_source": "workspace_default" if recovery is not None else "bridge_default",
            })

        return {
            "default_budget_seconds": default_budget,
            "default_auto_recover": default_recovery,
            "projects": sorted(projects, key=lambda x: x["project_file"].lower()),
            "workspaces": workspaces,
            "active_projects": resolved_projects,
            "active_workspaces": resolved_workspaces,
            "precedence": {
                "budget": ["ai_explicit", "project_default", "workspace_default", "bridge_default"],
                "auto_recover": ["ai_explicit", "workspace_default", "bridge_default"],
            },
            "min_budget_seconds": MIN_BUDGET_SECONDS,
            "max_budget_seconds": MAX_BUDGET_SECONDS,
        }
