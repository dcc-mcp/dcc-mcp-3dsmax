"""Enable or disable a modifier, with separate viewport and render granularity."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import (
    find_modifier,
    mesh_error,
    mesh_success,
    resolve_targets,
    set_modifier_state,
)
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    modifier_name: Optional[str] = None,
    modifier_index: Optional[int] = None,
    enabled: Optional[bool] = None,
    enabled_in_views: Optional[bool] = None,
    enabled_in_render: Optional[bool] = None,
) -> Dict[str, Any]:
    """Set modifier enable state on every explicit target.

    Every requested flag is verified by reading it back, so a modifier that does
    not accept the flag fails the call instead of reporting success.
    """
    if enabled is None and enabled_in_views is None and enabled_in_render is None:
        return mesh_error("At least one of enabled, enabled_in_views, or enabled_in_render is required")

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
        applied, error = set_modifier_state(
            rt,
            modifier,
            enabled=enabled,
            enabled_in_views=enabled_in_views,
            enabled_in_render=enabled_in_render,
        )
        if error:
            return mesh_error(error, node=node_identity(node), updated=rows)
        rows.append(
            {
                "node": node_identity(node),
                "modifier": {
                    "index": index,
                    "name": str(getattr(modifier, "name", "") or type(modifier).__name__),
                    "applied": applied,
                },
            }
        )

    return mesh_success(
        "Updated modifier state on {} node(s)".format(len(rows)),
        nodes=rows,
        count=len(rows),
    )
