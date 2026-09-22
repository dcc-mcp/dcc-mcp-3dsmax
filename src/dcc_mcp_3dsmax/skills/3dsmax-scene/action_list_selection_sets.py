"""List named selection sets."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._organization_utils import list_selection_sets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(include_nodes: bool = False) -> Dict[str, Any]:
    """List the named selection sets in the current scene."""
    return list_selection_sets(get_runtime(), include_nodes=include_nodes)
