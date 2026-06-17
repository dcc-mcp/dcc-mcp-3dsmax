"""Detect UV overlaps."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._uv_atlas import overlap_summary, resolve_uv_targets, uv_success, validate_channel
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    channel: int, node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Report UV overlap summary data."""
    error = validate_channel(channel)
    if error is not None:
        return error
    rt = get_runtime()
    targets = resolve_uv_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    rows = [overlap_summary(node, int(channel)) for node in targets["objects"]]
    return uv_success("Detected UV overlaps", nodes=rows, count=len(rows))
