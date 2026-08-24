"""Capture a typed controller pose."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._pose_contract import save_pose
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Capture explicit node transforms in the typed pose schema."""
    return save_pose(
        get_runtime(),
        node_names=node_names,
        handles=handles,
        use_selection=use_selection,
    )
