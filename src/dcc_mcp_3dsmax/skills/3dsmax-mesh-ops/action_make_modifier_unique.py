"""Break modifier instancing so one node's modifier can be edited independently."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import find_modifier, make_modifier_unique, mesh_error, mesh_success, resolve_targets
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
    """Make one modifier unique on every explicit target.

    The modifier is resolved on every target before anything is changed, so a
    target without it aborts the call without partial changes.
    """
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets

    # Phase 1 - resolve every target before mutating anything.
    pending = []
    for node in targets["objects"]:
        index, modifier, error = find_modifier(node, modifier_name=modifier_name, modifier_index=modifier_index)
        if error:
            return mesh_error(error, node=node_identity(node), updated=[])
        pending.append((node, index, modifier))

    # Phase 2 - apply.
    rows = []
    warnings = []
    for node, index, modifier in pending:
        outcome = make_modifier_unique(rt, node, modifier)
        if outcome.get("error"):
            return mesh_error(outcome["error"], node=node_identity(node), updated=rows)
        if outcome.get("warning"):
            warnings.append(outcome["warning"])
        rows.append(
            {
                "node": node_identity(node),
                "modifier": {
                    "index": index,
                    "name": str(getattr(modifier, "name", "") or type(modifier).__name__),
                    "entry_point": outcome.get("entry_point"),
                },
            }
        )

    return mesh_success(
        "Made the modifier unique on {} node(s)".format(len(rows)),
        nodes=rows,
        count=len(rows),
        warnings=warnings,
    )
