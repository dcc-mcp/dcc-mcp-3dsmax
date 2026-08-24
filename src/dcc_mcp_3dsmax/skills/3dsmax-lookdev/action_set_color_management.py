"""Configure 3ds Max scene color management."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._lookdev_utils import set_color_management
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    ocio_config_path: str,
    rendering_color_space: str = "ACEScg",
    data_color_space: str = "Raw",
    lock_settings: bool = True,
    display: Optional[str] = None,
    view_transform: Optional[str] = None,
) -> Dict[str, Any]:
    import pymxs

    return set_color_management(
        get_runtime(),
        ocio_config_path=ocio_config_path,
        rendering_color_space=rendering_color_space,
        data_color_space=data_color_space,
        lock_settings=lock_settings,
        display=display,
        view_transform=view_transform,
        byref=getattr(pymxs, "byref", None),
    )
