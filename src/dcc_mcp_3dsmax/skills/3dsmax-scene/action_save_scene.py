"""Save the current 3ds Max scene."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_lifecycle import save_scene_to_path, scene_status
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(file_path: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
    """Save to the current path, retaining the legacy explicit-path form."""
    rt = get_runtime()
    before = scene_status(rt)
    requested_path = file_path if file_path is not None else before["current_file_path"]
    if not requested_path:
        return {
            "success": False,
            "message": "Current scene has no file path; use save_scene_as",
            "data": {"failure_stage": "precondition", "failure_reason": "current_scene_has_no_path"},
        }

    explicit_path = file_path is not None
    return save_scene_to_path(
        rt,
        requested_path,
        overwrite=overwrite,
        allow_existing_current=not explicit_path,
    )
