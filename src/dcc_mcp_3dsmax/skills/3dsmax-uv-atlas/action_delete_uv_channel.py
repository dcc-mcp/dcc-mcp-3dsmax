"""Delete UV channels."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._uv_atlas import changed_channels, delete_channel, resolve_uv_targets, uv_success, validate_channel
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    channel: int, node_names: Optional[list] = None, handles: Optional[list] = None, use_selection: bool = False
) -> Dict[str, Any]:
    """Delete or disable one UV channel on explicit targets."""
    error = validate_channel(channel)
    if error is not None:
        return error
    rt = get_runtime()
    targets = resolve_uv_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    warnings = []
    for node in targets["objects"]:
        warnings.extend(delete_channel(rt, node, int(channel)))
    return uv_success("Deleted UV channel", nodes=changed_channels(rt, targets["objects"]), warnings=warnings)
