"""Render the current 3ds Max scene to an image file."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._render_utils import IMAGE_EXTENSIONS, render_scene, validate_output_path
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    output_path: str,
    overwrite: bool = False,
    width: Optional[int] = None,
    height: Optional[int] = None,
    camera_name: Optional[str] = None,
    camera_handle: Optional[int] = None,
) -> Dict[str, Any]:
    """Render the current scene to an image file."""
    path, error = validate_output_path(output_path, allowed_extensions=IMAGE_EXTENSIONS, overwrite=overwrite)
    if error is not None:
        return error
    return render_scene(
        get_runtime(),
        path,
        width=width,
        height=height,
        camera_name=camera_name,
        camera_handle=camera_handle,
    )
