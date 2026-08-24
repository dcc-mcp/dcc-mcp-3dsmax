"""Helpers for 3ds Max lookdev skill scripts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from dcc_mcp_3dsmax._camera_light_utils import create_three_point_light_rig
from dcc_mcp_3dsmax._render_utils import current_renderer


def lookdev_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def lookdev_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def get_display_view_transform(
    runtime: Any,
    *,
    byref: Optional[Callable[[Any], Any]] = None,
    target: str = "FrameBuffer",
) -> Dict[str, Any]:
    """Read one native ColorPipelineMgr display/view target."""
    manager = getattr(runtime, "ColorPipelineMgr", None)
    getter = getattr(manager, "GetDefaultDisplayViewTransform", None)
    if manager is None or not callable(getter):
        return {
            "supported": False,
            "target": target,
            "automatic": None,
            "display": None,
            "view_transform": None,
            "reason": "display_view_api_unavailable",
        }
    if byref is None:
        return {
            "supported": False,
            "target": target,
            "automatic": None,
            "display": None,
            "view_transform": None,
            "reason": "pymxs_byref_unavailable",
        }
    try:
        result = getter(
            _max_name(runtime, target),
            byref(None),
            byref(None),
            byref(None),
        )
    except Exception as exc:  # noqa: BLE001 - pymxs reports host failures as RuntimeError.
        return {
            "supported": False,
            "target": target,
            "automatic": None,
            "display": None,
            "view_transform": None,
            "reason": "display_view_readback_failed",
            "error": str(exc),
        }
    if not isinstance(result, tuple) or len(result) < 4:
        return {
            "supported": False,
            "target": target,
            "automatic": None,
            "display": None,
            "view_transform": None,
            "reason": "invalid_display_view_readback",
        }
    return {
        "supported": True,
        "target": target,
        "automatic": bool(result[-3]),
        "display": str(result[-2]),
        "view_transform": str(result[-1]),
        "reason": None,
    }


def get_color_management(runtime: Any, *, byref: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
    """Read the 3ds Max 2024+ scene color-pipeline settings."""
    manager = getattr(runtime, "ColorPipelineMgr", None)
    if manager is None:
        return lookdev_error("ColorPipelineMgr is unavailable in this 3ds Max version")
    display_view = get_display_view_transform(runtime, byref=byref)
    return lookdev_success(
        "Read color management",
        mode=str(manager.Mode),
        ocio_config_path=str(manager.OCIOConfigPath),
        rendering_color_space=str(manager.RenderingColorSpace),
        data_color_space=str(manager.DataColorSpace),
        status=str(manager.Status),
        locked=bool(manager.Locked),
        display_view=display_view,
        display=display_view["display"],
        view_transform=display_view["view_transform"],
    )


def set_color_management(
    runtime: Any,
    *,
    ocio_config_path: str,
    rendering_color_space: str = "ACEScg",
    data_color_space: str = "Raw",
    lock_settings: bool = True,
    display: Optional[str] = None,
    view_transform: Optional[str] = None,
    byref: Optional[Callable[[Any], Any]] = None,
) -> Dict[str, Any]:
    """Configure the scene to use a custom OCIO config and rendering space."""
    path = Path(ocio_config_path).expanduser()
    if not path.is_file():
        return lookdev_error("OCIO config file does not exist", ocio_config_path=str(path))
    manager = getattr(runtime, "ColorPipelineMgr", None)
    if manager is None:
        return lookdev_error("ColorPipelineMgr is unavailable in this 3ds Max version")

    if (display is None) != (view_transform is None):
        return lookdev_error("display and view_transform must be supplied together")

    previous_display_view = get_display_view_transform(runtime, byref=byref)
    if display is not None and not previous_display_view["supported"]:
        return lookdev_error(
            "Cannot safely configure display/view transform without a rollback snapshot",
            reason="display_view_snapshot_unavailable",
            snapshot_reason=previous_display_view["reason"],
            mutation_started=False,
            rolled_back=False,
        )

    previous = {
        "mode": manager.Mode,
        "ocio_config_path": manager.OCIOConfigPath,
        "rendering_color_space": manager.RenderingColorSpace,
        "data_color_space": manager.DataColorSpace,
        "locked": bool(manager.Locked),
        "display_view": previous_display_view,
        "ocio_env": os.environ.get("OCIO"),
    }
    try:
        os.environ["OCIO"] = str(path)
        manager.Locked = False
        manager.Mode = _max_name(runtime, "OCIO_EnvVar")
        if manager.ReInitialize() is False:
            raise RuntimeError("ColorPipelineMgr rejected the OCIO configuration")
        manager.RenderingColorSpace = rendering_color_space
        manager.DataColorSpace = data_color_space
        if display is not None and view_transform is not None:
            resolved_display = _resolve_color_choice(manager.GetDisplayList(), display, "display")
            resolved_view = _resolve_color_choice(
                manager.GetViewList(resolved_display), view_transform, "view transform"
            )
            manager.SetDefaultDisplayViewTransform(
                _max_name(runtime, "FrameBuffer"),
                False,
                display=resolved_display,
                viewTransform=resolved_view,
            )
        manager.Locked = bool(lock_settings)
        result = get_color_management(runtime, byref=byref)
        if not result["success"]:
            raise RuntimeError(result["message"])
        data = result["data"]
        if "ocio_envvar" not in data["mode"].lower() or "normal" not in data["status"].lower():
            raise RuntimeError("OCIO color management did not become valid")
        if data["rendering_color_space"].lower() != rendering_color_space.lower():
            raise RuntimeError("Rendering color space was not applied")
        if display is not None and view_transform is not None:
            display_view = data["display_view"]
            if not display_view["supported"]:
                raise RuntimeError("Display/view transform readback is unavailable")
            if display_view["automatic"]:
                raise RuntimeError("Frame buffer display/view transform remained automatic")
            if display_view["display"].lower() != display.lower():
                raise RuntimeError("Frame buffer display was not applied")
            if display_view["view_transform"].lower() != view_transform.lower():
                raise RuntimeError("Frame buffer view transform was not applied")
    except Exception as exc:  # noqa: BLE001 - pymxs reports host failures as RuntimeError.
        rollback_error = _restore_color_management(runtime, manager, previous)
        error_data = {"error": str(exc), "rolled_back": rollback_error is None}
        if rollback_error is not None:
            error_data["rollback_error"] = rollback_error
        return lookdev_error("Failed to configure color management", **error_data)

    result["message"] = "Configured OCIO color management"
    return result


def _max_name(runtime: Any, value: str) -> Any:
    return getattr(runtime, "Name", lambda item: item)(value)


def _resolve_color_choice(values: Sequence[Any], requested: str, label: str) -> str:
    choices = [str(value) for value in values]
    for choice in choices:
        if choice.lower() == requested.lower():
            return choice
    raise ValueError("Unknown {} {!r}; available: {}".format(label, requested, ", ".join(choices)))


def _restore_color_management(runtime: Any, manager: Any, previous: Dict[str, Any]) -> Optional[str]:
    try:
        manager.Locked = False
        if previous["ocio_env"] is None:
            os.environ.pop("OCIO", None)
        else:
            os.environ["OCIO"] = previous["ocio_env"]
        manager.OCIOConfigPath = previous["ocio_config_path"]
        manager.Mode = previous["mode"]
        if manager.ReInitialize() is False:
            raise RuntimeError("ColorPipelineMgr rejected the previous configuration")
        manager.RenderingColorSpace = previous["rendering_color_space"]
        manager.DataColorSpace = previous["data_color_space"]
        display_view = previous["display_view"]
        if display_view["supported"]:
            manager.SetDefaultDisplayViewTransform(
                _max_name(runtime, display_view["target"]),
                bool(display_view["automatic"]),
                display=display_view["display"],
                viewTransform=display_view["view_transform"],
            )
        manager.Locked = previous["locked"]
    except Exception as exc:  # noqa: BLE001 - rollback must report, never mask, host failures.
        return str(exc)
    return None


def setup_hdr_lighting(
    runtime: Any,
    *,
    hdri_path: str,
    name_prefix: str = "Review",
    intensity: float = 1.0,
    rotation: float = 0.0,
    target_position: Optional[Sequence[float]] = None,
    distance: float = 100.0,
) -> Dict[str, Any]:
    """Configure HDR/environment lighting and a simple three-point rig."""
    path = Path(hdri_path).expanduser()
    if not path.exists():
        return lookdev_error("HDRI file does not exist", hdri_path=str(path))
    if not path.is_file():
        return lookdev_error("HDRI path is not a file", hdri_path=str(path))

    renderer_family = _renderer_family(runtime)
    if renderer_family == "arnold" and not callable(getattr(runtime, "Arnold_Light", None)):
        return lookdev_error(
            "Active Arnold renderer has no compatible typed light factory",
            renderer_family=renderer_family,
            failure_reason="compatible_light_factory_unavailable",
        )

    rig_light_type = "arnold" if renderer_family == "arnold" else "omni"

    bitmap = _create_bitmap(runtime, str(path))
    rig = create_three_point_light_rig(
        runtime,
        name_prefix=name_prefix,
        target_position=target_position,
        distance=distance,
        light_type=rig_light_type,
    )
    if not rig.get("success"):
        return lookdev_error(
            "Could not configure renderer-compatible HDR lighting",
            renderer_family=renderer_family,
            light_compatibility="unverified",
            failure_reason="renderer_light_readback_failed",
            rig=rig,
        )

    env_warnings = _apply_environment_map(runtime, bitmap, intensity=float(intensity), rotation=float(rotation))
    result = {
        "hdri_path": str(path),
        "bitmap": _bitmap_summary(bitmap),
        "intensity": float(intensity),
        "rotation": float(rotation),
        "renderer_family": renderer_family,
        "light_compatibility": "verified",
        "environment_warnings": env_warnings,
        "rig": rig,
        "changed_node_count": int(rig["data"].get("changed_node_count", 0)),
        "lights": rig["data"].get("lights", []),
    }
    return lookdev_success("Configured HDR lighting", **result)


def set_hdri_rotation(runtime: Any, rotation: float, frame: Optional[float] = None) -> Dict[str, Any]:
    """Rotate the active environment bitmap through its native UV placement."""
    bitmap = getattr(runtime, "environmentMap", None)
    if bitmap is None:
        return lookdev_error("No active environment map")
    if frame is None:
        u_offset = _set_bitmap_rotation(bitmap, rotation)
    else:
        try:
            u_offset = float(rotation) / 360.0
            controller = _ensure_bitmap_rotation_controller(runtime, bitmap)
            key = runtime.addNewKey(controller, float(frame))
            key.value = u_offset
            name = getattr(runtime, "Name", str)
            if hasattr(runtime, "setInTangentType") and hasattr(runtime, "setOutTangentType"):
                runtime.setInTangentType(key, name("linear"))
                runtime.setOutTangentType(key, name("linear"))
            else:
                key.inTangentType = name("linear")
                key.outTangentType = name("linear")
        except Exception as exc:  # noqa: BLE001
            return lookdev_error("Could not key HDRI rotation", frame=float(frame), error=str(exc))
    if u_offset is None:
        return lookdev_error("Active environment map does not expose UV placement")
    return lookdev_success(
        "Updated HDRI rotation",
        rotation=float(rotation),
        u_offset=u_offset,
        frame=None if frame is None else float(frame),
        interpolation=None if frame is None else "linear",
    )


def _renderer_family(runtime: Any) -> str:
    renderer = current_renderer(runtime)
    if renderer is None:
        return "unknown"
    class_of = getattr(runtime, "classOf", None)
    try:
        name = str(class_of(renderer)) if callable(class_of) else type(renderer).__name__
    except Exception:  # noqa: BLE001 - host wrappers can reject class introspection.
        name = type(renderer).__name__
    normalized = name.lower()
    if "arnold" in normalized:
        return "arnold"
    if "vray" in normalized or "v-ray" in normalized:
        return "vray"
    if "scanline" in normalized:
        return "scanline"
    return "other"


def _create_bitmap(runtime: Any, hdri_path: str) -> Any:
    for attr in ("BitmapTexture", "bitmapTexture", "Bitmaptexture"):
        factory = getattr(runtime, attr, None)
        if callable(factory):
            try:
                bitmap = factory()
                _set_bitmap_path(bitmap, hdri_path)
                return bitmap
            except Exception:  # noqa: BLE001
                pass
    bitmap = type("BitmapTexture", (), {})()
    _set_bitmap_path(bitmap, hdri_path)
    return bitmap


def _set_bitmap_path(bitmap: Any, hdri_path: str) -> None:
    for attr in ("filename", "fileName", "path"):
        try:
            setattr(bitmap, attr, hdri_path)
        except Exception:  # noqa: BLE001
            pass


def _apply_environment_map(runtime: Any, bitmap: Any, *, intensity: float, rotation: float) -> list:
    warnings = []
    for attr in ("environmentMap", "environment_map", "envMap", "backgroundMap"):
        try:
            setattr(runtime, attr, bitmap)
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not set {}: {}".format(attr, exc))
    for attr in ("environmentMapOn", "useEnvironmentMap", "environment_map_on"):
        try:
            setattr(runtime, attr, True)
        except Exception:  # noqa: BLE001
            pass
    for attr in ("environmentMapAmount", "envMapIntensity", "environmentIntensity"):
        try:
            setattr(runtime, attr, float(intensity))
        except Exception:  # noqa: BLE001
            pass
    for attr in ("environmentMapAngle", "envMapRotation", "environmentRotation"):
        try:
            setattr(runtime, attr, float(rotation))
        except Exception:  # noqa: BLE001
            pass
    if _set_bitmap_rotation(bitmap, rotation) is None:
        warnings.append("Environment bitmap does not expose UV placement")
    return warnings


def _set_bitmap_rotation(bitmap: Any, rotation: float, *, wrap: bool = True) -> Optional[float]:
    coords = getattr(bitmap, "coords", None)
    if coords is None or not hasattr(coords, "U_Offset"):
        return None
    u_offset = ((float(rotation) % 360.0) if wrap else float(rotation)) / 360.0
    coords.U_Offset = u_offset
    return float(coords.U_Offset)


def _ensure_bitmap_rotation_controller(runtime: Any, bitmap: Any) -> Any:
    coords = bitmap.coords
    name = getattr(runtime, "Name", str)
    property_name = name("U_Offset")
    controller = runtime.getPropertyController(coords, property_name)
    if controller is not None:
        return controller
    controller = runtime.Bezier_Float()
    if runtime.setPropertyController(coords, property_name, controller) is False:
        raise RuntimeError("3ds Max rejected the HDRI rotation controller")
    return controller


def _bitmap_summary(bitmap: Any) -> Dict[str, Any]:
    return {
        "path": str(getattr(bitmap, "filename", getattr(bitmap, "fileName", getattr(bitmap, "path", ""))) or ""),
        "type": type(bitmap).__name__,
    }
