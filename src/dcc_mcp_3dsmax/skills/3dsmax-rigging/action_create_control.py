"""Create a bounded rig control helper."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._rig_control_contract import create_control
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    name: str,
    shape: str = "circle",
    size: float = 10.0,
    position: Optional[list] = None,
    color: Optional[list] = None,
) -> Dict[str, Any]:
    """Create and verify a circle or point rig control."""
    return create_control(
        get_runtime(),
        name=name,
        shape=shape,
        size=size,
        position=position,
        color=color,
    )
