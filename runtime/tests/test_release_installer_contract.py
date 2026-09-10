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
    assert "fmb22333-dev/ai-bridge-bus" not in text


def test_release_builder_is_deterministic_and_houdini_only_for_now():
    text = BUILDER.read_text(encoding="utf-8")
    assert "date_time=(1980, 1, 1, 0, 0, 0)" in text
    assert 'supported_hosts": ["houdini"]' in text
    assert 'unimplemented_hosts": ["unreal", "blender"]' in text
    assert "bundle_sha256" in text
    assert "knowledge_digest" in text
    assert "AI_Bridge_Installer.zip" in text


def test_release_verifier_checks_product_authority_boundaries():
    text = VERIFIER.read_text(encoding="utf-8")
    assert "Developer Bus leaked into product update authority" in text
    assert "Runtime bundle SHA-256 mismatch" in text
    assert "Clean Knowledge manifest digest mismatch" in text
