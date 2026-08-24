"""Save the current 3ds Max scene to an explicit path."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._scene_lifecycle import save_scene_to_path
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(file_path: str, overwrite: bool = False) -> Dict[str, Any]:
    """Save to a bounded .max path and verify it becomes the clean current scene."""
    return save_scene_to_path(get_runtime(), file_path, overwrite=overwrite)
