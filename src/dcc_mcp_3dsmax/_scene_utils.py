"""Small helpers shared by 3ds Max skill scripts."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional, Tuple


def coerce_vector3(value: Any, name: str) -> Optional[List[float]]:
    """Normalize a 3D vector from ``[x, y, z]`` or ``{"x": ..., ...}``."""
    if value is None:
        return None

    if isinstance(value, Mapping):
        raw = [value.get("x"), value.get("y"), value.get("z")]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raw = list(value)
    else:
        raise ValueError("{} must be an object with x/y/z or a 3-item array".format(name))

    if len(raw) != 3 or any(item is None for item in raw):
        raise ValueError("{} must contain exactly x, y, and z values".format(name))

    try:
        return [float(raw[0]), float(raw[1]), float(raw[2])]
    except (TypeError, ValueError) as exc:
        raise ValueError("{} values must be numbers".format(name)) from exc


def make_point3(runtime: Any, value: Any, name: str = "position") -> Optional[Any]:
    """Create a pymxs Point3 from user input."""
    vector = coerce_vector3(value, name)
    if vector is None:
        return None
    return runtime.Point3(vector[0], vector[1], vector[2])


def set_node_position(runtime: Any, node: Any, position: Any) -> Optional[List[float]]:
    """Apply a position to a 3ds Max node and return the normalized vector."""
    vector = coerce_vector3(position, "position")
    if vector is None:
        return None
    node.pos = runtime.Point3(vector[0], vector[1], vector[2])
    return vector


def json_safe(value: Any, depth: int = 0) -> Any:
    """Best-effort conversion of pymxs/Python values into JSON-safe data."""
    if depth > 4:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [json_safe(item, depth + 1) for item in value]

    identity = node_identity(value)
    if identity["node_name"] or identity["object_id"] is not None:
        return identity
    return str(value)


def node_identity(node: Any) -> Dict[str, Any]:
    """Return a JSON-safe identity payload for a 3ds Max node-like object."""
    handle = getattr(node, "handle", None)
    try:
        object_id = int(handle) if handle is not None else None
    except (TypeError, ValueError):
        object_id = None
    parent = getattr(node, "parent", None)
    class_name = getattr(getattr(node, "baseObject", None), "__class__", None)
    payload = {
        "node_name": str(getattr(node, "name", "")),
        "object_id": object_id,
        "class_name": getattr(class_name, "__name__", type(node).__name__),
        "parent": str(getattr(parent, "name", "")) if parent is not None else None,
        "visible": node_visible(node),
    }
    return payload


def iter_scene_nodes(runtime: Any) -> List[Any]:
    """Return scene nodes from ``runtime.objects`` as a plain list."""
    try:
        return list(runtime.objects)
    except Exception:  # noqa: BLE001
        return []


def _coerce_handle(handle: Optional[int]) -> Optional[int]:
    if handle is None:
        return None
    try:
        return int(handle)
    except (TypeError, ValueError):
        return None


def resolve_node(runtime: Any, *, node_name: Optional[str] = None, handle: Optional[int] = None) -> Dict[str, Any]:
    """Resolve one node by name or handle and return a structured envelope."""
    if not node_name and handle is None:
        return {"success": False, "message": "node_name or handle is required", "matches": []}

    matches: List[Any] = []
    all_nodes = iter_scene_nodes(runtime)
    if node_name:
        matches.extend(node for node in all_nodes if str(getattr(node, "name", "")) == str(node_name))
        if not matches:
            try:
                node = runtime.getNodeByName(node_name)
            except Exception:  # noqa: BLE001
                node = None
            if node is not None:
                matches.append(node)

    if handle is not None:
        wanted = _coerce_handle(handle)
        resolved = []
        for node in all_nodes:
            try:
                if int(getattr(node, "handle")) == wanted:
                    resolved.append(node)
            except Exception:  # noqa: BLE001
                continue
        matches = resolved

    identities = [node_identity(node) for node in matches]
    if not identities:
        return {"success": False, "message": "No matching node found", "matches": []}
    if len(identities) > 1:
        return {"success": False, "message": "Node reference is ambiguous", "matches": identities}
    return {"success": True, "message": "Resolved node", "node": identities[0], "matches": identities}


def resolve_node_object(
    runtime: Any, *, node_name: Optional[str] = None, handle: Optional[int] = None
) -> Tuple[Dict[str, Any], Any]:
    """Resolve one node and return both the envelope and raw node object."""
    result = resolve_node(runtime, node_name=node_name, handle=handle)
    if not result.get("success"):
        return result, None
    node_identity_payload = result["node"]
    for node in iter_scene_nodes(runtime):
        if node_identity(node) == node_identity_payload:
            return result, node
    if node_name:
        try:
            return result, runtime.getNodeByName(node_name)
        except Exception:  # noqa: BLE001
            pass
    return result, None


def resolve_node_objects(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Resolve multiple nodes by names and/or handles."""
    raw_names = list(node_names or [])
    raw_handles = list(handles or [])
    if not raw_names and not raw_handles:
        return {"success": False, "message": "node_names or handles is required", "nodes": [], "objects": []}

    nodes: List[Any] = []
    errors: List[Dict[str, Any]] = []
    for name in raw_names:
        result, node = resolve_node_object(runtime, node_name=str(name))
        if node is None:
            errors.append(
                {"node_name": str(name), "message": result.get("message"), "matches": result.get("matches", [])}
            )
        else:
            nodes.append(node)
    for handle in raw_handles:
        result, node = resolve_node_object(runtime, handle=_coerce_handle(handle))
        if node is None:
            errors.append({"handle": handle, "message": result.get("message"), "matches": result.get("matches", [])})
        else:
            nodes.append(node)

    if errors:
        return {
            "success": False,
            "message": "One or more node references could not be resolved",
            "errors": errors,
            "objects": [],
        }
    return {
        "success": True,
        "message": "Resolved nodes",
        "nodes": [node_identity(node) for node in nodes],
        "objects": nodes,
    }


def node_visible(node: Any) -> bool:
    """Best-effort visibility state for a 3ds Max node."""
    hidden = getattr(node, "isHidden", None)
    if callable(hidden):
        try:
            hidden = hidden()
        except Exception:  # noqa: BLE001
            hidden = None
    if isinstance(hidden, bool):
        return not hidden
    visibility = getattr(node, "visibility", None)
    if isinstance(visibility, (int, float)):
        return visibility > 0
    return True


def set_node_visible(runtime: Any, node: Any, visible: bool) -> None:
    """Set node visibility using properties first, then runtime helpers."""
    try:
        setattr(node, "isHidden", not visible)
        return
    except Exception:  # noqa: BLE001
        pass
    helper = getattr(runtime, "unhide", None) if visible else getattr(runtime, "hide", None)
    if callable(helper):
        helper(node)


def node_bounding_box(node: Any) -> Dict[str, Any]:
    """Serialize a node bounding box from min/max-like attributes."""
    min_value = getattr(node, "min", None)
    max_value = getattr(node, "max", None)
    return {
        "node": node_identity(node),
        "min": point3_to_list(min_value),
        "max": point3_to_list(max_value),
    }


def point3_to_list(value: Any) -> Optional[List[float]]:
    """Serialize a Point3-like value into floats."""
    if value is None:
        return None
    for attrs in (("x", "y", "z"), ("X", "Y", "Z")):
        try:
            return [float(getattr(value, attrs[0])), float(getattr(value, attrs[1])), float(getattr(value, attrs[2]))]
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
        try:
            return [float(value[0]), float(value[1]), float(value[2])]
        except (TypeError, ValueError):
            return None
    return None


def is_camera_node(node: Any) -> bool:
    """Best-effort camera detection."""
    if bool(getattr(node, "is_camera", False)):
        return True
    text = " ".join(
        [
            type(node).__name__,
            type(getattr(node, "baseObject", None)).__name__,
            str(getattr(node, "className", "")),
        ]
    ).lower()
    return "camera" in text


def runtime_symbol_info(runtime: Any, name: str) -> Dict[str, Any]:
    """Return safe metadata for one runtime symbol."""
    try:
        value = getattr(runtime, name)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "available": False, "error": str(exc)}

    signature = None
    try:
        signature = str(inspect.signature(value)) if callable(value) else None
    except (TypeError, ValueError):
        signature = None

    doc = getattr(value, "__doc__", None)
    if isinstance(doc, str):
        doc = doc.strip().splitlines()[0] if doc.strip() else None
    return {
        "name": name,
        "available": True,
        "type": type(value).__name__,
        "callable": callable(value),
        "signature": signature,
        "doc": doc,
    }
