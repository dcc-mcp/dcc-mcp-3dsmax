"""List keyframes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_utils import anim_success, list_keyframes, resolve_anim_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """List keyframes for explicit targets."""
    rt = get_runtime()
    targets = resolve_anim_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    rows = [list_keyframes(node) for node in targets["objects"]]
    return anim_success("Listed keyframes", nodes=rows, count=len(rows))
