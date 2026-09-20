from __future__ import annotations

import math

from .inspect_ops import _safe


_OWNER_ALIASES = {
    "detail": "detail",
    "global": "detail",
    "point": "point",
    "points": "point",
    "prim": "prim",
    "primitive": "prim",
    "primitives": "prim",
    "vertex": "vertex",
    "vertices": "vertex",
}


def _node(hou, path: str):
    node = hou.node(path)
    if node is None:
        raise ValueError(f"NODE_NOT_FOUND: {path}")
    return node


def _normalize_owner(owner: str) -> str:
    key = str(owner or "point").strip().lower()
    normalized = _OWNER_ALIASES.get(key)
    if normalized is None:
        raise ValueError("ARGUMENT_INVALID: owner must be detail|point|prim|vertex")
    return normalized


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(number, maximum))


def _geometry(node, frame):
    if frame is None:
        fn = getattr(node, "geometry", None)
        if not callable(fn):
            raise ValueError(f"GEOMETRY_UNAVAILABLE: {node.path()}")
        geo = fn()
    else:
        fn = getattr(node, "geometryAtFrame", None)
        if not callable(fn):
            raise ValueError(f"GEOMETRY_AT_FRAME_UNAVAILABLE: {node.path()}")
        geo = fn(frame)
    if geo is None:
        raise ValueError(f"GEOMETRY_UNAVAILABLE: {node.path()}")
    return geo


def _attribs(geo, owner: str):
    method = {
        "detail": "globalAttribs",
        "point": "pointAttribs",
        "prim": "primAttribs",
        "vertex": "vertexAttribs",
    }[owner]
    fn = getattr(geo, method, None)
    if not callable(fn):
        return []
    return list(fn() or [])


def _attrib_name(attrib) -> str:
    fn = getattr(attrib, "name", None)
    return str(fn() if callable(fn) else attrib)


def _attrib_metadata(attrib) -> dict:
    def call(name, default=None):
        fn = getattr(attrib, name, None)
        if not callable(fn):
            return default
        try:
            return fn()
        except Exception:
            return default

    data_type = call("dataType")
    return {
        "name": _attrib_name(attrib),
        "data_type": str(data_type) if data_type is not None else None,
        "size": call("size"),
        "array": bool(call("isArrayType", False)),
    }


def _elements(geo, owner: str):
    if owner == "detail":
        return [geo]
    if owner == "point":
        fn = getattr(geo, "points", None)
        if not callable(fn):
            raise ValueError("POINT_QUERY_UNAVAILABLE")
        return list(fn() or [])
    if owner == "prim":
        fn = getattr(geo, "prims", None)
        if not callable(fn):
            raise ValueError("PRIM_QUERY_UNAVAILABLE")
        return list(fn() or [])

    # Houdini Geometry has no reliable iterVertices() contract across the
    # supported project history. Flatten primitive.vertices() instead.
    prim_fn = getattr(geo, "prims", None)
    if not callable(prim_fn):
        raise ValueError("VERTEX_QUERY_UNAVAILABLE")
    vertices = []
    for prim in prim_fn() or []:
        vertex_fn = getattr(prim, "vertices", None)
        if callable(vertex_fn):
            vertices.extend(list(vertex_fn() or []))
    return vertices


def _element_id(element, owner: str, fallback: int):
    if owner == "detail":
        return 0
    for method in (("number",) if owner != "vertex" else ("linearNumber", "number")):
        fn = getattr(element, method, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return fallback


def _attrib_value(element, owner: str, name: str):
    if owner == "detail":
        fn = getattr(element, "attribValue", None)
    else:
        fn = getattr(element, "attribValue", None)
    if not callable(fn):
        raise ValueError(f"ATTRIBUTE_READ_UNAVAILABLE: {name}")
    return _safe(fn(name))


def _selected_attribute_names(available: list[str], requested, max_attributes: int) -> tuple[list[str], bool]:
    if requested is None:
        names = list(available)
    else:
        if not isinstance(requested, list):
            raise ValueError("ARGUMENT_INVALID: attributes must be a list[str]")
        names = []
        for item in requested:
            name = str(item or "").strip()
            if not name:
                raise ValueError("ARGUMENT_INVALID: attributes contains an empty name")
            if name not in names:
                names.append(name)
    missing = [name for name in names if name not in available]
    if missing:
        raise ValueError("ATTRIBUTE_NOT_FOUND: " + ", ".join(missing))
    truncated = len(names) > max_attributes
    return names[:max_attributes], truncated


def _matches_key(element, owner: str, key_attribute: str | None, key_values) -> bool:
    if not key_attribute:
        return True
    value = _attrib_value(element, owner, key_attribute)
    if key_values is None:
        return bool(value)
    if not isinstance(key_values, list):
        key_values = [key_values]
    safe_values = [_safe(item) for item in key_values]
    return value in safe_values


def _numeric_shape(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = float(value)
        return [value] if math.isfinite(value) else None
    if isinstance(value, (list, tuple)) and value:
        out = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            number = float(item)
            if not math.isfinite(number):
                return None
            out.append(number)
        return out
    return None


def _stats(values: list):
    numeric = [_numeric_shape(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    if numeric and len(numeric) == len(values):
        width = len(numeric[0])
        if all(len(value) == width for value in numeric):
            mins = [min(value[index] for value in numeric) for index in range(width)]
            maxs = [max(value[index] for value in numeric) for index in range(width)]
            means = [sum(value[index] for value in numeric) / len(numeric) for index in range(width)]
            if width == 1:
                return {
                    "kind": "numeric",
                    "count": len(values),
                    "min": mins[0],
                    "max": maxs[0],
                    "mean": means[0],
                }
            return {
                "kind": "numeric_vector",
                "count": len(values),
                "size": width,
                "min": mins,
                "max": maxs,
                "mean": means,
            }

    distinct = []
    seen = set()
    for value in values:
        rendered = repr(value)
        if rendered in seen:
            continue
        seen.add(rendered)
        distinct.append(value)
        if len(distinct) >= 20:
            break
    return {
        "kind": "non_numeric",
        "count": len(values),
        "distinct_sample": distinct,
        "distinct_sample_truncated": len(seen) >= 20 and len(values) > len(distinct),
    }


def _sample(geo, *, owner: str, attributes, key_attribute, key_values, mode: str, max_rows: int, max_attributes: int):
    attrib_objects = _attribs(geo, owner)
    metadata = [_attrib_metadata(attrib) for attrib in attrib_objects]
    available = [item["name"] for item in metadata]

    if key_attribute:
        key_attribute = str(key_attribute).strip()
        if key_attribute not in available:
            raise ValueError(f"ATTRIBUTE_NOT_FOUND: {key_attribute}")

    names, attributes_truncated = _selected_attribute_names(available, attributes, max_attributes)
    if key_attribute and key_attribute not in names and mode in {"rows", "stats"}:
        # Filtering attributes need not consume the caller's output attribute budget.
        pass

    elements = _elements(geo, owner)
    selected = []
    for index, element in enumerate(elements):
        if _matches_key(element, owner, key_attribute, key_values):
            selected.append((index, element))

    out = {
        "element_count": len(elements),
        "selected_count": len(selected),
        "attribute_count": len(metadata),
        "attributes": metadata,
        "selected_attributes": names,
        "attributes_truncated": attributes_truncated,
    }

    if mode == "summary":
        return out

    if mode == "rows":
        rows = []
        for index, element in selected[:max_rows]:
            rows.append({
                "id": _element_id(element, owner, index),
                "attributes": {
                    name: _attrib_value(element, owner, name)
                    for name in names
                },
            })
        out.update({
            "rows": rows,
            "returned_rows": len(rows),
            "rows_truncated": len(selected) > len(rows),
        })
        return out

    if mode == "stats":
        stats = {}
        for name in names:
            values = [_attrib_value(element, owner, name) for _, element in selected]
            stats[name] = _stats(values) if values else {"kind": "empty", "count": 0}
        out["stats"] = stats
        return out

    raise ValueError("ARGUMENT_INVALID: mode must be summary|rows|stats")


def query(
    hou,
    path: str,
    *,
    owner: str = "point",
    attributes=None,
    key_attribute: str | None = None,
    key_values=None,
    mode: str = "summary",
    frames=None,
    max_rows: int = 200,
    max_attributes: int = 32,
) -> dict:
    node = _node(hou, str(path))
    owner = _normalize_owner(owner)
    mode = str(mode or "summary").strip().lower()
    if mode not in {"summary", "rows", "stats"}:
        raise ValueError("ARGUMENT_INVALID: mode must be summary|rows|stats")

    max_rows = _bounded_int(max_rows, 200, 0, 1000)
    max_attributes = _bounded_int(max_attributes, 32, 1, 64)

    if frames is None:
        frame_values = [None]
    else:
        if not isinstance(frames, list) or not frames:
            raise ValueError("ARGUMENT_INVALID: frames must be a non-empty list")
        if len(frames) > 64:
            raise ValueError("ARGUMENT_INVALID: frames exceeds maximum 64")
        frame_values = []
        for value in frames:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("ARGUMENT_INVALID: frames must contain only numbers")
            frame_values.append(value)

    samples = []
    for frame in frame_values:
        geo = _geometry(node, frame)
        payload = _sample(
            geo,
            owner=owner,
            attributes=attributes,
            key_attribute=key_attribute,
            key_values=key_values,
            mode=mode,
            max_rows=max_rows,
            max_attributes=max_attributes,
        )
        payload["frame"] = frame
        samples.append(payload)

    return {
        "path": node.path(),
        "owner": owner,
        "mode": mode,
        "frames_requested": None if frames is None else list(frame_values),
        "sampling": "current_geometry" if frames is None else "geometryAtFrame",
        "frame_count": len(samples),
        "samples": samples,
        "bounded": {
            "max_rows": max_rows,
            "max_attributes": max_attributes,
            "max_frames": 64,
        },
    }
