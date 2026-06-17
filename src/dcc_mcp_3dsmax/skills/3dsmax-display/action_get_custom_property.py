"""Get a custom property."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._display_utils import display_success, get_custom_property, resolve_display_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    property_name: str, node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Get one custom property from explicit target nodes."""
    rt = get_runtime()
    targets = resolve_display_targets(
        rt, node_names=node_names, handles=handles, use_selection=use_selection, require_targets=True
    )
    if not targets.get("success"):
        return targets
    rows = []
    for node in targets["objects"]:
        result = get_custom_property(node, property_name=property_name)
        if not result.get("success"):
            return result
        rows.append(result["data"])
    return display_success("Read custom properties", properties=rows, count=len(rows))
