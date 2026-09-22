"""Select the members of a named selection set."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._organization_utils import select_selection_set
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(name: str, add: bool = False) -> Dict[str, Any]:
    """Select the nodes held by a named selection set."""
    return select_selection_set(get_runtime(), name=name, add=add)
