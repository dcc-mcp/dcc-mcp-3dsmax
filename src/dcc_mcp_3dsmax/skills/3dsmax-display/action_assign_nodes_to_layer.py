"""Assign nodes to a display layer."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._display_utils import assign_nodes_to_layer, resolve_display_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    layer_name: str,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    create_if_missing: bool = True,
) -> Dict[str, Any]:
    """Assign explicit target nodes to a display layer."""
    rt = get_runtime()
    targets = resolve_display_targets(
        rt, node_names=node_names, handles=handles, use_selection=use_selection, require_targets=True
    )
    if not targets.get("success"):
        return targets
    return assign_nodes_to_layer(
        rt, layer_name=layer_name, nodes=targets["objects"], create_if_missing=create_if_missing
    )
