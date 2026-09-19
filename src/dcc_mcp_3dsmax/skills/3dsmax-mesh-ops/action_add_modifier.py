"""Add an arbitrary modifier class to explicit mesh targets."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import (
    apply_modifier_properties,
    attach_modifier,
    create_modifier,
    mesh_error,
    mesh_success,
    resolve_targets,
)
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    modifier_class: str = "",
    properties: Optional[dict] = None,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Add a modifier by class name to every explicit target.

    ``properties`` are applied after instantiation and verified by reading each
    value back, so a property the modifier does not accept fails the call.
    """
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets

    wanted_class = str(modifier_class or "").strip()
    if not wanted_class:
        return mesh_error("modifier_class is required")

    rows = []
    for node in targets["objects"]:
        modifier, error = create_modifier(rt, wanted_class)
        if error:
            return mesh_error(error, modifier_class=wanted_class, applied_to=rows)

        applied, error = apply_modifier_properties(rt, modifier, properties)
        if error:
            # The modifier is not attached yet, so the partial writes are
            # discarded with it - but report them rather than hiding them.
            return mesh_error(error, modifier_class=wanted_class, applied_to=rows, partially_applied=applied)

        index, error = attach_modifier(rt, node, modifier)
        if error:
            return mesh_error(error, modifier_class=wanted_class, applied_to=rows)

        rows.append(
            {
                "node": node_identity(node),
                "modifier": {
                    "index": index,
                    "name": str(getattr(modifier, "name", "") or wanted_class),
                    "type": wanted_class,
                    "applied_properties": applied,
                },
            }
        )

    return mesh_success(
        "Added {} modifier to {} node(s)".format(wanted_class, len(rows)),
        modifier_class=wanted_class,
        nodes=rows,
        count=len(rows),
    )
