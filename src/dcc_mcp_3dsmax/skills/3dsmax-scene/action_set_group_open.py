"""Open or close a 3ds Max group."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._organization_utils import resolve_single_target, set_group_open
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    open: bool,
    group_name: Optional[str] = None,
    handle: Optional[int] = None,
) -> Dict[str, Any]:
    """Open a group for member editing, or close it again."""
    rt = get_runtime()
    target = resolve_single_target(rt, node_name=group_name, handle=handle, label="Group")
    if not target.get("success"):
        return target
    return set_group_open(rt, node=target["objects"][0], open=bool(open))
