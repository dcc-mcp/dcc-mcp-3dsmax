"""Assign bitmap textures to material slots."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dcc_mcp_3dsmax._material_utils import (
    assign_bitmap,
    bitmap_connections,
    create_bitmap,
    find_material,
    material_error,
    material_success,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(material_name: str, slot: str, texture_path: str, allow_missing: bool = False) -> Dict[str, Any]:
    """Assign a bitmap texture path to one common map slot."""
    path = Path(texture_path).expanduser()
    if not path.is_file() and not allow_missing:
        return material_error("Texture path does not exist", texture_path=str(path))
    rt = get_runtime()
    material = find_material(rt, material_name)
    if material is None:
        return material_error("Material not found", material_name=material_name)
    bitmap = create_bitmap(rt, str(path))
    warnings = assign_bitmap(material, slot, bitmap, runtime=rt)
    return material_success(
        "Assigned bitmap texture",
        material_name=material_name,
        slot=slot,
        texture_path=str(path),
        connections=bitmap_connections(material),
        warnings=warnings,
    )
