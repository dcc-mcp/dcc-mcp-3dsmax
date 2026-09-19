"""Collapse the modifier stack of explicit mesh targets."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import collapse_modifier_stack, mesh_error, mesh_success, resolve_targets
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Collapse the modifier stack of every explicit target.

    Collapsing is irreversible. The stack is re-read after each collapse to
    confirm it actually shrank; a host that silently does nothing is an error,
    not a success.
    """
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets

    rows = []
    warnings = []
    for node in targets["objects"]:
        outcome = collapse_modifier_stack(rt, node)
        if outcome.get("error"):
            return mesh_error(outcome["error"], collapsed=rows)
        if outcome.get("warning"):
            warnings.append(outcome["warning"])
        rows.append(
            {
                "node": node_identity(node),
                "modifiers_before": outcome["before"],
                "modifiers_after": outcome["after"],
            }
        )

    return mesh_success(
        "Collapsed the modifier stack on {} node(s)".format(len(rows)),
        nodes=rows,
        count=len(rows),
        warnings=warnings,
    )
