"""Duplicate 3ds Max nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_objects
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, name_suffix: str = "_copy"
) -> Dict[str, Any]:
    """Duplicate nodes and return created identities."""
    rt = get_runtime()
    result = resolve_node_objects(rt, node_names=node_names, handles=handles)
    if not result.get("success"):
        return {"success": False, "message": result["message"], "data": result}
    copied = []
    for node in result["objects"]:
        try:
            new_node = rt.copy(node)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "message": "Could not duplicate node",
                "data": {"node": node_identity(node), "error": str(exc)},
            }
        if name_suffix:
            try:
                new_node.name = "{}{}".format(getattr(node, "name", "node"), name_suffix)
            except Exception:  # noqa: BLE001
                pass
        copied.append(node_identity(new_node))
    return {"success": True, "message": "Duplicated nodes", "data": {"nodes": copied, "count": len(copied)}}
