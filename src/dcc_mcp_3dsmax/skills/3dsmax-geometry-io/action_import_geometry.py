"""Import supported geometry files into 3ds Max."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._geometry_io import (
    SUPPORTED_IMPORT_FORMATS,
    fbx_option_error,
    import_geometry_file,
    resolve_import_file,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    file_path: str,
    format: Optional[str] = None,  # noqa: A002 - matches the published tool parameter name.
    mode: str = "merge",
    timeline_mode: str = "source",
) -> Dict[str, Any]:
    """Import a supported geometry file using extension-based dispatch."""
    path, error = resolve_import_file(file_path, expected_format=format)
    if error is not None:
        return error
    format_name = SUPPORTED_IMPORT_FORMATS[path.suffix.lower()]
    fbx_options: Dict[str, Any] = {}
    if format_name == "fbx":
        option_error = fbx_option_error(mode=mode, timeline_mode=timeline_mode)
        if option_error is not None:
            return option_error
        fbx_options["mode"] = mode
        fbx_options["timeline_mode"] = timeline_mode
    return import_geometry_file(get_runtime(), path, format_name=format_name, fbx_options=fbx_options)
