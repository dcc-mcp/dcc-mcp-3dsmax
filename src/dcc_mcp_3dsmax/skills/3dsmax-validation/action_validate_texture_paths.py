"""Validate texture paths."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._validation_utils import resolve_validation_targets, response_for, validate_texture_paths
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Validate referenced texture paths."""
    rt = get_runtime()
    targets = resolve_validation_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    return response_for("texture_paths", validate_texture_paths(targets["objects"]))
