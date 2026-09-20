from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MANIFEST_RELATIVE_PATH = Path(".ai-bridge") / "project.json"


def _validate_relative_path(value: str) -> str:
    text = str(value or ".").strip() or "."
    if "\x00" in text:
        raise ValueError("path contains NUL")
    posix = PurePosixPath(text.replace("\\", "/"))
    windows = PureWindowsPath(text)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError("path must be relative to the workspace")
    if ".." in posix.parts or ".." in windows.parts:
        raise ValueError("path must not contain parent traversal")
    return text


class _ProcessSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: list[str] = Field(min_length=1)
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = str(item)
            if not text.strip():
                raise ValueError("argv entries must not be blank")
            if "\x00" in text:
                raise ValueError("argv entries must not contain NUL")
            cleaned.append(text)
        return cleaned

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            name = str(key).strip()
            if not name or "=" in name or "\x00" in name:
                raise ValueError("environment variable names must be non-empty names")
            text = str(item)
            if "\x00" in text:
                raise ValueError("environment variable values must not contain NUL")
            cleaned[name] = text
        return cleaned


class CommandSpec(_ProcessSpec):
    timeout_seconds: float = Field(default=300.0, ge=1.0, le=7200.0)


class ServiceSpec(_ProcessSpec):
    reload: Literal["native", "restart", "manual"] = "restart"
    stop_timeout_seconds: float = Field(default=10.0, ge=0.1, le=60.0)


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    project: str = Field(min_length=1, max_length=128)
    commands: dict[str, CommandSpec] = Field(default_factory=dict)
    services: dict[str, ServiceSpec] = Field(default_factory=dict)

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("project must not be blank")
        return text

    @field_validator("commands", "services")
    @classmethod
    def validate_entry_names(cls, value: dict):
        for name in value:
            if not str(name).strip():
                raise ValueError("command/service names must not be blank")
        return value


def load_project_manifest(workspace_root: Path) -> ProjectManifest:
    root = Path(workspace_root).expanduser().resolve()
    path = root / MANIFEST_RELATIVE_PATH
    if not path.is_file():
        # Keep the missing-manifest marker platform-native so callers can classify
        # the error reliably on both Windows and POSIX.
        raise FileNotFoundError(str(MANIFEST_RELATIVE_PATH))
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ProjectManifest.model_validate(raw)
