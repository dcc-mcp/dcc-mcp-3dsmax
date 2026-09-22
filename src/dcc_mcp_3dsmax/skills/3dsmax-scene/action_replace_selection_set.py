"""Replace the contents of a named selection set."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._organization_utils import replace_selection_set, resolve_organization_targets
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    name: str,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Replace the contents of an existing named selection set."""
    rt = get_runtime()
    targets = resolve_organization_targets(rt, node_names=node_names, handles=handles, use_selection=use_selection)
    if not targets.get("success"):
        return targets
    return replace_selection_set(rt, name=name, nodes=targets["objects"])
