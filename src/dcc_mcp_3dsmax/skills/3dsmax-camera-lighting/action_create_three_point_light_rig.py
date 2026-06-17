"""Create a three-point light rig."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._camera_light_utils import create_three_point_light_rig
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    name_prefix: str = "Review", target_position: Optional[list] = None, distance: float = 100.0
) -> Dict[str, Any]:
    """Create a three-point review light rig."""
    return create_three_point_light_rig(
        get_runtime(), name_prefix=name_prefix, target_position=target_position, distance=distance
    )
