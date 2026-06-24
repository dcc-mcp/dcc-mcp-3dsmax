"""Render scene to HDR/EXR file."""

from __future__ import annotations
from typing import Any, Dict, Optional
from dcc_mcp_3dsmax._render_advanced import render_hdr_scene, HDR_IMAGE_EXTENSIONS
from dcc_mcp_3dsmax._render_utils import validate_output_path
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    output_path: str,
    overwrite: bool = False,
    width: Optional[int] = None,
    height: Optional[int] = None,
    camera_name: Optional[str] = None,
    camera_handle: Optional[int] = None,
    hdr_format: str = "exr",
    bit_depth: int = 16,
    compression: Optional[str] = None,
) -> Dict[str, Any]:
    path, error = validate_output_path(output_path, allowed_extensions=HDR_IMAGE_EXTENSIONS, overwrite=overwrite)
    if error is not None:
        return error
    return render_hdr_scene(
        get_runtime(), path, width=width, height=height,
        camera_name=camera_name, camera_handle=camera_handle,
        hdr_format=hdr_format, bit_depth=bit_depth, compression=compression,
    )
