"""Import FBX geometry into 3ds Max."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._geometry_io import fbx_option_error, import_geometry_file, resolve_import_file
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    file_path: str,
    mode: str = "merge",
    units: Optional[str] = None,
    up_axis: Optional[str] = None,
    include_animation: bool = True,
    timeline_mode: str = "source",
) -> Dict[str, Any]:
    """Import one FBX file and return created node identities."""
    path, error = resolve_import_file(file_path, expected_format="fbx")
    if error is not None:
        return error
    option_error = fbx_option_error(units=units, up_axis=up_axis, mode=mode, timeline_mode=timeline_mode)
    if option_error is not None:
        return option_error
    return import_geometry_file(
        get_runtime(),
        path,
        format_name="fbx",
        fbx_options={
            "mode": mode,
            "units": units,
            "up_axis": up_axis,
            "include_animation": include_animation,
            "timeline_mode": timeline_mode,
        },
    )
