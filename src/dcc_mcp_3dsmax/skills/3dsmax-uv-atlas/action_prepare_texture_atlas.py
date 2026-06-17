"""Prepare texture-atlas plans."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._uv_atlas import atlas_plan, resolve_uv_targets, uv_success
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Collect material and bitmap usage for texture-atlas planning."""
    rt = get_runtime()
    targets = resolve_uv_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    return uv_success("Prepared texture atlas plan", **atlas_plan(targets["objects"]))
