"""Import typed animation curve data."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._animation_contract import import_anim_curves
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(curve_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and import the anim-curves v1 exchange payload."""
    return import_anim_curves(get_runtime(), curve_data)
