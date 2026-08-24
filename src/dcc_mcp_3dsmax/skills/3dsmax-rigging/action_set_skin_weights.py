"""Replace native Skin modifier vertex weights."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._skin_weights import set_skin_weights
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    vertices: list,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    modifier_name: Optional[str] = None,
    normalize: bool = True,
) -> Dict[str, Any]:
    """Set a validated Skin weight batch and verify native readback."""
    return set_skin_weights(
        get_runtime(),
        vertices=vertices,
        node_name=node_name,
        handle=handle,
        modifier_name=modifier_name,
        normalize=normalize,
    )
