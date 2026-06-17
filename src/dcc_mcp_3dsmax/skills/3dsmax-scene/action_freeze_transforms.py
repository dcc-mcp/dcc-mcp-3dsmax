"""Freeze transforms for 3ds Max nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_objects
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(node_names: Optional[list] = None, handles: Optional[list] = None) -> Dict[str, Any]:
    """Freeze transforms for resolved nodes."""
    rt = get_runtime()
    result = resolve_node_objects(rt, node_names=node_names, handles=handles)
    if not result.get("success"):
        return {"success": False, "message": result["message"], "data": result}
    helper = getattr(rt, "freezeTransform", None) or getattr(rt, "resetTransform", None)
    for node in result["objects"]:
        if callable(helper):
            helper(node)
        else:
            rt.execute("resetTransform ${}".format(getattr(node, "name", "")))
    return {
        "success": True,
        "message": "Froze transforms",
        "data": {"nodes": [node_identity(node) for node in result["objects"]]},
    }
