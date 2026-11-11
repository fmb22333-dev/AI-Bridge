# Dated snapshot source changes

This directory preserves the MCP helper, its contract, installer inputs, package builder and verifier for the separately downloadable 2026-11-11 v2 snapshot. The complete selected Runtime and host source are inside the clean archive; this directory does not replace the canonical runtime/ source or update manifests.

The builder accepts explicit --runtime, --bridge-root, --plugin and fresh --output inputs from a development source. Do not point it at live user state or use generated supervisor output as the original source. The verifier starts only an isolated unpacked Runtime and tests its own fixture workspace.
