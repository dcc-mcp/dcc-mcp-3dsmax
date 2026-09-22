"""Reset node materials."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._material_utils import (
    find_material,
    material_error,
    material_success,
    reset_materials,
    resolve_material_targets,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    default_material_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Reset materials on explicit targets.

    Every reset is verified by readback, so a host that kept the old material
    is reported as an error instead of a successful reset.
    """
    rt = get_runtime()
    targets = resolve_material_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    default_material = None
    if default_material_name:
        default_material = find_material(rt, default_material_name)
        if default_material is None:
            return material_error("Default material not found", material_name=default_material_name)
    rows, errors = reset_materials(targets["objects"], default_material=default_material)
    data = {
        "assignments": rows,
        "count": len(rows),
        "requested_count": len(targets["objects"]),
        "errors": errors,
    }
    if errors:
        return material_error("Could not reset the material on every target", **data)
    return material_success("Reset material assignments", **data)
