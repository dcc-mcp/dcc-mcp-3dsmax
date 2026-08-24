"""Read the 3ds Max scene lifecycle status."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._scene_lifecycle import scene_status
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main() -> Dict[str, Any]:
    """Return the current file, dirty state, scene facts, and object count."""
    return {
        "success": True,
        "message": "Retrieved 3ds Max scene lifecycle status",
        "data": scene_status(get_runtime()),
    }
