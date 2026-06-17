"""Set node display state."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._display_utils import display_success, resolve_display_targets, set_display_state
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    hidden: Optional[bool] = None,
    frozen: Optional[bool] = None,
    wire_color: Optional[list] = None,
    object_color: Optional[list] = None,
    display_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Set display state on explicit target nodes."""
    rt = get_runtime()
    targets = resolve_display_targets(
        rt, node_names=node_names, handles=handles, use_selection=use_selection, require_targets=True
    )
    if not targets.get("success"):
        return targets
    changes = [
        set_display_state(
            node,
            hidden=hidden,
            frozen=frozen,
            wire_color=wire_color,
            object_color=object_color,
            display_mode=display_mode,
        )["data"]
        for node in targets["objects"]
    ]
    return display_success("Updated node display state", changes=changes, changed_node_count=len(changes))
