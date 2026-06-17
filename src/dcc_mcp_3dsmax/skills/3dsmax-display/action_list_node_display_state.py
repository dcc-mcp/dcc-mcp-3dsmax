"""List node display state."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._display_utils import display_state_summary, display_success, resolve_display_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """List node display state."""
    rt = get_runtime()
    targets = resolve_display_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    states = [display_state_summary(node) for node in targets["objects"]]
    return display_success("Listed node display state", nodes=states, count=len(states))
