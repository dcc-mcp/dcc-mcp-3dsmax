"""Delete a named selection set."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._organization_utils import delete_selection_set
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(name: str) -> Dict[str, Any]:
    """Delete one named selection set by name."""
    return delete_selection_set(get_runtime(), name=name)
