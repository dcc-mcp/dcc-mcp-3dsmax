"""Control the V-Ray interactive production rendering (IPR) preview."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._viewport_utils import vray_ipr
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(action: str = "status") -> Dict[str, Any]:
    """Start, stop, refresh, or query the V-Ray IPR preview."""
    return vray_ipr(get_runtime(), str(action or "status").lower())
