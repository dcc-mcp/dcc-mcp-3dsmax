"""Typed, readback-verified rig control creation."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._rigging_utils import create_helper_node, rig_error, rig_success
from dcc_mcp_3dsmax._scene_utils import node_identity, point3_to_list, resolve_node_object


def create_control(
    runtime: Any,
    *,
    name: str,
    shape: str = "circle",
    size: float = 10.0,
    position: Optional[Sequence[float]] = None,
    color: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Create a bounded helper control and verify transform and display color."""
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        return rig_error("Control name must contain 1 to 128 characters")
    if shape not in {"circle", "point"}:
        return rig_error("Unsupported control shape", shape=shape)
    if not isinstance(size, (int, float)) or isinstance(size, bool) or not math.isfinite(float(size)):
        return rig_error("Control size must be a finite number")
    if not 0 < float(size) <= 1_000_000:
        return rig_error("Control size is outside the supported bound")
    try:
        if runtime.getNodeByName(name) is not None:
            return rig_error("A scene node already uses the requested control name", name=name)
    except Exception:  # noqa: BLE001
        pass
    try:
        position_value = _vector(position or [0.0, 0.0, 0.0])
        color_value = _control_color(color or [255, 255, 0])
    except (TypeError, ValueError) as exc:
        return rig_error("Invalid control transform or color", reason=str(exc))

    created = create_helper_node(
        runtime,
        name=name,
        helper_type=shape,
        size=float(size),
        position=position_value,
    )
    if not created.get("success"):
        return created
    _resolved, node = resolve_node_object(runtime, node_name=name)
    if node is None:
        return rig_error("Created control could not be read back", name=name)
    size_attr = "radius" if shape == "circle" else "size"
    try:
        setattr(node, size_attr, float(size))
    except Exception as exc:  # noqa: BLE001
        _remove_scene_object(runtime, node)
        return rig_error("Could not set control size", reason=str(exc), rolled_back=True)
    factory = getattr(runtime, "color", None)
    native_color = factory(*color_value) if callable(factory) else list(color_value)
    try:
        node.wirecolor = native_color
    except Exception as exc:  # noqa: BLE001
        _remove_scene_object(runtime, node)
        return rig_error("Could not set control display color", reason=str(exc), rolled_back=True)
    actual_position = point3_to_list(getattr(node, "position", None))
    actual_color = _control_color_components(getattr(node, "wirecolor", None))
    try:
        actual_size = float(getattr(node, size_attr))
    except (AttributeError, TypeError, ValueError):
        actual_size = -1.0
    if (
        actual_position != position_value
        or actual_color != color_value
        or not math.isclose(actual_size, float(size), rel_tol=1e-6, abs_tol=1e-6)
    ):
        _remove_scene_object(runtime, node)
        return rig_error("Control host readback did not match the request", rolled_back=True)
    return rig_success(
        "Created and verified rig control",
        control=node_identity(node),
        shape=shape,
        size=float(size),
        position=position_value,
        color=color_value,
        changed_node_count=1,
    )


def _remove_scene_object(runtime: Any, node: Any) -> None:
    delete = getattr(runtime, "delete", None)
    if callable(delete):
        try:
            delete(node)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        if isinstance(runtime.objects, list) and node in runtime.objects:
            runtime.objects.remove(node)
    except Exception:  # noqa: BLE001
        pass


def _vector(value: Sequence[float]) -> List[float]:
    if len(value) != 3:
        raise ValueError("Control position requires exactly three numbers")
    vector = [float(item) for item in value]
    if not all(math.isfinite(item) for item in vector):
        raise ValueError("Control position must contain finite numbers")
    return vector


def _control_color(value: Sequence[int]) -> List[int]:
    if len(value) != 3:
        raise ValueError("Control color requires exactly three components")
    components = []
    for component in value:
        if not isinstance(component, int) or isinstance(component, bool) or not 0 <= component <= 255:
            raise ValueError("Control color components must be integers from 0 through 255")
        components.append(component)
    return components


def _control_color_components(value: Any) -> Optional[List[int]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
        try:
            return [int(value[0]), int(value[1]), int(value[2])]
        except (TypeError, ValueError):
            return None
    for attrs in (("r", "g", "b"), ("R", "G", "B")):
        try:
            return [int(getattr(value, attr)) for attr in attrs]
        except (AttributeError, TypeError, ValueError):
            continue
    return None
