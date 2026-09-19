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


def is_camera_node(node: Any, *, runtime: Any = None) -> bool:
    """Best-effort camera detection."""
    if bool(getattr(node, "is_camera", False)):
        return True
    runtime_names = []
    if runtime is not None:
        predicate = getattr(runtime, "isKindOf", None)
        camera_class = getattr(runtime, "Camera", None)
        if callable(predicate) and camera_class is not None:
            try:
                if bool(predicate(node, camera_class)):
                    return True
            except Exception:  # noqa: BLE001
                pass
        for method_name in ("classOf", "superClassOf"):
            method = getattr(runtime, method_name, None)
            if callable(method):
                try:
                    runtime_names.append(str(method(node)))
                except Exception:  # noqa: BLE001
                    pass
    text = " ".join(
        [
            type(node).__name__,
            type(getattr(node, "baseObject", None)).__name__,
            str(getattr(node, "className", "")),
            *runtime_names,
        ]
    ).lower()
    return "camera" in text


def euler_degrees_to_list(value: Any) -> Optional[List[float]]:
    """Serialize an EulerAngles-like value into degrees as floats."""
    return point3_to_list(value)


def color_to_list(value: Any) -> Optional[List[float]]:
    """Serialize a Color-like value (r/g/b) into floats."""
    if value is None:
        return None
    for attrs in (("r", "g", "b"), ("R", "G", "B")):
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


def quat_to_list(value: Any) -> Optional[List[float]]:
    """Serialize a Quat-like value (x/y/z/w) into floats."""
    for attrs in (("x", "y", "z", "w"), ("X", "Y", "Z", "W")):
        try:
            return [
                float(getattr(value, attrs[0])),
                float(getattr(value, attrs[1])),
                float(getattr(value, attrs[2])),
                float(getattr(value, attrs[3])),
            ]
        except Exception:  # noqa: BLE001
            pass
    return None


def matrix_rows(value: Any) -> Optional[List[List[float]]]:
    """Serialize a Matrix3-like value into its four rows as float lists."""
    if value is None:
        return None
    rows = []
    for index in range(4):
        try:
            row = value[index]
        except Exception:  # noqa: BLE001
            try:
                row = value.row(index)
            except Exception:  # noqa: BLE001
                return None
        serialized = point3_to_list(row)
        if serialized is None:
            return None
        rows.append(serialized)
    return rows


def matrix_equal(left: Any, right: Any, tolerance: float = 1e-4) -> bool:
    """Compare two Matrix3-like values row by row."""
    left_rows = matrix_rows(left)
    right_rows = matrix_rows(right)
    if left_rows is None or right_rows is None:
        return False
    for left_row, right_row in zip(left_rows, right_rows):
        for left_value, right_value in zip(left_row, right_row):
            if abs(left_value - right_value) > tolerance:
                return False
    return True


def vector_equal(left: Any, right: Any, tolerance: float = 1e-4) -> bool:
    """Compare two Point3/Color-like values component by component."""
    left_values = point3_to_list(left)
    if left_values is None:
        left_values = color_to_list(left)
    right_values = point3_to_list(right)
    if right_values is None:
        right_values = color_to_list(right)
    if left_values is None or right_values is None:
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(left_values, right_values))


def scalar_equal(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    """Compare two scalar values with a tolerance."""
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return str(left) == str(right)


def _build_scalar(current: Any, value: Any, name: str) -> Any:
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        raise ValueError("{} expects a boolean value".format(name))
    if isinstance(current, int) and not isinstance(value, bool):
        if isinstance(value, int):
            return value
        if isinstance(value, float) and float(value).is_integer():
            return int(value)
        raise ValueError("{} expects an integer value".format(name))
    if isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("{} expects a numeric value".format(name))
        return float(value)
    if isinstance(current, str):
        if not isinstance(value, str):
            raise ValueError("{} expects a string value".format(name))
        return value
    return None


def build_property_value(runtime: Any, current: Any, value: Any, name: str) -> Any:
    """Coerce a user supplied value into the shape the target property expects.

    Raises ``ValueError`` whenever the value cannot be represented in the
    target type so callers fail loudly instead of writing a wrong value.
    """
    scalar = _build_scalar(current, value, name)
    if scalar is not None:
        return scalar

    if point3_to_list(current) is not None and hasattr(current, "x"):
        vector = coerce_vector3(value, name)
        return runtime.Point3(vector[0], vector[1], vector[2])

    if color_to_list(current) is not None and hasattr(current, "r"):
        vector = coerce_vector3(value, name)
        return runtime.Color(vector[0], vector[1], vector[2])

    if quat_to_list(current) is not None:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
            raise ValueError("{} expects four components [x, y, z, w]".format(name))
        return runtime.Quat(value[0], value[1], value[2], value[3])

    return value


def serialize_property_value(value: Any) -> Any:
    """Convert one property value into JSON-safe data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    point = point3_to_list(value)
    if point is not None and hasattr(value, "x") and not hasattr(value, "w"):
        return point
    color = color_to_list(value)
    if color is not None and hasattr(value, "r"):
        return color
    quat = quat_to_list(value)
    if quat is not None:
        return quat
    rows = matrix_rows(value)
    if rows is not None:
        return rows
    return json_safe(value)


def read_property(node: Any, name: str) -> Dict[str, Any]:
    """Read one property from a node and report availability explicitly."""
    try:
        value = getattr(node, name)
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "available": False, "error": "{}: {}".format(type(exc).__name__, exc)}

    if callable(value):
        return {
            "name": name,
            "available": False,
            "callable": True,
            "error": "{} is a method, not a property".format(name),
        }

    return {"name": name, "available": True, "type": type(value).__name__, "value": serialize_property_value(value)}


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
