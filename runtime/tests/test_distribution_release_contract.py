from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_publish_keeps_development_and_clean_runtime_channels_separate():
    text = (ROOT / "src" / "ai_bridge" / "adapters" / "bridge_admin.py").read_text(encoding="utf-8")
    assert '"runtime_bundle.zip"' in text
    assert '"runtime_distribution_bundle.zip"' in text
    assert '"distribution-release.json"' in text
    assert '"stable-clean"' in text
    assert "_build_distribution_bundle" in text
    assert "_publish_source_mirror" in text
