"""Read 3ds Max scene color-management settings."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._lookdev_utils import get_color_management
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main() -> Dict[str, Any]:
    return get_color_management(get_runtime())
