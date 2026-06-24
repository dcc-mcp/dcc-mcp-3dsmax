"""Helpers for 3ds Max lookdev skill scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from dcc_mcp_3dsmax._camera_light_utils import create_three_point_light_rig


def lookdev_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def lookdev_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


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
    return warnings


def _bitmap_summary(bitmap: Any) -> Dict[str, Any]:
    return {
        "path": str(getattr(bitmap, "filename", getattr(bitmap, "fileName", getattr(bitmap, "path", ""))) or ""),
        "type": type(bitmap).__name__,
    }
