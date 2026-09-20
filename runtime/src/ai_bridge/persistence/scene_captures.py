from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCENE_SCHEMA = "aibridge_ue_scene/1"
CAPTURE_SCHEMA = "aibridge_scene_capture/1"
_ALLOWED_INCLUDE = {
    "identity",
    "transform",
    "bounds",
    "properties",
    "references",
    "components",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    ).encode("utf-8")


def _safe_token(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_.-")
    return text[:48] or "capture"


def _actor_components(actor: dict) -> list[dict]:
    value = actor.get("components")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _object_refs(obj: dict) -> list[dict]:
    value = obj.get("asset_references")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _object_props(obj: dict) -> list[dict]:
    value = obj.get("properties")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _search_blob(actor: dict) -> str:
    return json.dumps(actor, ensure_ascii=False, sort_keys=True, default=repr).casefold()


class SceneCaptureStore:
    """Immutable local store for full host scene snapshots.

    The host is allowed to return a high-fidelity shallow scene payload to the
    local Bridge. Bridge persists that payload once and returns only a bounded
    summary to the command caller. Follow-up queries operate on this store and
    do not re-contact the host.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_supported_scene(scene: Any) -> bool:
        return (
            isinstance(scene, dict)
            and scene.get("schema_version") == SCENE_SCHEMA
            and isinstance(scene.get("actors"), list)
        )

    def capture(self, *, command_id: str, session_id: str, scene: dict) -> dict:
        if not self.is_supported_scene(scene):
            raise ValueError("UNSUPPORTED_SCENE_SCHEMA")

        raw = _canonical_bytes(scene)
        digest = hashlib.sha256(raw).hexdigest()
        capture_id = f"cap_{_safe_token(command_id)}_{digest[:12]}"
        actors = [item for item in scene.get("actors", []) if isinstance(item, dict)]

        component_count = 0
        property_count = 0
        reference_count = 0
        unique_assets: set[str] = set()
        class_counts: Counter[str] = Counter()
        truncated = bool(scene.get("actors_truncated") or scene.get("truncated"))

        for actor in actors:
            actor_class = str(actor.get("class") or "")
            if actor_class:
                class_counts[actor_class] += 1

            property_count += len(_object_props(actor))
            refs = _object_refs(actor)
            reference_count += len(refs)
            for ref in refs:
                path = str(ref.get("object_path") or "")
                if path and bool(ref.get("is_asset", True)):
                    unique_assets.add(path)

            components = _actor_components(actor)
            component_count += len(components)
            truncated = truncated or bool(
                actor.get("components_truncated")
                or actor.get("attached_actors_truncated")
            )
            for component in components:
                component_class = str(component.get("class") or "")
                if component_class:
                    class_counts[component_class] += 1
                property_count += len(_object_props(component))
                component_refs = _object_refs(component)
                reference_count += len(component_refs)
                for ref in component_refs:
                    path = str(ref.get("object_path") or "")
                    if path and bool(ref.get("is_asset", True)):
                        unique_assets.add(path)

        captured_at = datetime.now(timezone.utc).isoformat()
        wrapper = {
            "capture_schema": CAPTURE_SCHEMA,
            "capture_id": capture_id,
            "command_id": command_id,
            "session_id": session_id,
            "captured_at": captured_at,
            "sha256": digest,
            "raw_bytes": len(raw),
            "scene": scene,
        }
        target = self.root / f"{capture_id}.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(
                wrapper,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=repr,
            ),
            encoding="utf-8",
        )
        temp.replace(target)

        return {
            "capture_schema": CAPTURE_SCHEMA,
            "capture_id": capture_id,
            "source_schema": scene.get("schema_version"),
            "captured_at": captured_at,
            "sha256": digest,
            "raw_bytes": len(raw),
            "project_name": scene.get("project_name"),
            "engine_version": scene.get("engine_version"),
            "plugin_version": scene.get("plugin_version"),
            "world": {
                "name": scene.get("world_name"),
                "path": scene.get("world_path"),
                "package": scene.get("world_package"),
            },
            "counts": {
                "actors": len(actors),
                "components": component_count,
                "properties": property_count,
                "references": reference_count,
                "unique_assets": len(unique_assets),
                "unique_classes": len(class_counts),
            },
            "classes": [
                {"class": name, "count": count}
                for name, count in sorted(
                    class_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:16]
            ],
            "truncated": truncated,
            "query": {
                "adapter": "scene_store",
                "operation": "scene.query",
            },
        }

    def read(self, capture_id: str) -> dict:
        capture_id = str(capture_id or "").strip()
        if not capture_id or not re.fullmatch(r"cap_[A-Za-z0-9_.-]+", capture_id):
            raise ValueError("INVALID_CAPTURE_ID")
        path = self.root / f"{capture_id}.json"
        if not path.exists():
            raise FileNotFoundError(capture_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def query(self, args: dict) -> dict:
        capture_id = str(args.get("capture_id") or "").strip()
        wrapper = self.read(capture_id)
        scene = wrapper.get("scene") or {}
        actors = [item for item in scene.get("actors", []) if isinstance(item, dict)]

        text = str(args.get("text") or "").strip().casefold()
        class_contains = str(args.get("class_contains") or "").strip().casefold()
        path_contains = str(args.get("path_contains") or "").strip().casefold()
        reference_contains = str(args.get("reference_contains") or "").strip().casefold()
        max_items = max(1, min(int(args.get("max_items") or 64), 512))

        requested = args.get("include") or ["identity", "references"]
        if not isinstance(requested, list):
            raise ValueError("include must be an array")
        include = [str(item) for item in requested]
        invalid = [item for item in include if item not in _ALLOWED_INCLUDE]
        if invalid:
            raise ValueError("unsupported include fields: " + ", ".join(invalid))

        matched: list[dict] = []
        for actor in actors:
            if text and text not in _search_blob(actor):
                continue
            if class_contains and not (
                class_contains in str(actor.get("class") or "").casefold()
                or class_contains in str(actor.get("class_path") or "").casefold()
            ):
                continue
            if path_contains and path_contains not in str(actor.get("path") or "").casefold():
                continue
            if reference_contains:
                refs = list(_object_refs(actor))
                for component in _actor_components(actor):
                    refs.extend(_object_refs(component))
                if not any(
                    reference_contains in str(ref.get("object_path") or "").casefold()
                    for ref in refs
                ):
                    continue
            matched.append(actor)

        projected = [
            self._project_actor(actor, include)
            for actor in matched[:max_items]
        ]
        return {
            "capture_id": capture_id,
            "source_schema": scene.get("schema_version"),
            "matched_actor_count": len(matched),
            "returned_count": len(projected),
            "truncated": len(matched) > max_items,
            "actors": projected,
        }

    @staticmethod
    def _project_actor(actor: dict, include: list[str]) -> dict:
        out: dict[str, Any] = {}
        if "identity" in include:
            for key in (
                "name",
                "path",
                "class",
                "class_path",
                "label",
                "fluidflux_related",
            ):
                if key in actor:
                    out[key] = actor[key]
        if "transform" in include:
            for key in ("location", "rotation", "scale"):
                if key in actor:
                    out[key] = actor[key]
        if "bounds" in include:
            for key in ("bounds_origin", "bounds_extent"):
                if key in actor:
                    out[key] = actor[key]
        if "properties" in include:
            out["properties"] = actor.get("properties", [])
        if "references" in include:
            out["asset_references"] = actor.get("asset_references", [])
        if "components" in include:
            out["components"] = actor.get("components", [])
        return out
