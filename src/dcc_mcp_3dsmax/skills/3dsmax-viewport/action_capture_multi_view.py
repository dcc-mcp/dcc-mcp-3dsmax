"""Capture several standard views without leaving the user's view moved."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._viewport_utils import MULTI_VIEWS, capture_multi_view
from dcc_mcp_3dsmax.api import get_runtime, with_max

TILE_EXTENSION = ".png"


@with_max
def main(
    output_dir: str,
    views: Optional[Sequence[str]] = None,
    composite: bool = True,
    sheet_path: Optional[str] = None,
    columns: Optional[int] = None,
    stem: str = "view",
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Capture front / left / top / perspective (or any supported set) as separate images."""
    directory = Path(str(output_dir or "")).expanduser()
    if not str(output_dir or "").strip():
        return {"success": False, "status": "error", "message": "output_dir is required", "data": {}}
    if not directory.exists():
        return {
            "success": False,
            "status": "error",
            "message": "Output directory does not exist",
            "data": {"output_dir": str(directory)},
        }
    if not directory.is_dir():
        return {
            "success": False,
            "status": "error",
            "message": "Output path is not a directory",
            "data": {"output_dir": str(directory)},
        }
    if columns is not None:
        try:
            columns = int(columns)
        except (TypeError, ValueError):
            return {
                "success": False,
                "status": "error",
                "message": "columns must be an integer",
                "data": {"columns": columns},
            }
        if columns <= 0:
            return {
                "success": False,
                "status": "error",
                "message": "columns must be greater than zero",
                "data": {"columns": columns},
            }

    requested: List[str] = [str(view).lower() for view in (views or ["front", "left", "top", "perspective"])]
    unsupported = [view for view in requested if view not in MULTI_VIEWS]
    if unsupported:
        return {
            "success": False,
            "status": "error",
            "message": "Unsupported view(s): {}".format(", ".join(sorted(set(unsupported)))),
            "data": {"views": requested, "supported_views": list(MULTI_VIEWS)},
        }
    if not overwrite:
        # Refuse before touching the viewport: a half-captured set with the
        # user's view already moved is worse than no capture at all.
        existing = [
            "{}_{}{}".format(stem, view, TILE_EXTENSION)
            for view in requested
            if (directory / "{}_{}{}".format(stem, view, TILE_EXTENSION)).exists()
        ]
        if existing:
            return {
                "success": False,
                "status": "error",
                "message": "Output files already exist; pass overwrite=true to replace them",
                "data": {"existing": sorted(existing), "output_dir": str(directory)},
            }

    sheet: Optional[Path] = Path(str(sheet_path)).expanduser() if sheet_path else None
    return capture_multi_view(
        get_runtime(),
        directory,
        views=requested,
        composite=bool(composite),
        sheet_path=sheet,
        columns=columns,
        stem=str(stem or "view"),
        extension=TILE_EXTENSION,
    )
