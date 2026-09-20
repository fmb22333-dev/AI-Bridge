import json

import pytest
from pydantic import ValidationError

from ai_bridge.core.worker_manifest import load_project_manifest


def _write_manifest(root, payload):
    path = root / ".ai-bridge" / "project.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_project_manifest_is_language_agnostic_and_argv_based(tmp_path):
    _write_manifest(
        tmp_path,
        {
            "schema_version": "1.0",
            "project": "qqbot",
            "commands": {
                "test": {"argv": ["python", "-m", "pytest"], "timeout_seconds": 60}
            },
            "services": {
                "bot": {"argv": ["python", "main.py"], "reload": "restart"}
            },
        },
    )

    manifest = load_project_manifest(tmp_path)

    assert manifest.project == "qqbot"
    assert manifest.commands["test"].argv == ["python", "-m", "pytest"]
    assert manifest.services["bot"].reload == "restart"
    assert not hasattr(manifest, "language")


def test_manifest_rejects_shell_string_commands(tmp_path):
    _write_manifest(
        tmp_path,
        {
            "schema_version": "1.0",
            "project": "web",
            "commands": {"dev": {"argv": "npm run dev"}},
        },
    )

    with pytest.raises(ValidationError):
        load_project_manifest(tmp_path)


def test_manifest_rejects_invalid_reload_mode(tmp_path):
    _write_manifest(
        tmp_path,
        {
            "schema_version": "1.0",
            "project": "web",
            "services": {"dev": {"argv": ["npm", "run", "dev"], "reload": "magic"}},
        },
    )

    with pytest.raises(ValidationError):
        load_project_manifest(tmp_path)


def test_manifest_rejects_parent_cwd(tmp_path):
    _write_manifest(
        tmp_path,
        {
            "schema_version": "1.0",
            "project": "web",
            "commands": {"test": {"argv": ["python", "test.py"], "cwd": "../outside"}},
        },
    )

    with pytest.raises(ValidationError):
        load_project_manifest(tmp_path)
