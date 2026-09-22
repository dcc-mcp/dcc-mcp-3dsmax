"""Ungroup 3ds Max group nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._organization_utils import resolve_organization_targets, ungroup_nodes
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Dissolve group heads and keep their member nodes."""
    rt = get_runtime()
    targets = resolve_organization_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    return ungroup_nodes(rt, nodes=targets["objects"])
