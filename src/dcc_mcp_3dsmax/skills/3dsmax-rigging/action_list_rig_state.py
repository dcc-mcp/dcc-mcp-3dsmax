"""List rigging state."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._rigging_utils import resolve_rig_targets, rig_state_summary, rig_success
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """List controllers, constraints, deformers, and skinning state."""
    rt = get_runtime()
    targets = resolve_rig_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    nodes = [rig_state_summary(node) for node in targets["objects"]]
    return rig_success("Listed rig state", nodes=nodes, count=len(nodes))
