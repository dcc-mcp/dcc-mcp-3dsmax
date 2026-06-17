"""Triangulate mesh targets."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import changed_summary, mesh_success, resolve_targets, triangulate_node
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Triangulate explicit mesh targets."""
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    warnings = []
    for node in targets["objects"]:
        warnings.extend(triangulate_node(rt, node))
    return mesh_success("Triangulated meshes", nodes=changed_summary(rt, targets["objects"]), warnings=warnings)
