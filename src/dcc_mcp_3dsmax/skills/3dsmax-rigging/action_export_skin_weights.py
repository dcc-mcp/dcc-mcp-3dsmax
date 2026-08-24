"""Export native Skin weights to an explicit JSON file."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._skin_weight_files import export_skin_weights
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    output_path: str,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    vertex_indices: Optional[list] = None,
    modifier_name: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Write a verified Skin weight payload atomically."""
    return export_skin_weights(
        get_runtime(),
        output_path=output_path,
        node_name=node_name,
        handle=handle,
        vertex_indices=vertex_indices,
        modifier_name=modifier_name,
        overwrite=overwrite,
    )
