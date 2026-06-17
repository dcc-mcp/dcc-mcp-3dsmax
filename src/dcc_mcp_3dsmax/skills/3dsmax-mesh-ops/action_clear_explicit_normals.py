"""Clear explicit normals on mesh targets."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import changed_summary, clear_explicit_normals, mesh_success, resolve_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Clear explicit normals on explicit mesh targets."""
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    warnings = []
    for node in targets["objects"]:
        warnings.extend(clear_explicit_normals(rt, node))
    return mesh_success("Cleared explicit normals", nodes=changed_summary(rt, targets["objects"]), warnings=warnings)
