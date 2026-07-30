"""Set key interpolation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_utils import anim_success, resolve_anim_targets, set_interpolation
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    interpolation: str,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    frames: Optional[list] = None,
) -> Dict[str, Any]:
    """Set interpolation on matching keys."""
    rt = get_runtime()
    targets = resolve_anim_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    total = 0
    rows = []
    for node in targets["objects"]:
        result = set_interpolation(rt, node, interpolation=interpolation, frames=frames)
        if not result.get("success"):
            return result
        rows.append(result["data"])
        total += max(
            result["data"]["changed_key_count"],
            result["data"].get("native_changed_key_count", 0),
        )
    return anim_success("Updated key interpolation", changes=rows, changed_key_count=total)
