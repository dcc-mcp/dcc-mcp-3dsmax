"""Create a shared-name typed transform constraint."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._rig_constraint_contract import create_constraint
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    constraint_type: str,
    constrained_name: Optional[str] = None,
    constrained_handle: Optional[int] = None,
    target_name: Optional[str] = None,
    target_handle: Optional[int] = None,
    weight: float = 100.0,
    maintain_offset: bool = False,
) -> Dict[str, Any]:
    """Create point, orient, aim, or parent constraint and verify target readback."""
    return create_constraint(
        get_runtime(),
        constrained_name=constrained_name,
        constrained_handle=constrained_handle,
        target_name=target_name,
        target_handle=target_handle,
        constraint_type=constraint_type,
        weight=weight,
        maintain_offset=maintain_offset,
    )
