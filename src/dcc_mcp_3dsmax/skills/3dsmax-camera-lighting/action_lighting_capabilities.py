"""Report the light providers the active 3ds Max host can build."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dcc_mcp_3dsmax._light_providers import lighting_capabilities
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    renderer: Optional[str] = None,
    providers: Optional[List[str]] = None,
    probe: bool = False,
) -> Dict[str, Any]:
    """Report lighting providers, their enums, and the renderer routing.

    ``probe=True`` builds one throwaway light per available factory, records
    which controls the host exposes and which enum indices it accepts, then
    deletes the light and verifies the deletion.
    """
    return lighting_capabilities(get_runtime(), renderer=renderer, providers=providers, probe=bool(probe))
