"""Apply UV projection modifiers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._uv_atlas import (
    apply_projection,
    changed_channels,
    resolve_uv_targets,
    uv_success,
    validate_channel,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    channel: int,
    projection: str,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    length: Optional[float] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
) -> Dict[str, Any]:
    """Apply a UVW Map projection modifier to explicit targets."""
    error = validate_channel(channel)
    if error is not None:
        return error
    rt = get_runtime()
    targets = resolve_uv_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    warnings = []
    for node in targets["objects"]:
        warnings.extend(
            apply_projection(
                rt, node, channel=int(channel), projection=projection, length=length, width=width, height=height
            )
        )
    return uv_success("Applied UV projection", nodes=changed_channels(rt, targets["objects"]), warnings=warnings)
