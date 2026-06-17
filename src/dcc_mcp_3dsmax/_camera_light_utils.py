"""Helpers for 3ds Max camera and lighting skill scripts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes, node_identity, point3_to_list, resolve_node_object

CAMERA_FACTORIES: Dict[str, Tuple[str, ...]] = {
    "target": ("Targetcamera", "TargetCamera"),
    "free": ("FreeCamera", "Freecamera"),
    "physical": ("PhysicalCamera",),
}

LIGHT_FACTORIES: Dict[str, Tuple[str, ...]] = {
    "omni": ("OmniLight", "Omnilight"),
    "spot": ("FreeSpot", "TargetSpot"),
    "directional": ("DirectionalLight", "TargetDirectionalLight"),
    "skylight": ("Skylight", "SkyLight"),
}


def cam_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def cam_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def list_cameras(runtime: Any) -> Dict[str, Any]:
    """List camera nodes with common properties."""
    cameras = [camera_summary(node) for node in iter_scene_nodes(runtime) if is_camera(node)]
    return cam_success("Listed cameras", cameras=cameras, count=len(cameras))


def list_lights(runtime: Any) -> Dict[str, Any]:
    """List light nodes with common properties."""
    lights = [light_summary(node) for node in iter_scene_nodes(runtime) if is_light(node)]
    return cam_success("Listed lights", lights=lights, count=len(lights))


def create_camera(
    runtime: Any,
    *,
    name: str,
    camera_type: str = "target",
    position: Optional[Sequence[float]] = None,
    target_position: Optional[Sequence[float]] = None,
    focal_length: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a host-native camera node."""
    factories = CAMERA_FACTORIES.get(camera_type)
    if factories is None:
        return cam_error("Unsupported camera_type", camera_type=camera_type)
    camera, warnings = _construct_runtime_object(runtime, factories)
    if camera is None:
        return cam_error("No supported camera constructor was available", camera_type=camera_type, warnings=warnings)
    _set_name(camera, name)
    _set_optional_attr(camera, "camera_type", camera_type)
    _set_optional_attr(camera, "is_camera", True)
    if position is not None:
        _set_optional_attr(camera, "position", _point3(runtime, position))
    if target_position is not None:
        _set_optional_attr(camera, "target_position", _vector(target_position))
    if focal_length is not None:
        _set_optional_attr(camera, "focalLength", float(focal_length))
        _set_optional_attr(camera, "focal_length", float(focal_length))
    _append_scene_object(runtime, camera)
    return cam_success("Created camera", camera=camera_summary(camera), changed_node_count=1, warnings=warnings)


def set_active_camera(
    runtime: Any, *, camera_name: Optional[str] = None, camera_handle: Optional[int] = None
) -> Dict[str, Any]:
    """Set active/render camera after target validation."""
    result, camera = resolve_node_object(runtime, node_name=camera_name, handle=camera_handle)
    if camera is None:
        return cam_error("Could not resolve camera target", camera=result)
    if not is_camera(camera):
        return cam_error("Target node is not a camera", node=node_identity(camera))
    viewport = getattr(runtime, "viewport", None)
    setter = getattr(viewport, "setCamera", None) if viewport is not None else None
    if callable(setter):
        try:
            setter(camera)
        except Exception:  # noqa: BLE001
            pass
    _set_optional_attr(runtime, "activeCamera", camera)
    _set_optional_attr(runtime, "renderCamera", camera)
    _set_optional_attr(runtime, "render_camera", camera)
    return cam_success("Set active camera", camera=camera_summary(camera), changed_camera_count=1)


def create_light(
    runtime: Any,
    *,
    name: str,
    light_type: str = "omni",
    position: Optional[Sequence[float]] = None,
    target_position: Optional[Sequence[float]] = None,
    intensity: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    shadows: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create a host-native light node."""
    factories = LIGHT_FACTORIES.get(light_type)
    if factories is None:
        return cam_error("Unsupported light_type", light_type=light_type)
    light, warnings = _construct_runtime_object(runtime, factories)
    if light is None:
        return cam_error("No supported light constructor was available", light_type=light_type, warnings=warnings)
    _set_name(light, name)
    _set_optional_attr(light, "light_type", light_type)
    _set_optional_attr(light, "is_light", True)
    if position is not None:
        _set_optional_attr(light, "position", _point3(runtime, position))
    if target_position is not None:
        _set_optional_attr(light, "target_position", _vector(target_position))
    _set_light_properties(light, intensity=intensity, color=color, shadows=shadows, enabled=True)
    _append_scene_object(runtime, light)
    return cam_success("Created light", light=light_summary(light), changed_node_count=1, warnings=warnings)


def set_light_properties(
    runtime: Any,
    *,
    light_name: Optional[str] = None,
    light_handle: Optional[int] = None,
    enabled: Optional[bool] = None,
    intensity: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    shadows: Optional[bool] = None,
) -> Dict[str, Any]:
    """Set common light properties after target validation."""
    result, light = resolve_node_object(runtime, node_name=light_name, handle=light_handle)
    if light is None:
        return cam_error("Could not resolve light target", light=result)
    if not is_light(light):
        return cam_error("Target node is not a light", node=node_identity(light))
    changed = _set_light_properties(light, enabled=enabled, intensity=intensity, color=color, shadows=shadows)
    return cam_success(
        "Updated light properties", light=light_summary(light), changed_fields=changed, changed_light_count=1
    )


def create_three_point_light_rig(
    runtime: Any,
    *,
    name_prefix: str = "Review",
    target_position: Optional[Sequence[float]] = None,
    distance: float = 100.0,
) -> Dict[str, Any]:
    """Create a simple three-point light rig with fallback errors."""
    target = _vector(target_position or [0.0, 0.0, 0.0])
    specs = [
        (
            "{}_key".format(name_prefix),
            [target[0] - distance, target[1] - distance, target[2] + distance],
            1.0,
            [255, 244, 230],
        ),
        (
            "{}_fill".format(name_prefix),
            [target[0] + distance, target[1] - distance * 0.6, target[2] + distance * 0.5],
            0.35,
            [190, 210, 255],
        ),
        (
            "{}_rim".format(name_prefix),
            [target[0], target[1] + distance, target[2] + distance * 0.8],
            0.65,
            [255, 255, 255],
        ),
    ]
    created = []
    errors = []
    for name, position, intensity, color in specs:
        result = create_light(
            runtime,
            name=name,
            light_type="omni",
            position=position,
            target_position=target,
            intensity=intensity,
            color=color,
            shadows=True,
        )
        if result.get("success"):
            created.append(result["data"]["light"])
        else:
            errors.append(result)
    if errors:
        return cam_error(
            "Created three-point light rig with errors", lights=created, errors=errors, changed_node_count=len(created)
        )
    return cam_success("Created three-point light rig", lights=created, changed_node_count=len(created))


def camera_summary(node: Any) -> Dict[str, Any]:
    """Return common camera properties."""
    return {
        "node": node_identity(node),
        "type": _node_kind(node, default="camera"),
        "position": _vector_or_none(getattr(node, "position", None)),
        "target": _target_summary(getattr(node, "target", None)),
        "target_position": _vector_or_none(getattr(node, "target_position", None)),
        "focal_length": _float_or_none(getattr(node, "focalLength", getattr(node, "focal_length", None))),
        "enabled": bool(getattr(node, "enabled", True)),
    }


def light_summary(node: Any) -> Dict[str, Any]:
    """Return common light properties."""
    return {
        "node": node_identity(node),
        "type": _node_kind(node, default="light"),
        "position": _vector_or_none(getattr(node, "position", None)),
        "target": _target_summary(getattr(node, "target", None)),
        "target_position": _vector_or_none(getattr(node, "target_position", None)),
        "enabled": bool(getattr(node, "enabled", True)),
        "intensity": _float_or_none(getattr(node, "multiplier", getattr(node, "intensity", None))),
        "color": _color_or_none(getattr(node, "color", None)),
        "shadows": bool(getattr(node, "castShadows", getattr(node, "shadows", False))),
    }


def is_camera(node: Any) -> bool:
    """Best-effort camera detection."""
    if bool(getattr(node, "is_camera", False)):
        return True
    text = _node_kind(node, default="").lower()
    return "camera" in text


def is_light(node: Any) -> bool:
    """Best-effort light detection."""
    if bool(getattr(node, "is_light", False)):
        return True
    text = _node_kind(node, default="").lower()
    return "light" in text or "spot" in text or "skylight" in text


def _construct_runtime_object(runtime: Any, factories: Sequence[str]) -> Tuple[Optional[Any], List[str]]:
    warnings = []
    for name in factories:
        factory = getattr(runtime, name, None)
        if not callable(factory):
            continue
        try:
            return factory(), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not create {}: {}".format(name, exc))
    return None, warnings


def _set_light_properties(
    light: Any,
    *,
    enabled: Optional[bool] = None,
    intensity: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    shadows: Optional[bool] = None,
) -> List[str]:
    changed = []
    if enabled is not None:
        _set_optional_attr(light, "enabled", bool(enabled))
        changed.append("enabled")
    if intensity is not None:
        _set_optional_attr(light, "multiplier", float(intensity))
        _set_optional_attr(light, "intensity", float(intensity))
        changed.append("intensity")
    if color is not None:
        _set_optional_attr(light, "color", _color(color))
        changed.append("color")
    if shadows is not None:
        _set_optional_attr(light, "castShadows", bool(shadows))
        _set_optional_attr(light, "shadows", bool(shadows))
        changed.append("shadows")
    return changed


def _append_scene_object(runtime: Any, node: Any) -> None:
    try:
        objects = runtime.objects
        if isinstance(objects, list) and node not in objects:
            objects.append(node)
    except Exception:  # noqa: BLE001
        pass


def _set_name(node: Any, name: str) -> None:
    _set_optional_attr(node, "name", str(name))


def _set_optional_attr(node: Any, attr: str, value: Any) -> None:
    try:
        setattr(node, attr, value)
    except Exception:  # noqa: BLE001
        pass


def _point3(runtime: Any, value: Sequence[float]) -> Any:
    vector = _vector(value)
    factory = getattr(runtime, "Point3", None)
    if callable(factory):
        try:
            return factory(vector[0], vector[1], vector[2])
        except Exception:  # noqa: BLE001
            pass
    return vector


def _vector(value: Sequence[float]) -> List[float]:
    if len(value) < 3:
        raise ValueError("Vector values require at least three numbers")
    return [float(value[0]), float(value[1]), float(value[2])]


def _color(value: Sequence[int]) -> List[int]:
    if len(value) < 3:
        raise ValueError("Color values require at least three channels")
    return [max(0, min(255, int(value[0]))), max(0, min(255, int(value[1]))), max(0, min(255, int(value[2])))]


def _vector_or_none(value: Any) -> Optional[List[float]]:
    return point3_to_list(value) or point3_to_list(getattr(value, "value", None))


def _color_or_none(value: Any) -> Optional[List[int]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
        return _color(value)
    return None


def _target_summary(target: Any) -> Optional[Dict[str, Any]]:
    return node_identity(target) if target is not None else None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _node_kind(node: Any, *, default: str) -> str:
    return str(
        getattr(
            node,
            "className",
            getattr(node, "camera_type", getattr(node, "light_type", type(node).__name__)),
        )
        or default
    )
