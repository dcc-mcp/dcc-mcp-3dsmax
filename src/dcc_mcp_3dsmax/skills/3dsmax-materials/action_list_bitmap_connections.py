"""List material bitmap connections."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._material_utils import find_material, material_error, material_success
from dcc_mcp_3dsmax._renderer_materials import renderer_bitmap_connections
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(material_name: str) -> Dict[str, Any]:
    """List bitmap/map connections for one material, including native slots."""
    runtime = get_runtime()
    material = find_material(runtime, material_name)
    if material is None:
        return material_error("Material not found", material_name=material_name)
    connections = renderer_bitmap_connections(material, runtime=runtime)
    return material_success(
        "Listed bitmap connections", material_name=material_name, connections=connections, count=len(connections)
    )
