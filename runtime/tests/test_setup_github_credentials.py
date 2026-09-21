from __future__ import annotations

from ai_bridge.web import routes


def test_gh_cli_environment_ignores_generic_token_overrides(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "stale-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "stale-github")
    monkeypatch.setenv("UNRELATED", "keep")
    env = routes._gh_cli_environment()
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["UNRELATED"] == "keep"


def test_local_credential_candidates_prefer_bridge_and_cli_before_generic_env(monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_GITHUB_TOKEN", "bridge-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(routes, "_gh_cli_token", lambda: "cli-token")
    assert routes._github_credential_candidates() == [
        ("bridge_env", "bridge-token"),
        ("github_cli", "cli-token"),
        ("gh_token_env", "gh-token"),
        ("github_token_env", "github-token"),
    ]


def test_local_credential_resolution_skips_stale_candidate(monkeypatch):
    monkeypatch.setattr(
        routes,
        "_github_credential_candidates",
        lambda: [("gh_token_env", "stale"), ("github_cli", "good")],
    )

    def verify(token):
        if token == "stale":
            raise RuntimeError("HTTP 403")
        return "alice"

    monkeypatch.setattr(routes, "_verify_github_credential", verify)
    credential, errors = routes._usable_local_github_credential()
    assert credential == {"source": "github_cli", "token": "good", "login": "alice"}
    assert errors and "403" in errors[0]
