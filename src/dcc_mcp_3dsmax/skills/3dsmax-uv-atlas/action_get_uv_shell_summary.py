"""Summarize UV shells."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._uv_atlas import resolve_uv_targets, shell_summary, uv_success, validate_channel
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    channel: int, node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Return UV shell summaries for explicit targets."""
    error = validate_channel(channel)
    if error is not None:
        return error
    rt = get_runtime()
    targets = resolve_uv_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    rows = [shell_summary(node, int(channel)) for node in targets["objects"]]
    return uv_success("Summarized UV shells", nodes=rows, count=len(rows))
