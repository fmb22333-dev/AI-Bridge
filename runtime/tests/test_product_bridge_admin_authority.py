from __future__ import annotations

import json

from ai_bridge.adapters.bridge_admin import BridgeAdminExecutor
from ai_bridge.deployment.github_provision import PRODUCT_REF, PRODUCT_REPOSITORY
from ai_bridge.transport.remote_config import GitHubRemoteConfig, save_remote_config


def test_default_update_source_is_shared_product_repository(tmp_path):
    executor = BridgeAdminExecutor(data_dir=tmp_path / "data")
    source = executor._update_source()
    assert source["repository"] == PRODUCT_REPOSITORY
    assert source["branch"] == PRODUCT_REF
    assert source["manifest_path"] == "runtime-release.json"
    assert source["bootstrap_from_bus"] is False
    assert source["publish_source_mirror"] is False


def test_user_bus_authority_is_separate_from_product_update_source(tmp_path):
    data = tmp_path / "data"
    save_remote_config(
        data / "remote.json",
        GitHubRemoteConfig(
            repository="example/user-bus",
            branch="main",
            bridge_id="BRIDGE-TEST",
        ),
    )
    executor = BridgeAdminExecutor(data_dir=data)

    assert executor._bus_source() == {
        "repository": "example/user-bus",
        "branch": "main",
    }
    assert executor._update_source()["repository"] == PRODUCT_REPOSITORY


def test_explicit_product_mirror_never_enables_legacy_bus_bootstrap(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "update_source.json").write_text(
        json.dumps({
            "repository": "example/product-mirror",
            "branch": "stable",
            "manifest_path": "release.json",
            "bootstrap_from_bus": True,
        }),
        encoding="utf-8",
    )
    executor = BridgeAdminExecutor(data_dir=data)
    source = executor._update_source()

    assert source["repository"] == "example/product-mirror"
    assert source["branch"] == "stable"
    assert source["manifest_path"] == "release.json"
    assert source["bootstrap_from_bus"] is False
    assert source["publish_source_mirror"] is False
