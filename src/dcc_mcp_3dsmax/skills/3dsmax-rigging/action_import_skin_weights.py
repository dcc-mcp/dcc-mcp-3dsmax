"""Import native Skin weights from an explicit JSON file."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._skin_weight_files import import_skin_weights
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    input_path: str,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    modifier_name: Optional[str] = None,
    expected_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify and import a typed Skin weight payload."""
    return import_skin_weights(
        get_runtime(),
        input_path=input_path,
        node_name=node_name,
        handle=handle,
        modifier_name=modifier_name,
        expected_sha256=expected_sha256,
    )
