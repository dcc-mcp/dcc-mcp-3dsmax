"""Shared readback helpers for typed 3ds Max scene lifecycle actions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

MAX_SCENE_PATH_LENGTH = 4096


def normalize_scene_path(file_path: Any, *, must_exist: bool) -> Tuple[Optional[Path], Optional[str]]:
    """Return a bounded absolute .max path or a stable validation reason."""
    if not isinstance(file_path, str) or not file_path.strip():
        return None, "file_path_required"
    if len(file_path) > MAX_SCENE_PATH_LENGTH or "\x00" in file_path:
        return None, "invalid_file_path"

    path = Path(file_path).expanduser()
    if not path.is_absolute() or path.suffix.lower() != ".max":
        return None, "absolute_max_path_required"
    path = path.resolve()
    if must_exist and not path.is_file():
        return None, "scene_file_not_found"
    return path, None


def _string_value(value: Any) -> str:
    if callable(value):
        value = value()
    return str(value).lstrip("#").strip() or "unknown"


def scene_status(rt: Any) -> Dict[str, Any]:
    """Read the post-condition surface directly from the 3ds Max runtime."""
    scene_name = str(getattr(rt, "maxFileName", "") or "Untitled")
    scene_dir = str(getattr(rt, "maxFilePath", "") or "")
    current_file_path = ""
    if scene_name != "Untitled" and scene_dir:
        current_file_path = str((Path(scene_dir) / scene_name).resolve())

    units = getattr(rt, "units", None)
    system_units = _string_value(getattr(units, "SystemType", "unknown"))
    display_units = _string_value(getattr(units, "DisplayType", "unknown"))
    renderers = getattr(rt, "renderers", None)
    renderer = _string_value(getattr(renderers, "current", "unknown"))

    return {
        "scene_name": scene_name,
        "current_file_path": current_file_path,
        "dirty": bool(rt.getSaveRequired()),
        "object_count": len(list(rt.objects)),
        "system_units": system_units,
        "display_units": display_units,
        "renderer": renderer,
    }


def same_scene_path(actual: str, expected: Path) -> bool:
    """Compare resolved scene paths using the host platform's case rules."""
    if not actual:
        return False
    return os.path.normcase(str(Path(actual).resolve())) == os.path.normcase(str(expected.resolve()))


def save_scene_to_path(
    rt: Any,
    file_path: Any,
    *,
    overwrite: bool,
    allow_existing_current: bool = False,
) -> Dict[str, Any]:
    """Save to one bounded path and require file/path/dirty post-condition readback."""
    if not isinstance(overwrite, bool):
        return {
            "success": False,
            "message": "overwrite must be a boolean",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_overwrite"},
        }
    before = scene_status(rt)
    path, path_error = normalize_scene_path(file_path, must_exist=False)
    if path_error:
        return {
            "success": False,
            "message": "file_path must be an absolute .max path",
            "data": {"failure_stage": "precondition", "failure_reason": path_error},
        }
    if not path.parent.is_dir():
        return {
            "success": False,
            "message": "Scene output directory does not exist",
            "data": {"failure_stage": "precondition", "failure_reason": "output_directory_not_found"},
        }
    if path.exists() and not (overwrite or allow_existing_current):
        return {
            "success": False,
            "message": "Scene file already exists; set overwrite=true to replace it",
            "data": {"failure_stage": "precondition", "failure_reason": "scene_file_exists"},
        }

    saved = rt.saveMaxFile(str(path), quiet=True)
    after = scene_status(rt)
    verified = (
        bool(saved) and path.is_file() and same_scene_path(after["current_file_path"], path) and not after["dirty"]
    )
    if not verified:
        return {
            "success": False,
            "message": "3ds Max did not confirm a clean scene at the requested path",
            "data": {
                "failure_stage": "verify",
                "failure_reason": "scene_save_readback_mismatch",
                "before": before,
                "after": after,
                "requested_file_path": str(path),
                "verified": False,
            },
        }
    return {
        "success": True,
        "message": "Saved and verified 3ds Max scene",
        "data": {
            "before": before,
            "after": after,
            "requested_file_path": str(path),
            "verified": True,
        },
    }
