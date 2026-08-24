"""Open a 3ds Max scene through a guarded typed action."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._scene_lifecycle import normalize_scene_path, same_scene_path, scene_status
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(file_path: str, force: bool = False) -> Dict[str, Any]:
    """Open a validated .max scene and verify the resulting host state."""
    rt = get_runtime()
    if not isinstance(force, bool):
        return {
            "success": False,
            "message": "force must be a boolean",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_force"},
        }
    path, path_error = normalize_scene_path(file_path, must_exist=True)
    if path_error:
        return {
            "success": False,
            "message": "file_path must be an existing absolute .max file",
            "data": {"failure_stage": "precondition", "failure_reason": path_error},
        }
    if not bool(rt.isMaxFile(str(path))):
        return {
            "success": False,
            "message": "3ds Max rejected the input as an invalid scene file",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_max_file"},
        }

    before = scene_status(rt)
    if before["dirty"] and not force:
        return {
            "success": False,
            "message": "Current scene has unsaved changes; set force=true to replace it",
            "data": {
                "failure_stage": "precondition",
                "failure_reason": "dirty_scene_requires_force",
                "before": before,
            },
        }

    loaded = rt.loadMaxFile(str(path), useFileUnits=True, quiet=True, allowPrompts=False)
    after = scene_status(rt)
    verified = bool(loaded) and same_scene_path(after["current_file_path"], path) and not after["dirty"]
    if not verified:
        return {
            "success": False,
            "message": "3ds Max did not confirm the requested scene after loading",
            "data": {
                "failure_stage": "verify",
                "failure_reason": "scene_open_readback_mismatch",
                "before": before,
                "after": after,
                "requested_file_path": str(path),
                "verified": False,
            },
        }
    return {
        "success": True,
        "message": "Opened and verified 3ds Max scene",
        "data": {
            "before": before,
            "after": after,
            "requested_file_path": str(path),
            "verified": True,
        },
    }
