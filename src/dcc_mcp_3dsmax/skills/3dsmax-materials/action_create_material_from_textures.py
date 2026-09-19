"""Build a PBR material from a texture-set directory and wire every map."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from dcc_mcp_3dsmax._material_utils import material_error
from dcc_mcp_3dsmax._renderer_materials import build_material_from_texture_set, collect_texture_files
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    name: Optional[str] = None,
    texture_dir: Optional[str] = None,
    texture_paths: Optional[List[str]] = None,
    renderer: str = "auto",
    slots: Optional[List[str]] = None,
    node_names: Optional[List[str]] = None,
    assign: bool = False,
    recursive: bool = False,
    allow_missing: bool = False,
) -> Dict[str, Any]:
    """Create a renderer material and connect every recognised texture."""
    paths: List[Path] = []
    if texture_paths:
        paths = [Path(str(path)).expanduser() for path in texture_paths]
    elif texture_dir:
        directory = Path(str(texture_dir)).expanduser()
        if not directory.is_dir():
            return material_error("Texture directory does not exist", texture_dir=str(directory))
        paths = collect_texture_files(directory, recursive=bool(recursive))
        if not paths:
            return material_error(
                "Texture directory contains no supported image files",
                texture_dir=str(directory),
                recursive=bool(recursive),
            )
    else:
        return material_error("Either texture_dir or texture_paths is required")

    material_name = str(name or "").strip()
    if not material_name:
        if texture_dir:
            material_name = Path(str(texture_dir)).expanduser().resolve().name or "TextureSetMaterial"
        else:
            material_name = Path(str(paths[0])).stem or "TextureSetMaterial"

    return build_material_from_texture_set(
        get_runtime(),
        paths=paths,
        name=material_name,
        renderer=renderer,
        slots=slots,
        node_names=node_names,
        assign=bool(assign),
        allow_missing=bool(allow_missing),
    )
