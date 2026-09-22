"""List display layers."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._display_utils import list_layers
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(include_nodes: bool = False, include_properties: bool = False) -> Dict[str, Any]:
    """List display layers."""
    return list_layers(get_runtime(), include_nodes=include_nodes, include_properties=include_properties)
