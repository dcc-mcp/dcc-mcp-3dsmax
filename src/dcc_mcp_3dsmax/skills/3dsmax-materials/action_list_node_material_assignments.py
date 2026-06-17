"""List node material assignments."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._material_utils import material_identity, material_success, resolve_material_targets
from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes, node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """List material assignments for explicit targets, selection, or the whole scene."""
    rt = get_runtime()
    if not node_names and not handles and not use_selection:
        nodes = iter_scene_nodes(rt)
    else:
        targets = resolve_material_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
        if not targets.get("success"):
            return targets
        nodes = targets["objects"]
    rows = []
    for node in nodes:
        material = getattr(node, "material", None)
        rows.append(
            {"node": node_identity(node), "material": material_identity(material) if material is not None else None}
        )
    return material_success("Listed node material assignments", assignments=rows, count=len(rows))
