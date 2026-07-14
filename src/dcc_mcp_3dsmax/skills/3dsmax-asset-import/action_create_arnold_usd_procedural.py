"""Create a MAXtoA Arnold USD procedural node."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._asset_import_utils import create_arnold_usd_procedural
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    file_path: str,
    name: str = "USD_Procedural",
    object_path: str = "",
    frame: float = 1.0,
    up_axis: str = "y",
) -> Dict[str, Any]:
    """Create a renderable Arnold procedural that references a USD stage."""
    return create_arnold_usd_procedural(
        get_runtime(),
        file_path,
        name=name,
        object_path=object_path,
        frame=frame,
        up_axis=up_axis,
    )
