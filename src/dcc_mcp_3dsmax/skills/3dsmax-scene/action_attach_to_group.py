"""Attach nodes to a 3ds Max group."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._organization_utils import (
    attach_to_group,
    resolve_organization_targets,
    resolve_single_target,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    group_name: Optional[str] = None,
    group_handle: Optional[int] = None,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Attach resolved nodes to an existing group head."""
    rt = get_runtime()
    group = resolve_single_target(rt, node_name=group_name, handle=group_handle, label="Group")
    if not group.get("success"):
        return group
    targets = resolve_organization_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    return attach_to_group(rt, nodes=targets["objects"], group=group["objects"][0])
