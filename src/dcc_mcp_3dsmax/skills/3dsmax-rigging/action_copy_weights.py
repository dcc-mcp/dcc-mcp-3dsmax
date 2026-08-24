"""Copy native Skin weights between exact-topology nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._skin_weights import copy_weights
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    source_name: Optional[str] = None,
    source_handle: Optional[int] = None,
    target_name: Optional[str] = None,
    target_handle: Optional[int] = None,
    vertex_indices: Optional[list] = None,
    source_modifier_name: Optional[str] = None,
    target_modifier_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Copy weights by vertex index and map target bones by exact name."""
    return copy_weights(
        get_runtime(),
        source_name=source_name,
        source_handle=source_handle,
        target_name=target_name,
        target_handle=target_handle,
        vertex_indices=vertex_indices,
        source_modifier_name=source_modifier_name,
        target_modifier_name=target_modifier_name,
    )
