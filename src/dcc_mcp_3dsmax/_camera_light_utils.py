"""Helpers for 3ds Max camera and lighting skill scripts."""

from __future__ import annotations

import math
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
    # MAXtoA exposes this factory only when its renderer plugin is loaded.
    # Callers must capability-check it and verify the native class readback.
    "arnold": ("Arnold_Light",),
}


def cam_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def cam_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def list_cameras(runtime: Any) -> Dict[str, Any]:
    """List camera nodes with common properties."""
    cameras = [camera_summary(node) for node in iter_scene_nodes(runtime) if is_camera(node, runtime=runtime)]
    return cam_success("Listed cameras", cameras=cameras, count=len(cameras))


def list_lights(runtime: Any) -> Dict[str, Any]:
    """List light nodes with common properties."""
    lights = [light_summary(node) for node in iter_scene_nodes(runtime) if is_light(node, runtime=runtime)]
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
        _set_node_position(camera, _point3(runtime, position))
    target = None
    if target_position is not None:
        _set_optional_attr(camera, "target_position", _vector(target_position))
        if camera_type == "target":
            target, target_warnings = _construct_runtime_object(runtime, ("Targetobject", "TargetObject"))
            warnings.extend(target_warnings)
            if target is not None:
                _set_name(target, "{}.Target".format(name))
                _set_node_position(target, _point3(runtime, target_position))
                _set_optional_attr(camera, "target", target)
            else:
                warnings.append("No supported target-object constructor was available")
    if focal_length is not None:
        _set_optional_attr(camera, "focalLength", float(focal_length))
        _set_optional_attr(camera, "focal_length", float(focal_length))
    _append_scene_object(runtime, camera)
    if target is not None:
        _append_scene_object(runtime, target)
    return cam_success(
        "Created camera",
        camera=camera_summary(camera),
        changed_node_count=1 + int(target is not None),
        warnings=warnings,
    )


def set_active_camera(
    runtime: Any, *, camera_name: Optional[str] = None, camera_handle: Optional[int] = None
) -> Dict[str, Any]:
    """Set active/render camera after target validation."""
    result, camera = resolve_node_object(runtime, node_name=camera_name, handle=camera_handle)
    if camera is None:
        return cam_error("Could not resolve camera target", camera=result)
    if not is_camera(camera, runtime=runtime):
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
    result, light = _create_light_with_node(
        runtime,
        name=name,
        light_type=light_type,
        position=position,
        target_position=target_position,
        intensity=intensity,
        color=color,
        shadows=shadows,
    )
    if not result.get("success") and light is not None:
        incomplete = _rollback_owned_nodes(runtime, [_owned_light(runtime, light)])
        result.setdefault("data", {}).update(_rollback_summary(incomplete))
    return result


def _create_light_with_node(
    runtime: Any,
    *,
    name: str,
    light_type: str = "omni",
    position: Optional[Sequence[float]] = None,
    target_position: Optional[Sequence[float]] = None,
    intensity: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    shadows: Optional[bool] = None,
) -> Tuple[Dict[str, Any], Optional[Any]]:
    """Create a light and retain its exact host reference for owned rollback."""
    factories = LIGHT_FACTORIES.get(light_type)
    if factories is None:
        return cam_error("Unsupported light_type", light_type=light_type), None
    light, warnings = _construct_runtime_object(runtime, factories)
    if light is None:
        return (
            cam_error("No supported light constructor was available", light_type=light_type, warnings=warnings),
            None,
        )
    _set_name(light, name)
    _set_supported_attrs(runtime, light, ("light_type",), light_type)
    _set_supported_attrs(runtime, light, ("is_light",), True)
    if position is not None:
        _set_supported_attrs(runtime, light, ("position", "pos"), _point3(runtime, position))
    if target_position is not None:
        _set_supported_attrs(runtime, light, ("target_position",), _vector(target_position))
    _set_light_properties(
        light,
        runtime=runtime,
        intensity=intensity,
        color=color,
        shadows=shadows,
        enabled=True,
    )
    summary = light_summary(light, runtime=runtime)
    if light_type == "arnold" and "arnold" not in summary["native_class"].lower():
        return (
            cam_error(
                "Renderer light factory returned an incompatible class",
                light_type=light_type,
                native_class=summary["native_class"],
                light=summary,
            ),
            light,
        )
    if intensity is not None and not _float_matches(summary["intensity"], float(intensity)):
        return (
            cam_error(
                "Light intensity readback did not match the request",
                light_type=light_type,
                requested_intensity=float(intensity),
                actual_intensity=summary["intensity"],
                light=summary,
            ),
            light,
        )
    if shadows is not None and summary["shadows"] is not bool(shadows):
        return (
            cam_error(
                "Light shadow readback did not match the request",
                light_type=light_type,
                requested_shadows=bool(shadows),
                actual_shadows=summary["shadows"],
                light=summary,
            ),
            light,
        )
    requested_color = _color(color) if color is not None else None
    if requested_color is not None and summary["color"] != requested_color:
        return (
            cam_error(
                "Light color readback did not match the request",
                light_type=light_type,
                requested_color=requested_color,
                actual_color=summary["color"],
                light=summary,
            ),
            light,
        )
    _append_scene_object(runtime, light)
    return cam_success("Created light", light=summary, changed_node_count=1, warnings=warnings), light


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
    if not is_light(light, runtime=runtime):
        return cam_error("Target node is not a light", node=node_identity(light))
    changed = _set_light_properties(
        light,
        runtime=runtime,
        enabled=enabled,
        intensity=intensity,
        color=color,
        shadows=shadows,
    )
    return cam_success(
        "Updated light properties",
        light=light_summary(light, runtime=runtime),
        changed_fields=changed,
        changed_light_count=1,
    )


def create_three_point_light_rig(
    runtime: Any,
    *,
    name_prefix: str = "Review",
    target_position: Optional[Sequence[float]] = None,
    distance: float = 100.0,
    light_type: str = "omni",
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
    owned_lights = []
    errors = []
    for name, position, intensity, color in specs:
        result, node = _create_light_with_node(
            runtime,
            name=name,
            light_type=light_type,
            position=position,
            target_position=target,
            intensity=intensity,
            color=color,
            shadows=True,
        )
        if node is not None:
            owned_lights.append(_owned_light(runtime, node))
        if not result.get("success") or node is None:
            errors.append(result)
            break
        created.append(result["data"]["light"])
    if errors:
        incomplete = _rollback_owned_nodes(runtime, owned_lights)
        return cam_error(
            "Could not create a verified three-point light rig",
            light_type=light_type,
            errors=errors,
            **_rollback_summary(incomplete),
        )
    return cam_success(
        "Created three-point light rig", light_type=light_type, lights=created, changed_node_count=len(created)
    )


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


def light_summary(node: Any, *, runtime: Any = None) -> Dict[str, Any]:
    """Return common light properties."""
    native_classes = _runtime_class_names(runtime, node)
    native_class = native_classes[0] if native_classes else _node_kind(node, default="light")
    return {
        "node": node_identity(node),
        "type": native_class,
        "native_class": native_class,
        "position": _vector_or_none(_read_first_attr(runtime, node, ("position", "pos"))),
        "target": _target_summary(_read_first_attr(runtime, node, ("target",))),
        "target_position": _vector_or_none(_read_first_attr(runtime, node, ("target_position",))),
        "enabled": bool(_read_first_attr(runtime, node, ("enabled", "on"), True)),
        "intensity": _float_or_none(_read_first_attr(runtime, node, ("multiplier", "intensity"))),
        "color": _color_or_none(_read_first_attr(runtime, node, ("color",))),
        "shadows": bool(_read_first_attr(runtime, node, ("castShadows", "cast_shadows", "shadows"), False)),
    }


def is_camera(node: Any, *, runtime: Any = None) -> bool:
    """Best-effort camera detection."""
    if bool(getattr(node, "is_camera", False)):
        return True
    if _runtime_is_kind(runtime, node, "Camera"):
        return True
    text = " ".join([_node_kind(node, default=""), *_runtime_class_names(runtime, node)]).lower()
    return "camera" in text


def is_light(node: Any, *, runtime: Any = None) -> bool:
    """Best-effort light detection."""
    if bool(getattr(node, "is_light", False)):
        return True
    if _runtime_is_kind(runtime, node, "Light"):
        return True
    text = " ".join([_node_kind(node, default=""), *_runtime_class_names(runtime, node)]).lower()
    return "light" in text or "spot" in text or "skylight" in text


def _runtime_is_kind(runtime: Any, node: Any, kind: str) -> bool:
    if runtime is None:
        return False
    predicate = getattr(runtime, "isKindOf", None)
    target = getattr(runtime, kind, None)
    if not callable(predicate) or target is None:
        return False
    try:
        return bool(predicate(node, target))
    except Exception:  # noqa: BLE001
        return False


def _runtime_class_names(runtime: Any, node: Any) -> List[str]:
    if runtime is None:
        return []
    names = []
    for method_name in ("classOf", "superClassOf"):
        method = getattr(runtime, method_name, None)
        if not callable(method):
            continue
        try:
            names.append(str(method(node)))
        except Exception:  # noqa: BLE001
            continue
    return names


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
    runtime: Any = None,
    enabled: Optional[bool] = None,
    intensity: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    shadows: Optional[bool] = None,
) -> List[str]:
    changed = []
    if enabled is not None:
        _set_supported_attrs(runtime, light, ("enabled", "on"), bool(enabled))
        changed.append("enabled")
    if intensity is not None:
        _set_supported_attrs(runtime, light, ("multiplier", "intensity"), float(intensity))
        changed.append("intensity")
    if color is not None:
        _set_supported_attrs(runtime, light, ("color",), _runtime_color(runtime, color))
        changed.append("color")
    if shadows is not None:
        _set_supported_attrs(runtime, light, ("castShadows", "cast_shadows", "shadows"), bool(shadows))
        changed.append("shadows")
    return changed


def _set_supported_attrs(runtime: Any, node: Any, attrs: Sequence[str], value: Any) -> bool:
    """Set aliases supported by the host, or all aliases on plain test doubles."""
    property_checker = getattr(runtime, "isProperty", None)
    host_has_property_contract = callable(property_checker)
    changed = False
    for attr in attrs:
        if host_has_property_contract and not _runtime_has_property(runtime, node, attr):
            continue
        try:
            setattr(node, attr, value)
            changed = True
        except Exception:  # noqa: BLE001 - readback validation is the fail-closed boundary.
            continue
    return changed


def _runtime_has_property(runtime: Any, node: Any, attr: str) -> bool:
    checker = getattr(runtime, "isProperty", None)
    if not callable(checker):
        return False
    try:
        name_factory = getattr(runtime, "Name", None)
        name = name_factory(attr) if callable(name_factory) else attr
        return bool(checker(node, name))
    except Exception:  # noqa: BLE001 - host property probing must fail closed.
        return False


def _read_first_attr(runtime: Any, node: Any, attrs: Sequence[str], default: Any = None) -> Any:
    property_checker = getattr(runtime, "isProperty", None)
    for attr in attrs:
        if callable(property_checker) and not _runtime_has_property(runtime, node, attr):
            continue
        try:
            return getattr(node, attr)
        except Exception:  # noqa: BLE001 - pymxs wrappers reject unknown properties.
            continue
    return default


def _runtime_color(runtime: Any, value: Sequence[int]) -> Any:
    channels = _color(value)
    for factory_name in ("color", "Color"):
        factory = getattr(runtime, factory_name, None)
        if not callable(factory):
            continue
        try:
            return factory(channels[0], channels[1], channels[2])
        except Exception:  # noqa: BLE001 - the readback check catches incompatible values.
            continue
    return channels


def _append_scene_object(runtime: Any, node: Any) -> None:
    try:
        objects = runtime.objects
        if isinstance(objects, list) and node not in objects:
            objects.append(node)
    except Exception:  # noqa: BLE001
        pass


def _delete_node(runtime: Any, node: Any) -> None:
    try:
        delete = getattr(runtime, "delete", None)
    except Exception:  # noqa: BLE001 - rollback verification remains authoritative.
        delete = None
    if callable(delete):
        try:
            delete(node)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        runtime.objects.remove(node)
    except Exception:  # noqa: BLE001 - rollback verification remains authoritative.
        pass


def _owned_light(runtime: Any, node: Any) -> Tuple[Any, Optional[int], Dict[str, Any]]:
    """Capture the raw node, immutable handle, and diagnostics before deletion."""
    try:
        handle = int(getattr(node, "handle"))
    except (AttributeError, TypeError, ValueError):
        handle = None
    return node, handle, light_summary(node, runtime=runtime)


def _rollback_owned_nodes(
    runtime: Any,
    owned_lights: Sequence[Tuple[Any, Optional[int], Dict[str, Any]]],
) -> List[Tuple[Any, Optional[int], Dict[str, Any]]]:
    """Delete exact owned references and return nodes whose absence is unproven."""
    for node, _handle, _summary in owned_lights:
        _delete_node(runtime, node)

    incomplete = []
    for owned_light in owned_lights:
        _node, handle, _summary = owned_light
        if not _node_handle_is_absent(runtime, handle):
            incomplete.append(owned_light)
    return incomplete


def _node_handle_is_absent(runtime: Any, handle: Optional[int]) -> bool:
    if handle is None:
        return False
    try:
        max_ops = getattr(runtime, "maxOps", None)
        get_node_by_handle = getattr(max_ops, "getNodeByHandle", None)
    except Exception:  # noqa: BLE001 - unverifiable deletion must fail closed.
        return False
    if not callable(get_node_by_handle):
        return False
    try:
        return get_node_by_handle(handle) is None
    except Exception:  # noqa: BLE001 - unverifiable deletion must fail closed.
        return False


def _rollback_summary(
    incomplete: Sequence[Tuple[Any, Optional[int], Dict[str, Any]]],
) -> Dict[str, Any]:
    return {
        "rolled_back": not incomplete,
        "changed_node_count": len(incomplete),
        "lights": [summary for _node, _handle, summary in incomplete],
        "rollback_incomplete_handles": [handle for _node, handle, _summary in incomplete if handle is not None],
    }


def _set_name(node: Any, name: str) -> None:
    _set_optional_attr(node, "name", str(name))


def _set_node_position(node: Any, value: Any) -> None:
    _set_optional_attr(node, "position", value)
    _set_optional_attr(node, "pos", value)


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
    try:
        return _color([value.r, value.g, value.b])
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        return _color([value.red, value.green, value.blue])
    except (AttributeError, TypeError, ValueError):
        pass
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


def _float_matches(actual: Optional[float], expected: float) -> bool:
    return actual is not None and math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6)


def _node_kind(node: Any, *, default: str) -> str:
    return str(
        getattr(
            node,
            "className",
            getattr(node, "camera_type", getattr(node, "light_type", type(node).__name__)),
        )
        or default
    )
