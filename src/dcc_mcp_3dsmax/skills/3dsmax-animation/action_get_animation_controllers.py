"""Get animation controllers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_utils import anim_success, controller_summary, resolve_anim_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Return transform controller metadata."""
    rt = get_runtime()
    targets = resolve_anim_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    rows = [controller_summary(node) for node in targets["objects"]]
    return anim_success("Retrieved animation controllers", nodes=rows, count=len(rows))
