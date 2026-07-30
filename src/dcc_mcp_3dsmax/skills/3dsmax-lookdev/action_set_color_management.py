"""Configure 3ds Max scene color management."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._lookdev_utils import set_color_management
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    ocio_config_path: str,
    rendering_color_space: str = "ACEScg",
    data_color_space: str = "Raw",
    lock_settings: bool = True,
) -> Dict[str, Any]:
    return set_color_management(
        get_runtime(),
        ocio_config_path=ocio_config_path,
        rendering_color_space=rendering_color_space,
        data_color_space=data_color_space,
        lock_settings=lock_settings,
    )
