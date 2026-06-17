"""Create viewport previews."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._render_utils import PREVIEW_EXTENSIONS, create_preview, render_error, validate_output_path
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    output_path: str,
    overwrite: bool = False,
    start_frame: Optional[int] = None,
    end_frame: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a viewport preview/playblast artifact."""
    if start_frame is not None and end_frame is not None and int(end_frame) < int(start_frame):
        return render_error(
            "end_frame must be greater than or equal to start_frame", start_frame=start_frame, end_frame=end_frame
        )
    path, error = validate_output_path(output_path, allowed_extensions=PREVIEW_EXTENSIONS, overwrite=overwrite)
    if error is not None:
        return error
    return create_preview(get_runtime(), path, start_frame=start_frame, end_frame=end_frame)
