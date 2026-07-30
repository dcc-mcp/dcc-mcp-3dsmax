"""Delete keyframes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_utils import anim_success, delete_keyframes, resolve_anim_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    frames: Optional[list] = None,
    properties: Optional[list] = None,
) -> Dict[str, Any]:
    """Delete matching keyframes."""
    rt = get_runtime()
    targets = resolve_anim_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    total = 0
    rows = []
    for node in targets["objects"]:
        result = delete_keyframes(rt, node, frames=frames, properties=properties)
        if not result.get("success"):
            return result
        rows.append(result["data"])
        total += max(
            result["data"]["changed_key_count"],
            result["data"].get("native_changed_key_count", 0),
        )
    return anim_success("Deleted keyframes", changes=rows, changed_key_count=total)
