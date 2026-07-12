"""Helpers for 3ds Max animation timeline/keyframe skill scripts."""

from __future__ import annotations

import contextlib
from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._scene_utils import node_identity, point3_to_list, resolve_node_objects


def anim_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def anim_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def resolve_anim_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Resolve explicit animation targets or explicitly requested selection."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001
            selected = []
        if not selected:
            return anim_error("Current selection is empty", objects=[])
        return {"success": True, "message": "Resolved selected nodes", "objects": selected}
    result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
    if not result.get("success"):
        return anim_error(result["message"], errors=result.get("errors", []), objects=[])
    result["status"] = "success"
    return result


def time_settings(runtime: Any) -> Dict[str, Any]:
    """Return common timeline settings."""
    return {
        "current_time": float(getattr(runtime, "currentTime", getattr(runtime, "sliderTime", 0)) or 0),
        "frame_start": int(getattr(runtime, "animationRangeStart", getattr(runtime, "frameStart", 0)) or 0),
        "frame_end": int(getattr(runtime, "animationRangeEnd", getattr(runtime, "frameEnd", 0)) or 0),
        "frame_rate": float(getattr(runtime, "frameRate", 30.0) or 30.0),
    }


def set_current_time(runtime: Any, frame: float) -> Dict[str, Any]:
    """Set current timeline time."""
    runtime.currentTime = float(frame)
    runtime.sliderTime = float(frame)
    return anim_success("Updated current time", settings=time_settings(runtime))


def set_timeline(
    runtime: Any,
    *,
    start_frame: Optional[int] = None,
    end_frame: Optional[int] = None,
    frame_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Set timeline range and frame rate."""
    current = time_settings(runtime)
    start = current["frame_start"] if start_frame is None else int(start_frame)
    end = current["frame_end"] if end_frame is None else int(end_frame)
    if end < start:
        return anim_error("end_frame must be greater than or equal to start_frame", start_frame=start, end_frame=end)
    runtime.animationRangeStart = start
    runtime.animationRangeEnd = end
    runtime.frameStart = start
    runtime.frameEnd = end
    if frame_rate is not None:
        runtime.frameRate = float(frame_rate)
    return anim_success("Updated timeline settings", settings=time_settings(runtime))


def controller_summary(node: Any) -> Dict[str, Any]:
    """Return transform controller metadata for one node."""
    controllers = {}
    for attr in ("position", "rotation", "scale"):
        value = getattr(node, attr, None)
        controller = getattr(value, "controller", None)
        controllers[attr] = type(controller).__name__ if controller is not None else None
    return {"node": node_identity(node), "controllers": controllers}


def list_keyframes(node: Any, properties: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Return keyframe data stored on a node."""
    keys = _keys(node)
    wanted = set(properties or ["position", "rotation", "scale"])
    rows = []
    for key in keys:
        if key.get("property") in wanted:
            rows.append(dict(key))
    return {"node": node_identity(node), "keyframes": rows, "count": len(rows)}


def set_transform_key(
    runtime: Any, node: Any, *, frame: float, property_name: str, value: Sequence[float]
) -> Dict[str, Any]:
    """Set one transform keyframe."""
    if property_name not in {"position", "rotation", "scale"}:
        return anim_error("Unsupported keyed property", property=property_name)
    key = {
        "frame": float(frame),
        "property": property_name,
        "value": [float(item) for item in value],
        "interpolation": None,
    }
    keys = _keys(node)
    keys[:] = [
        item for item in keys if not (item.get("frame") == key["frame"] and item.get("property") == property_name)
    ]
    keys.append(key)
    if not _key_with_pymxs_animation(runtime, node, frame, property_name, key["value"]):
        setter = getattr(runtime, "setKey", None)
        if callable(setter):
            setter(node, frame, property_name, key["value"])
    return anim_success("Set transform keyframe", node=node_identity(node), keyframe=key, changed_key_count=1)


def _key_with_pymxs_animation(
    runtime: Any, node: Any, frame: float, property_name: str, value: Sequence[float]
) -> bool:
    """Create a native Max key without relying on the unrelated ``setKey`` struct."""
    try:
        import pymxs  # noqa: PLC0415
    except ImportError:
        return False
    animate = getattr(pymxs, "animate", None)
    attime = getattr(pymxs, "attime", None)
    if not callable(animate) or not callable(attime):
        return False

    converted: Any = list(value)
    constructor_name = "EulerAngles" if property_name == "rotation" else "Point3"
    constructor = getattr(runtime, constructor_name, None)
    if callable(constructor):
        converted = constructor(*value[:3])
    with contextlib.ExitStack() as stack:
        stack.enter_context(animate(True))
        stack.enter_context(attime(frame))
        setattr(node, property_name, converted)
    return True


def delete_keyframes(
    node: Any, *, frames: Optional[Sequence[float]] = None, properties: Optional[Sequence[str]] = None
) -> Dict[str, Any]:
    """Delete matching keyframes."""
    keys = _keys(node)
    if not keys:
        return anim_error("Target has no animation keyframes", node=node_identity(node), changed_key_count=0)
    before = len(keys)
    wanted_frames = {float(frame) for frame in frames} if frames else None
    wanted_props = set(properties or ["position", "rotation", "scale"])
    keys[:] = [
        key
        for key in keys
        if not (
            key.get("property") in wanted_props
            and (wanted_frames is None or float(key.get("frame", 0)) in wanted_frames)
        )
    ]
    changed = before - len(keys)
    if not changed:
        return anim_error("No matching keyframes found", node=node_identity(node), changed_key_count=0)
    return anim_success("Deleted keyframes", node=node_identity(node), changed_key_count=changed)


def set_interpolation(node: Any, *, interpolation: str, frames: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """Set interpolation/tangent metadata on matching keys."""
    if interpolation not in {"linear", "step", "bezier", "auto"}:
        return anim_error("Unsupported interpolation", interpolation=interpolation)
    keys = _keys(node)
    if not keys:
        return anim_error("Target has no animation keyframes", node=node_identity(node), changed_key_count=0)
    wanted_frames = {float(frame) for frame in frames} if frames else None
    changed = 0
    for key in keys:
        if wanted_frames is None or float(key.get("frame", 0)) in wanted_frames:
            key["interpolation"] = interpolation
            changed += 1
    if not changed:
        return anim_error("No matching keyframes found", node=node_identity(node), changed_key_count=0)
    return anim_success("Updated key interpolation", node=node_identity(node), changed_key_count=changed)


def bake_transform_animation(
    runtime: Any, node: Any, *, start_frame: int, end_frame: int, step: int = 1
) -> Dict[str, Any]:
    """Bake simple transform values into keyframe rows."""
    if end_frame < start_frame:
        return anim_error(
            "end_frame must be greater than or equal to start_frame", start_frame=start_frame, end_frame=end_frame
        )
    safe_step = max(1, int(step))
    changed = 0
    original_time = getattr(runtime, "sliderTime", getattr(runtime, "currentTime", None))
    try:
        for frame in range(int(start_frame), int(end_frame) + 1, safe_step):
            if hasattr(runtime, "sliderTime"):
                runtime.sliderTime = frame
            elif hasattr(runtime, "currentTime"):
                runtime.currentTime = frame
            for prop in ("position", "rotation", "scale"):
                value = _transform_vector(node, prop)
                set_transform_key(runtime, node, frame=frame, property_name=prop, value=value)
                changed += 1
    finally:
        if original_time is not None:
            if hasattr(runtime, "sliderTime"):
                runtime.sliderTime = original_time
            elif hasattr(runtime, "currentTime"):
                runtime.currentTime = original_time
    return anim_success("Baked transform animation", node=node_identity(node), changed_key_count=changed)


def export_curve_data(nodes: Sequence[Any]) -> Dict[str, Any]:
    """Export a simple public curve-data shape."""
    return {
        "version": 1,
        "nodes": [{"node": node_identity(node), "keyframes": list_keyframes(node)["keyframes"]} for node in nodes],
    }


def import_curve_data(runtime: Any, curve_data: Dict[str, Any]) -> Dict[str, Any]:
    """Import the public curve-data shape."""
    changed = 0
    errors = []
    for row in curve_data.get("nodes", []):
        node_info = row.get("node", {})
        result = resolve_anim_targets(runtime, node_names=[node_info.get("node_name")])
        if not result.get("success"):
            errors.append({"node": node_info, "message": result.get("message")})
            continue
        node = result["objects"][0]
        for key in row.get("keyframes", []):
            value = key.get("value") or [0, 0, 0]
            set_transform_key(
                runtime, node, frame=key.get("frame", 0), property_name=key.get("property", "position"), value=value
            )
            if key.get("interpolation"):
                set_interpolation(node, interpolation=key["interpolation"], frames=[key.get("frame", 0)])
            changed += 1
    if errors:
        return anim_error("Imported animation curves with errors", changed_key_count=changed, errors=errors)
    return anim_success("Imported animation curves", changed_key_count=changed)


def _keys(node: Any) -> List[Dict[str, Any]]:
    keys = getattr(node, "keyframes", None)
    if keys is None:
        keys = []
        setattr(node, "keyframes", keys)
    return keys


def _transform_vector(node: Any, property_name: str) -> List[float]:
    value = getattr(node, property_name, None)
    vector = point3_to_list(value)
    if vector is None:
        vector = point3_to_list(getattr(value, "value", None))
    return vector or [0.0, 0.0, 0.0]
