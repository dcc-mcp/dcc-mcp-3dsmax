"""Create a guarded new 3ds Max scene."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._scene_lifecycle import scene_status
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(force: bool = False) -> Dict[str, Any]:
    """Reject resetting a dirty scene unless the caller explicitly opts in."""
    rt = get_runtime()
    if not isinstance(force, bool):
        return {
            "success": False,
            "message": "force must be a boolean",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_force"},
        }
    before = scene_status(rt)
    if before["dirty"] and not force:
        return {
            "success": False,
            "message": "Current scene has unsaved changes; set force=true to reset it",
            "data": {
                "failure_stage": "precondition",
                "failure_reason": "dirty_scene_requires_force",
                "before": before,
            },
        }
    reset = rt.resetMaxFile(rt.Name("noPrompt"))
    after = scene_status(rt)
    verified = (
        bool(reset)
        and after["scene_name"] == "Untitled"
        and after["current_file_path"] == ""
        and not after["dirty"]
        and after["object_count"] == 0
    )
    if not verified:
        return {
            "success": False,
            "message": "3ds Max did not confirm an empty clean scene after reset",
            "data": {
                "failure_stage": "verify",
                "failure_reason": "scene_reset_readback_mismatch",
                "before": before,
                "after": after,
                "verified": False,
            },
        }
    return {
        "success": True,
        "message": "Created and verified a new 3ds Max scene",
        "data": {"before": before, "after": after, "verified": True},
    }
