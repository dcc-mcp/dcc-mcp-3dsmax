"""Assign bitmap textures to renderer-native material slots."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dcc_mcp_3dsmax._material_utils import find_material, material_error, material_success
from dcc_mcp_3dsmax._renderer_materials import (
    detect_renderer_family,
    renderer_bitmap_connections,
    set_material_map,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    material_name: str,
    slot: str,
    texture_path: str,
    renderer: str = "auto",
    allow_missing: bool = False,
    wrap_normal: bool = True,
) -> Dict[str, Any]:
    """Assign a bitmap texture path to one material map slot."""
    path = Path(texture_path).expanduser()
    if not path.is_file() and not allow_missing:
        return material_error("Texture path does not exist", texture_path=str(path))
    runtime = get_runtime()
    material = find_material(runtime, material_name)
    if material is None:
        return material_error("Material not found", material_name=material_name)

    requested = None if renderer == "auto" else renderer
    family = detect_renderer_family(runtime, material=material, requested=requested)
    result = set_material_map(
        material,
        slot,
        _create_bitmap(runtime, str(path)),
        runtime=runtime,
        renderer=family,
        wrap_normal=bool(wrap_normal),
    )
    data = {
        "material_name": material_name,
        "slot": slot,
        "texture_path": str(path),
        "renderer": family,
        "attribute": result.get("attribute"),
        "connections": renderer_bitmap_connections(material, runtime=runtime, renderer=family),
        "warnings": result.get("warnings", []),
    }
    if not result.get("applied"):
        data["candidates"] = result.get("candidates", [])
        data["failure_reason"] = result.get("error", "map_slot_not_applied")
        return material_error("Could not wire the texture into the material slot", **data)
    return material_success("Assigned bitmap texture", **data)


def _create_bitmap(runtime: Any, texture_path: str) -> Any:
    constructor = getattr(runtime, "Bitmaptexture", None) or getattr(runtime, "BitmapTexture", None)
    if callable(constructor):
        try:
            return constructor(filename=texture_path)
        except TypeError:
            bitmap = constructor()
            bitmap.filename = texture_path
            return bitmap
    bitmap = type("BitmapTexture", (), {})()
    bitmap.filename = texture_path
    return bitmap
