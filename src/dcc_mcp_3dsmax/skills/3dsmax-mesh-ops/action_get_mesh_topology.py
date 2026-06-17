"""Summarize mesh topology."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import mesh_success, resolve_targets, topology_summary
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Return topology counts for explicit targets."""
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    rows = [topology_summary(rt, node) for node in targets["objects"]]
    return mesh_success("Summarized mesh topology", nodes=rows, count=len(rows))
