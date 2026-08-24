"""Read native Skin modifier vertex weights."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._skin_weights import get_skin_weights
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    vertex_indices: Optional[list] = None,
    modifier_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Read weights from one explicit Skin modifier."""
    return get_skin_weights(
        get_runtime(),
        node_name=node_name,
        handle=handle,
        vertex_indices=vertex_indices,
        modifier_name=modifier_name,
    )
