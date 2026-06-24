"""Set up HDR environment lighting for look development."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from dcc_mcp_3dsmax._lookdev_utils import setup_hdr_lighting
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    hdri_path: str,
    name_prefix: str = "Review",
    intensity: float = 1.0,
    rotation: float = 0.0,
    target_position: Optional[Sequence[float]] = None,
    distance: float = 100.0,
) -> Dict[str, Any]:
    """Configure HDR environment lighting and an optional three-point rig."""
    return setup_hdr_lighting(
        get_runtime(),
        hdri_path=hdri_path,
        name_prefix=name_prefix,
        intensity=intensity,
        rotation=rotation,
        target_position=target_position,
        distance=distance,
    )
