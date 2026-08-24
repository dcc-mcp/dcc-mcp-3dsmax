"""Set and verify a bounded batch of transform keyframes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._animation_contract import set_keyframes
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    keys: list,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Set one validated key batch on explicit targets."""
    return set_keyframes(
        get_runtime(),
        keys=keys,
        node_names=node_names,
        handles=handles,
        use_selection=use_selection,
    )
