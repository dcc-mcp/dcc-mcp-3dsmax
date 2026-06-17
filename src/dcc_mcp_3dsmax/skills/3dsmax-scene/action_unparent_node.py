"""Unparent one 3ds Max node."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_object
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(node_name: Optional[str] = None, handle: Optional[int] = None) -> Dict[str, Any]:
    """Detach one node from its parent."""
    result, node = resolve_node_object(get_runtime(), node_name=node_name, handle=handle)
    if node is None:
        return {"success": False, "message": result["message"], "data": result}
    old_parent = getattr(node, "parent", None)
    try:
        node.parent = None
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": "Could not unparent node", "data": {"error": str(exc)}}
    return {
        "success": True,
        "message": "Unparented node",
        "data": {
            "node": node_identity(node),
            "previous_parent": node_identity(old_parent) if old_parent is not None else None,
        },
    }
