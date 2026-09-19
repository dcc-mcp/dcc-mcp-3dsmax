"""Remove a modifier by name or index from explicit mesh targets."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import find_modifier, mesh_error, mesh_success, remove_modifier, resolve_targets
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    modifier_name: Optional[str] = None,
    modifier_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Remove one modifier from every explicit target.

    The modifier is resolved on every target *before* anything is removed, so a
    target that does not have it aborts the whole call without partial changes.
    """
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets

    # Phase 1 - resolve every target so a single miss cannot leave half a batch removed.
    pending = []
    for node in targets["objects"]:
        index, modifier, error = find_modifier(node, modifier_name=modifier_name, modifier_index=modifier_index)
        if error:
            return mesh_error(error, node=node_identity(node), removed_from=[])
        pending.append((node, index, modifier))

    # Phase 2 - remove, verifying the stack shrank for each node.
    rows = []
    for node, index, modifier in pending:
        error = remove_modifier(rt, node, index)
        if error:
            return mesh_error(error, removed_from=rows)
        rows.append(
            {
                "node": node_identity(node),
                "modifier": {
                    "index": index,
                    "name": str(getattr(modifier, "name", "") or type(modifier).__name__),
                },
            }
        )

    return mesh_success(
        "Removed modifier from {} node(s)".format(len(rows)),
        nodes=rows,
        count=len(rows),
    )
