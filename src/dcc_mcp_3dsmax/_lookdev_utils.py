"""Helpers for 3ds Max lookdev skill scripts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from dcc_mcp_3dsmax._camera_light_utils import create_three_point_light_rig


def lookdev_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def lookdev_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def get_color_management(runtime: Any) -> Dict[str, Any]:
    """Read the 3ds Max 2024+ scene color-pipeline settings."""
    manager = getattr(runtime, "ColorPipelineMgr", None)
    if manager is None:
        return lookdev_error("ColorPipelineMgr is unavailable in this 3ds Max version")
    return lookdev_success(
        "Read color management",
        mode=str(manager.Mode),
        ocio_config_path=str(manager.OCIOConfigPath),
        rendering_color_space=str(manager.RenderingColorSpace),
        data_color_space=str(manager.DataColorSpace),
        status=str(manager.Status),
        locked=bool(manager.Locked),
    )


def set_color_management(
    runtime: Any,
    *,
    ocio_config_path: str,
    rendering_color_space: str = "ACEScg",
    data_color_space: str = "Raw",
    lock_settings: bool = True,
) -> Dict[str, Any]:
    """Configure the scene to use a custom OCIO config and rendering space."""
    path = Path(ocio_config_path).expanduser()
    if not path.is_file():
        return lookdev_error("OCIO config file does not exist", ocio_config_path=str(path))
    manager = getattr(runtime, "ColorPipelineMgr", None)
    if manager is None:
        return lookdev_error("ColorPipelineMgr is unavailable in this 3ds Max version")

    try:
        os.environ["OCIO"] = str(path)
        name = getattr(runtime, "Name", lambda value: value)
        manager.Mode = name("OCIO_EnvVar")
        manager.Locked = False
        manager.ReInitialize()
        manager.RenderingColorSpace = rendering_color_space
        manager.DataColorSpace = data_color_space
        manager.Locked = bool(lock_settings)
    except Exception as exc:  # noqa: BLE001 - pymxs reports host failures as RuntimeError.
        return lookdev_error("Failed to configure color management", error=str(exc))

    result = get_color_management(runtime)
    if not result["success"]:
        return result
    data = result["data"]
    if "ocio_envvar" not in data["mode"].lower() or "normal" not in data["status"].lower():
        return lookdev_error("OCIO color management did not become valid", **data)
    if data["rendering_color_space"].lower() != rendering_color_space.lower():
        return lookdev_error("Rendering color space was not applied", **data)
    result["message"] = "Configured OCIO color management"
    return result


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

    bitmap = _create_bitmap(runtime, str(path))
    env_warnings = _apply_environment_map(runtime, bitmap, intensity=float(intensity), rotation=float(rotation))
    rig = create_three_point_light_rig(
        runtime,
        name_prefix=name_prefix,
        target_position=target_position,
        distance=distance,
    )
    result = {
        "hdri_path": str(path),
        "bitmap": _bitmap_summary(bitmap),
        "intensity": float(intensity),
        "rotation": float(rotation),
        "environment_warnings": env_warnings,
        "rig": rig,
    }
    if rig.get("success"):
        result["changed_node_count"] = int(rig["data"].get("changed_node_count", 0))
        result["lights"] = rig["data"].get("lights", [])
        return lookdev_success("Configured HDR lighting", **result)
    result["changed_node_count"] = 0
    result["warnings"] = [rig.get("message", "Lighting rig creation failed")]
    return lookdev_success("Configured HDR lighting with rig warnings", **result)


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
