from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "INSTALL_AI_BRIDGE.ps1"
BUILDER = ROOT / "installer" / "build_release.py"
VERIFIER = ROOT / "installer" / "verify_release.py"


def test_installer_consumes_release_authority_and_verifies_hashes():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'ProductRepository = "fmb22333-dev/AI-Bridge"' in text
    assert 'Get-RepoJson "release-manifest.json"' in text
    assert "runtime_manifest" in text
    assert "supervisor_manifest" in text
    assert "runtime_bundle_sha256" not in text or "bundle_sha256" in text
    assert "Get-Sha256Hex" in text
    assert 'manifest_path = "runtime-release.json"' in text
    assert "bootstrap_from_bus = $false" in text
    assert "ai-bridge-bus" not in text.lower()


def test_release_builder_is_deterministic_and_supports_houdini_and_unreal():
    text = BUILDER.read_text(encoding="utf-8")
    assert "date_time=(1980, 1, 1, 0, 0, 0)" in text
    assert 'supported_hosts": ["houdini", "unreal"]' in text
    assert 'unimplemented_hosts": ["blender"]' in text
    assert "bundle_sha256" in text
    assert "knowledge_digest" in text
    assert "AI_Bridge_Installer.zip" in text


def test_release_verifier_checks_product_authority_boundaries():
    text = VERIFIER.read_text(encoding="utf-8")
    assert "Developer Bus leaked into product update authority" in text
    assert "Runtime bundle SHA-256 mismatch" in text
    assert "Clean Knowledge manifest digest mismatch" in text


def test_installer_falls_back_to_git_blob_for_large_runtime_archives():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "git_url" in text
    assert "GitHub Contents API omits inline content for files larger than 1 MB" in text
    assert '$blob.encoding -ne "base64"' in text
    assert "$blob.content" in text


def test_installer_recovers_from_bad_local_token_for_public_repo():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "$AnonymousHeaders" in text
    assert "raw.githubusercontent.com" in text
    assert "authenticated API, anonymous API, and raw download" in text


def test_release_bundles_offline_windows_dependency_wheelhouse():
    builder = BUILDER.read_text(encoding="utf-8")
    assert "requirements-release-lock.txt" in builder
    assert '"--only-binary=:all:"' in builder
    assert 'OFFLINE_PYTHON_MINORS = ("311", "312", "313", "314")' in builder
    assert "runtime/_wheelhouse/" in builder

    bootstrap = (
        ROOT / "bootstrap" / "supervisor" / "0.1.6" / "_System" / "bootstrap.bat"
    ).read_text(encoding="utf-8")
    assert "--no-index" in bootstrap
    assert "--find-links" in bootstrap
    assert "requirements-release-lock.txt" in bootstrap
    assert "pip install -e" not in bootstrap
