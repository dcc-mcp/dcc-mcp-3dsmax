"""Rotate the active 3ds Max HDR environment."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._lookdev_utils import set_hdri_rotation
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(rotation: float, frame: Optional[float] = None) -> Dict[str, Any]:
    """Set the active environment rotation in degrees."""
    return set_hdri_rotation(get_runtime(), rotation, frame)
