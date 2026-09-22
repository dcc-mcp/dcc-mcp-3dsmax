"""Detach nodes from their 3ds Max group."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._organization_utils import detach_from_group, resolve_organization_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Detach resolved nodes from the group they belong to."""
    rt = get_runtime()
    targets = resolve_organization_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    return detach_from_group(rt, nodes=targets["objects"])
