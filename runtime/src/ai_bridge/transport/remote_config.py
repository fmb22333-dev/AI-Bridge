from __future__ import annotations

import json
import platform
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode


@dataclass(frozen=True)
class GitHubRemoteConfig:
    repository: str
    branch: str
    bridge_id: str
    kind: str = "github_bus"


RemoteConfig = GitHubRemoteConfig


def save_remote_config(path: Path, config: RemoteConfig) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_remote_config(path: Path) -> RemoteConfig | None:
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != "github_bus":
        return None
    return GitHubRemoteConfig(
        repository=str(data["repository"]),
        branch=str(data.get("branch") or "main"),
        bridge_id=str(data["bridge_id"]),
    )


def delete_remote_config(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def default_bridge_id() -> str:
    host = platform.node().strip() or "AI-Bridge"
    host = re.sub(r"[^A-Za-z0-9_.-]+", "-", host).strip("-._") or "AI-Bridge"
    suffix = f"{uuid.getnode():012x}"[-6:]
    return f"{host[:48]}-{suffix}"


def normalize_github_repository(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("git@github.com:"):
        value = value[len("git@github.com:"):]
    elif value.startswith("https://github.com/"):
        value = value[len("https://github.com/"):]
    elif value.startswith("http://github.com/"):
        value = value[len("http://github.com/"):]
    elif value.startswith("github.com/"):
        value = value[len("github.com/"):]
    value = value.strip().strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    parts = [part for part in value.split("/") if part]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return value


def github_repository_url(repository: str) -> str:
    repository = normalize_github_repository(repository)
    return f"https://github.com/{repository}" if repository.count("/") == 1 else "https://github.com/"


def github_repository_new_url() -> str:
    return "https://github.com/new?name=ai-bridge-bus&description=AI%20Bridge%20command%20bus&visibility=private"


def github_login_url() -> str:
    return "https://github.com/login"


def github_token_settings_url() -> str:
    return "https://github.com/settings/personal-access-tokens"


def github_token_template_url(owner: str = "") -> str:
    query = {
        "name": "AI Bridge Bus",
        "description": "AI Bridge local GitHub transport. Grant access only to the dedicated Bridge repository.",
        "expires_in": "90",
        "administration": "write",
        "contents": "write",
        "issues": "write",
        "metadata": "read",
    }
    owner = owner.strip()
    if owner:
        query["target_name"] = owner
    return "https://github.com/settings/personal-access-tokens/new?" + urlencode(query)
