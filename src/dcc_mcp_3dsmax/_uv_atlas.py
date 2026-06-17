"""Helpers for 3ds Max UV and texture-atlas skill scripts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._mesh_ops import add_modifier
from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_objects


def uv_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def uv_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def resolve_uv_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Resolve explicit UV targets or an explicitly requested selection."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001
            selected = []
        if not selected:
            return uv_error("Current selection is empty", objects=[])
        return {"success": True, "message": "Resolved selected nodes", "objects": selected}
    result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
    if not result.get("success"):
        return uv_error(result["message"], errors=result.get("errors", []), objects=[])
    result["status"] = "success"
    return result


def validate_channel(channel: int) -> Optional[Dict[str, Any]]:
    """Validate a 3ds Max map channel number."""
    if int(channel) < 1 or int(channel) > 99:
        return uv_error("UV channel must be between 1 and 99", channel=channel)
    return None


def channel_summary(runtime: Any, node: Any) -> Dict[str, Any]:
    """Return UV/map channel summaries for one node."""
    channels = _channels(node)
    rows = []
    for channel, data in sorted(channels.items()):
        rows.append(
            {
                "channel": int(channel),
                "present": True,
                "uv_count": int(data.get("uv_count", 0)),
                "face_count": int(data.get("face_count", 0)),
                "shell_count": int(data.get("shell_count", len(data.get("shells", [])))),
            }
        )
    if not rows:
        rows = _runtime_channel_rows(runtime, node)
    return {"node": node_identity(node), "channels": rows, "count": len(rows)}


def shell_summary(node: Any, channel: int) -> Dict[str, Any]:
    """Return shell/element summary data for one UV channel."""
    data = _channels(node).get(int(channel), {})
    shells = list(data.get("shells", []))
    return {
        "node": node_identity(node),
        "channel": int(channel),
        "shell_count": int(data.get("shell_count", len(shells))),
        "shells": shells,
    }


def create_channel(runtime: Any, node: Any, channel: int) -> List[str]:
    """Create or enable one UV channel."""
    warnings = _set_map_support(runtime, node, channel, True)
    channels = _channels(node)
    channels.setdefault(int(channel), {"uv_count": 0, "face_count": 0, "shells": []})
    return warnings


def delete_channel(runtime: Any, node: Any, channel: int) -> List[str]:
    """Delete or disable one UV channel."""
    warnings = _set_map_support(runtime, node, channel, False)
    _channels(node).pop(int(channel), None)
    return warnings


def copy_channel(runtime: Any, node: Any, source_channel: int, target_channel: int) -> List[str]:
    """Copy UV data between channels."""
    channels = _channels(node)
    if int(source_channel) not in channels:
        return ["Source UV channel {} does not exist on {}".format(source_channel, getattr(node, "name", "<node>"))]
    poly_op = getattr(runtime, "polyOp", None)
    copier = getattr(poly_op, "copyMapChannel", None) if poly_op is not None else None
    warnings = []
    if callable(copier):
        try:
            copier(node, int(source_channel), int(target_channel))
        except Exception as exc:  # noqa: BLE001
            warnings.append("Host copyMapChannel failed: {}".format(exc))
    channels[int(target_channel)] = deepcopy(channels[int(source_channel)])
    return warnings


def apply_projection(
    runtime: Any,
    node: Any,
    *,
    channel: int,
    projection: str,
    length: Optional[float],
    width: Optional[float],
    height: Optional[float],
) -> List[str]:
    """Apply a UVW Map projection modifier."""
    modifier, warnings = add_modifier(runtime, node, ("UVWMap", "UVW_Map"), mapChannel=int(channel))
    if modifier is None:
        return warnings + ["No UVW Map modifier was available"]
    for key, value in (("maptype", projection), ("length", length), ("width", width), ("height", height)):
        if value is None:
            continue
        try:
            setattr(modifier, key, value)
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not set projection attribute {}: {}".format(key, exc))
    create_channel(runtime, node, channel)
    return warnings


def apply_unwrap(runtime: Any, node: Any, *, channel: int, operation: str, padding: Optional[float]) -> List[str]:
    """Apply Unwrap UVW operations for unwrap, pack, or normalize."""
    modifier, warnings = add_modifier(runtime, node, ("Unwrap_UVW", "UnwrapUVW"), mapChannel=int(channel))
    if modifier is None:
        return warnings + ["No Unwrap UVW modifier was available"]
    method = getattr(modifier, operation, None)
    if callable(method):
        try:
            if padding is None:
                method()
            else:
                method(padding)
        except Exception as exc:  # noqa: BLE001
            warnings.append("Unwrap operation {} failed: {}".format(operation, exc))
    create_channel(runtime, node, channel)
    return warnings


def overlap_summary(node: Any, channel: int) -> Dict[str, Any]:
    """Return UV overlap summary data."""
    overlaps = getattr(node, "uv_overlaps", {})
    channel_overlaps = list(overlaps.get(int(channel), [])) if isinstance(overlaps, dict) else []
    return {
        "node": node_identity(node),
        "channel": int(channel),
        "overlap_count": len(channel_overlaps),
        "overlaps": channel_overlaps,
    }


def atlas_plan(nodes: Sequence[Any]) -> Dict[str, Any]:
    """Collect material and bitmap usage for a texture-atlas preparation plan."""
    entries = []
    bitmap_paths = []
    for node in nodes:
        material = getattr(node, "material", None)
        material_name = str(getattr(material, "name", "")) if material is not None else None
        bitmaps = _bitmap_paths(material)
        bitmap_paths.extend(bitmaps)
        entries.append({"node": node_identity(node), "material": material_name, "bitmaps": bitmaps})
    unique_bitmaps = sorted({path for path in bitmap_paths if path})
    return {
        "nodes": entries,
        "node_count": len(entries),
        "bitmap_paths": unique_bitmaps,
        "bitmap_count": len(unique_bitmaps),
        "can_bake": False,
        "plan": "collect_material_bitmaps",
    }


def changed_channels(runtime: Any, nodes: Sequence[Any]) -> List[Dict[str, Any]]:
    """Return channel summaries for changed nodes."""
    return [channel_summary(runtime, node) for node in nodes]


def _channels(node: Any) -> Dict[int, Dict[str, Any]]:
    channels = getattr(node, "uv_channels", None)
    if channels is None:
        channels = {}
        try:
            setattr(node, "uv_channels", channels)
        except Exception:  # noqa: BLE001
            return {}
    return channels


def _set_map_support(runtime: Any, node: Any, channel: int, enabled: bool) -> List[str]:
    poly_op = getattr(runtime, "polyOp", None)
    setter = getattr(poly_op, "setMapSupport", None) if poly_op is not None else None
    if callable(setter):
        try:
            setter(node, int(channel), bool(enabled))
            return []
        except Exception as exc:  # noqa: BLE001
            return ["Host setMapSupport failed: {}".format(exc)]
    return []


def _runtime_channel_rows(runtime: Any, node: Any) -> List[Dict[str, Any]]:
    poly_op = getattr(runtime, "polyOp", None)
    getter = getattr(poly_op, "getNumMaps", None) if poly_op is not None else None
    if not callable(getter):
        return []
    try:
        count = int(getter(node))
    except Exception:  # noqa: BLE001
        return []
    return [
        {"channel": index, "present": True, "uv_count": 0, "face_count": 0, "shell_count": 0}
        for index in range(1, count + 1)
    ]


def _bitmap_paths(material: Any) -> List[str]:
    if material is None:
        return []
    direct = getattr(material, "bitmap_paths", None)
    if direct is not None:
        return [str(path) for path in direct]
    paths = []
    for attr in ("diffuseMap", "bumpMap", "opacityMap", "maps"):
        value = getattr(material, attr, None)
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            filename = getattr(item, "filename", None)
            if filename:
                paths.append(str(filename))
    return paths
