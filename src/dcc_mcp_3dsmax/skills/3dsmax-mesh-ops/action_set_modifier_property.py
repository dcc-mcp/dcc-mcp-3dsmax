"""Set properties on a modifier for one object or many."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import (
    apply_modifier_properties,
    find_modifier,
    mesh_error,
    mesh_success,
    resolve_targets,
)
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    properties: Optional[dict] = None,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    modifier_name: Optional[str] = None,
    modifier_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Set modifier properties on every explicit target.

    Each property is written and then read back and compared, so a property the
    modifier rejects or coerces to a different value fails the call.
    """
    if not properties:
        return mesh_error("properties is required and must be a non-empty object")

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

    # Phase 2 - apply and verify.
    rows = []
    for node, index, modifier in pending:
        applied, error = apply_modifier_properties(rt, modifier, properties)
        if error:
            # Surface the values that did land before the failure, so the caller
            # knows the modifier is now partially modified.
            return mesh_error(error, node=node_identity(node), updated=rows, partially_applied=applied)
        rows.append(
            {
                "node": node_identity(node),
                "modifier": {
                    "index": index,
                    "name": str(getattr(modifier, "name", "") or type(modifier).__name__),
                    "applied_properties": applied,
                },
            }
        )

    return mesh_success(
        "Set modifier properties on {} node(s)".format(len(rows)),
        nodes=rows,
        count=len(rows),
    )
