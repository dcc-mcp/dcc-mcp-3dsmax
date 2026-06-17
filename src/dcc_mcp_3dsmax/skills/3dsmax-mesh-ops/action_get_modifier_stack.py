"""Summarize modifier stacks."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._mesh_ops import mesh_success, modifier_stack_summary, resolve_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Return modifier stack summaries for explicit targets."""
    rt = get_runtime()
    targets = resolve_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    rows = [modifier_stack_summary(node) for node in targets["objects"]]
    return mesh_success("Summarized modifier stacks", nodes=rows, count=len(rows))
