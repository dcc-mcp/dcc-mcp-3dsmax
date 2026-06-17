"""Export animation curves."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_utils import anim_success, export_curve_data, resolve_anim_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Export simple curve data for explicit targets."""
    rt = get_runtime()
    targets = resolve_anim_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    return anim_success("Exported animation curves", curve_data=export_curve_data(targets["objects"]))
