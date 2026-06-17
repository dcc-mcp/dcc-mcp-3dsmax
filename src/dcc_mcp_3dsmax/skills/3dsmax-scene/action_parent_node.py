"""Parent one 3ds Max node under another."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_object
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    child_name: Optional[str] = None,
    child_handle: Optional[int] = None,
    parent_name: Optional[str] = None,
    parent_handle: Optional[int] = None,
) -> Dict[str, Any]:
    """Parent one node under another node."""
    rt = get_runtime()
    child_result, child = resolve_node_object(rt, node_name=child_name, handle=child_handle)
    if child is None:
        return {"success": False, "message": child_result["message"], "data": {"child": child_result}}
    parent_result, parent = resolve_node_object(rt, node_name=parent_name, handle=parent_handle)
    if parent is None:
        return {"success": False, "message": parent_result["message"], "data": {"parent": parent_result}}
    try:
        child.parent = parent
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": "Could not parent node", "data": {"error": str(exc)}}
    return {
        "success": True,
        "message": "Parented node",
        "data": {"child": node_identity(child), "parent": node_identity(parent)},
    }
