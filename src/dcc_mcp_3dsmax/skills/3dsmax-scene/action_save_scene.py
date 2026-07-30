"""Save the current 3ds Max scene."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(file_path: str, overwrite: bool = False) -> Dict[str, Any]:
    """Save the current scene to an explicit .max path."""
    path = Path(file_path).expanduser().resolve()
    if path.suffix.lower() != ".max":
        return {"success": False, "message": "file_path must end with .max", "data": {"file_path": str(path)}}
    if not path.parent.is_dir():
        return {
            "success": False,
            "message": "Scene output directory does not exist",
            "data": {"file_path": str(path)},
        }
    if path.exists() and not overwrite:
        return {"success": False, "message": "Scene file already exists", "data": {"file_path": str(path)}}

    saved = get_runtime().saveMaxFile(str(path), quiet=True)
    if saved is False:
        return {
            "success": False,
            "message": "3ds Max failed to save the scene",
            "data": {"file_path": str(path)},
        }
    return {"success": True, "message": "Saved 3ds Max scene", "data": {"file_path": str(path)}}
