"""Set a custom property."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._display_utils import display_success, resolve_display_targets, set_custom_property
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    property_name: str,
    value: Any,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Set one custom property on explicit target nodes."""
    rt = get_runtime()
    targets = resolve_display_targets(
        rt, node_names=node_names, handles=handles, use_selection=use_selection, require_targets=True
    )
    if not targets.get("success"):
        return targets
    rows = [set_custom_property(node, property_name=property_name, value=value)["data"] for node in targets["objects"]]
    return display_success("Set custom properties", properties=rows, changed_property_count=len(rows))
