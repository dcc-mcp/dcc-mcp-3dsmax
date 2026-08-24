"""Typed batch animation and curve-exchange contracts."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._animation_native import native_curve, set_native_key_tangents
from dcc_mcp_3dsmax._animation_utils import (
    _keys,
    _transform_vector,
    anim_error,
    anim_success,
    resolve_anim_targets,
    set_interpolation,
    set_transform_key,
    time_settings,
)

ANIM_CURVES_SCHEMA = "dcc-mcp/anim-curves@1"
PROPERTIES = {"position", "rotation", "scale"}
TANGENTS = {"auto", "bezier", "linear", "step"}
MAX_TARGETS = 128
MAX_KEYS = 10_000
MAX_MUTATIONS = 50_000


def set_keyframes(
    runtime: Any,
    *,
    keys: Sequence[Dict[str, Any]],
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Apply one validated key batch to explicit nodes and verify each value."""
    targets = resolve_anim_targets(
        runtime,
        node_names=node_names,
        handles=handles,
        use_selection=use_selection,
    )
    if not targets.get("success"):
        return targets
    nodes = list(targets["objects"])
    validated, error = _validate_batch(keys, target_count=len(nodes))
    if error is not None:
        return error

    changed = 0
    for node in nodes:
        for key in validated:
            result = set_transform_key(
                runtime,
                node,
                frame=key["frame"],
                property_name=key["property"],
                value=key["value"],
            )
            if not result.get("success"):
                return anim_error(
                    "Could not set the complete keyframe batch",
                    changed_key_count=changed,
                    failure=result,
                )
            _record_tangents(node, key)
            tangent_readback = set_native_key_tangents(runtime, node, key)
            if tangent_readback is False:
                return anim_error(
                    "Keyframe tangent host readback did not match the request",
                    changed_key_count=changed,
                    target=_target_name(node, key["property"]),
                    frame=key["frame"],
                )
            if key["in_tangent"] == key["out_tangent"]:
                set_interpolation(
                    runtime,
                    node,
                    interpolation=key["in_tangent"],
                    frames=[key["frame"]],
                )
            actual = _read_transform_at(runtime, node, key["frame"], key["property"])
            if not _vectors_match(actual, key["value"]):
                return anim_error(
                    "Keyframe host readback did not match the requested value",
                    changed_key_count=changed,
                    target=_target_name(node, key["property"]),
                    frame=key["frame"],
                    expected=key["value"],
                    actual=actual,
                )
            changed += 1

    return anim_success(
        "Set and verified batch keyframes",
        changed_key_count=changed,
        curve_data=curve_data(runtime, nodes),
    )


def get_anim_curves(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
    properties: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Return versioned curve data for explicit target nodes."""
    error = _validate_properties(properties)
    if error is not None:
        return error
    targets = resolve_anim_targets(
        runtime,
        node_names=node_names,
        handles=handles,
        use_selection=use_selection,
    )
    if not targets.get("success"):
        return targets
    nodes = list(targets["objects"])
    if len(nodes) > MAX_TARGETS:
        return anim_error("Too many animation targets", maximum=MAX_TARGETS)
    return anim_success(
        "Read animation curves",
        curve_data=curve_data(runtime, nodes, properties=properties),
    )


def import_anim_curves(runtime: Any, curve_data_value: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a complete anim-curves payload before applying any mutation."""
    plans, error = _validate_curve_payload(runtime, curve_data_value)
    if error is not None:
        return error

    changed = 0
    imported_nodes = []
    for node, keys in plans:
        result = set_keyframes(runtime, node_names=[str(node.name)], keys=keys)
        if not result.get("success"):
            return anim_error(
                "Could not import the complete animation curve payload",
                changed_key_count=changed,
                failure=result,
            )
        changed += int(result["data"]["changed_key_count"])
        imported_nodes.append(node)
    return anim_success(
        "Imported and verified animation curves",
        changed_key_count=changed,
        curve_data=curve_data(runtime, imported_nodes),
    )


def curve_data(
    runtime: Any,
    nodes: Sequence[Any],
    *,
    properties: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Build the provisional shared anim-curves v1 shape from verified keys."""
    wanted = set(properties or PROPERTIES)
    curves: List[Dict[str, Any]] = []
    for node in nodes:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for key in _keys(node):
            property_name = key.get("property")
            if property_name not in wanted:
                continue
            grouped.setdefault(property_name, []).append(_curve_key(key))
        for property_name in sorted(wanted):
            native = native_curve(runtime, node, property_name, _read_transform_at)
            if native is not None:
                curves.append(native)
                continue
            if property_name not in grouped:
                continue
            rows = sorted(grouped[property_name], key=lambda row: row["t"])
            curves.append(
                {
                    "target": _target_name(node, property_name),
                    "keys": rows,
                    "pre_infinity": "constant",
                    "post_infinity": "constant",
                    "key_count": len(rows),
                }
            )
    return {
        "schema": ANIM_CURVES_SCHEMA,
        "fps": float(time_settings(runtime)["frame_rate"]),
        "curves": curves,
    }


def _validate_batch(
    keys: Sequence[Dict[str, Any]], *, target_count: int
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not isinstance(keys, (list, tuple)) or not keys:
        return [], anim_error("keys must contain at least one keyframe")
    if target_count > MAX_TARGETS:
        return [], anim_error("Too many animation targets", maximum=MAX_TARGETS)
    if len(keys) > MAX_KEYS or target_count * len(keys) > MAX_MUTATIONS:
        return [], anim_error(
            "Keyframe batch exceeds the bounded mutation limit",
            maximum_keys=MAX_KEYS,
            maximum_mutations=MAX_MUTATIONS,
        )
    validated = []
    for index, raw in enumerate(keys):
        if not isinstance(raw, dict):
            return [], anim_error("Each keyframe must be an object", key_index=index)
        property_name = raw.get("property")
        if property_name not in PROPERTIES:
            return [], anim_error("Unsupported keyed property", key_index=index, property=property_name)
        frame = raw.get("frame")
        if not _finite_number(frame):
            return [], anim_error("Keyframe frame must be finite", key_index=index)
        value = raw.get("value")
        expected_length = (
            4 if property_name == "rotation" and isinstance(value, (list, tuple)) and len(value) == 4 else 3
        )
        if not isinstance(value, (list, tuple)) or len(value) != expected_length:
            return [], anim_error("Keyframe value must contain three numbers", key_index=index)
        if not all(_finite_number(item) for item in value):
            return [], anim_error("Keyframe values must be finite numbers", key_index=index)
        in_tangent = raw.get("in_tangent", raw.get("interpolation", "auto"))
        out_tangent = raw.get("out_tangent", raw.get("interpolation", "auto"))
        if in_tangent not in TANGENTS or out_tangent not in TANGENTS:
            return [], anim_error("Unsupported key tangent", key_index=index)
        validated.append(
            {
                "frame": float(frame),
                "property": property_name,
                "value": [float(item) for item in value],
                "in_tangent": in_tangent,
                "out_tangent": out_tangent,
            }
        )
    return validated, None


def _validate_properties(properties: Optional[Sequence[str]]) -> Optional[Dict[str, Any]]:
    if properties is None:
        return None
    if not isinstance(properties, (list, tuple)) or any(item not in PROPERTIES for item in properties):
        return anim_error("properties must contain only position, rotation, or scale")
    return None


def _validate_curve_payload(
    runtime: Any, payload: Dict[str, Any]
) -> Tuple[List[Tuple[Any, List[Dict[str, Any]]]], Optional[Dict[str, Any]]]:
    if not isinstance(payload, dict) or payload.get("schema") != ANIM_CURVES_SCHEMA:
        return [], anim_error("curve_data must use schema {}".format(ANIM_CURVES_SCHEMA))
    fps = payload.get("fps")
    if not _finite_number(fps) or float(fps) <= 0:
        return [], anim_error("curve_data fps must be a positive finite number")
    source_fps = float(fps)
    runtime_fps = time_settings(runtime).get("frame_rate")
    if not _finite_number(runtime_fps) or float(runtime_fps) <= 0:
        return [], anim_error("Runtime frame rate must be a positive finite number")
    target_fps = float(runtime_fps)
    curves = payload.get("curves")
    if not isinstance(curves, list) or not curves:
        return [], anim_error("curve_data curves must contain at least one curve")
    if len(curves) > MAX_TARGETS * len(PROPERTIES):
        return [], anim_error("curve_data contains too many curves")

    plans: List[Tuple[Any, List[Dict[str, Any]]]] = []
    total_keys = 0
    for curve_index, raw_curve in enumerate(curves):
        if not isinstance(raw_curve, dict):
            return [], anim_error("Each curve must be an object", curve_index=curve_index)
        target = raw_curve.get("target")
        if not isinstance(target, str) or "." not in target:
            return [], anim_error("Curve target must be node.property", curve_index=curve_index)
        node_name, property_name = target.rsplit(".", 1)
        if not node_name or property_name not in PROPERTIES:
            return [], anim_error("Curve target is not supported", curve_index=curve_index, target=target)
        resolved = resolve_anim_targets(runtime, node_names=[node_name])
        if not resolved.get("success"):
            return [], anim_error("Curve target node is missing: {}".format(node_name), target=target)
        rows = raw_curve.get("keys")
        if not isinstance(rows, list) or not rows:
            return [], anim_error("Curve keys must not be empty", curve_index=curve_index)
        if raw_curve.get("key_count") is not None and raw_curve.get("key_count") != len(rows):
            return [], anim_error("Curve key_count does not match keys", curve_index=curve_index)
        converted = []
        for key in rows:
            if not isinstance(key, dict):
                return [], anim_error("Each curve key must be an object", curve_index=curve_index)
            source_frame = key.get("t")
            target_frame = source_frame
            if _finite_number(source_frame):
                target_frame = float(source_frame) * target_fps / source_fps
            converted.append(
                {
                    "frame": target_frame,
                    "property": property_name,
                    "value": key.get("v"),
                    "in_tangent": key.get("in", "auto"),
                    "out_tangent": key.get("out", "auto"),
                }
            )
        validated, validation_error = _validate_batch(converted, target_count=1)
        if validation_error is not None:
            validation_error["data"]["curve_index"] = curve_index
            return [], validation_error
        total_keys += len(validated)
        if total_keys > MAX_MUTATIONS:
            return [], anim_error("Curve payload exceeds the bounded mutation limit")
        plans.append((resolved["objects"][0], validated))
    return plans, None


def _record_tangents(node: Any, key: Dict[str, Any]) -> None:
    for stored in reversed(_keys(node)):
        if stored.get("frame") == key["frame"] and stored.get("property") == key["property"]:
            stored["in_tangent"] = key["in_tangent"]
            stored["out_tangent"] = key["out_tangent"]
            stored["interpolation"] = key["in_tangent"]
            return


def _read_transform_at(runtime: Any, node: Any, frame: float, property_name: str) -> List[float]:
    try:
        import pymxs  # noqa: PLC0415

        attime = getattr(pymxs, "attime", None)
        if callable(attime):
            with attime(frame):
                return _transform_vector(node, property_name)
    except (ImportError, RuntimeError, TypeError, ValueError):
        pass
    return _transform_vector(node, property_name)


def _vectors_match(actual: Sequence[float], expected: Sequence[float]) -> bool:
    return len(actual) >= len(expected) and all(
        math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-6) for left, right in zip(actual, expected)
    )


def _curve_key(key: Dict[str, Any]) -> Dict[str, Any]:
    tangent = key.get("interpolation") or "auto"
    return {
        "t": float(key["frame"]),
        "v": [float(item) for item in key["value"]],
        "in": key.get("in_tangent") or tangent,
        "out": key.get("out_tangent") or tangent,
    }


def _target_name(node: Any, property_name: str) -> str:
    return "{}.{}".format(str(getattr(node, "name", "")), property_name)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
