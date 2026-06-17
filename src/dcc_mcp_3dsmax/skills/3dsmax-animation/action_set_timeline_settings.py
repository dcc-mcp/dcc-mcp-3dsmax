"""Set timeline settings."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_utils import set_timeline
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    start_frame: Optional[int] = None, end_frame: Optional[int] = None, frame_rate: Optional[float] = None
) -> Dict[str, Any]:
    """Set timeline range and frame rate."""
    return set_timeline(get_runtime(), start_frame=start_frame, end_frame=end_frame, frame_rate=frame_rate)
