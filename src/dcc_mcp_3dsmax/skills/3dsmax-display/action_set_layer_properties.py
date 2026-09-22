"""Set display layer properties."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._display_utils import set_layer_properties
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(layer_name: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    """Set layer properties and verify the host kept every one of them."""
    return set_layer_properties(get_runtime(), layer_name=layer_name, properties=properties)
