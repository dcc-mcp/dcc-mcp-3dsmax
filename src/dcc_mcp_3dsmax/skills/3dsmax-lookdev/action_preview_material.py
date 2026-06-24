"""Preview a material on test geometry for look development."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._lookdev_utils import lookdev_error, lookdev_success
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    material_name: str,
    preview_type: str = "sphere",
) -> Dict[str, Any]:
    """Create test geometry, apply a scene material, and return the result."""
    runtime = get_runtime()
    material = _find_material(runtime, material_name)
    if material is None:
        return lookdev_error("Material not found in scene", material_name=material_name)

    geo = _create_preview_geo(runtime, preview_type)
    geo_name = str(getattr(geo, "name", "preview"))
    try:
        setattr(geo, "material", material)
    except Exception as exc:  # noqa: BLE001
        return lookdev_error(
            "Could not assign material to preview geometry",
            material_name=material_name,
            preview_type=preview_type,
            error=str(exc),
        )

    return lookdev_success(
        "Created material preview",
        material_name=material_name,
        preview_type=preview_type,
        geometry_name=geo_name,
    )


def _find_material(runtime: Any, name: str) -> Any:
    for attr in ("sceneMaterials", "materials"):
        try:
            collection = getattr(runtime, attr)
        except Exception:  # noqa: BLE001
            continue
        if collection is not None:
            try:
                for mat in collection:
                    if str(getattr(mat, "name", "") or "") == name:
                        return mat
            except Exception:  # noqa: BLE001
                return getattr(collection, name, None)
            return getattr(collection, name, None)
    return None


def _create_preview_geo(runtime: Any, preview_type: str) -> Any:
    if preview_type == "quad":
        plane = getattr(runtime, "Plane", None)
        if callable(plane):
            return plane()
        return _fallback_sphere(runtime)
    return _fallback_sphere(runtime)


def _fallback_sphere(runtime: Any) -> Any:
    sphere = getattr(runtime, "Sphere", None)
    if callable(sphere):
        return sphere()
    sphere = getattr(runtime, "GeoSphere", None)
    if callable(sphere):
        return sphere()
    return type("PreviewSphere", (), {"name": "PreviewSphere"})()
