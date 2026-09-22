"""Capture the 3ds Max desktop, cropped to a renderer frame buffer."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._render_utils import IMAGE_EXTENSIONS, validate_output_path
from dcc_mcp_3dsmax._viewport_utils import capture_screen
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    output_path: Optional[str] = None,
    source: str = "auto",
    crop: bool = True,
    open_frame_buffer: bool = True,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Capture the desktop, optionally cropped to the V-Ray / Corona / FStorm frame buffer."""
    target = output_path or str(Path(tempfile.gettempdir()) / "dcc_mcp_3dsmax_screen.png")
    path, error = validate_output_path(target, allowed_extensions=IMAGE_EXTENSIONS, overwrite=overwrite)
    if error is not None:
        return error
    return capture_screen(
        get_runtime(),
        path,
        source=str(source or "auto").lower(),
        crop=bool(crop),
        open_frame_buffer=bool(open_frame_buffer),
    )
