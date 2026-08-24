"""Read typed animation curve data."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_contract import get_anim_curves
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
    properties: Optional[list] = None,
) -> Dict[str, Any]:
    """Return versioned curve data for explicit targets."""
    return get_anim_curves(
        get_runtime(),
        node_names=node_names,
        handles=handles,
        use_selection=use_selection,
        properties=properties,
    )
