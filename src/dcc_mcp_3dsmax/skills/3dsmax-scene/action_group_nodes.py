"""Group 3ds Max nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_objects
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_names: Optional[list] = None, handles: Optional[list] = None, group_name: str = "Group"
) -> Dict[str, Any]:
    """Group resolved nodes under a new group node."""
    rt = get_runtime()
    result = resolve_node_objects(rt, node_names=node_names, handles=handles)
    if not result.get("success"):
        return {"success": False, "message": result["message"], "data": result}
    try:
        group = rt.group(result["objects"], name=group_name)
    except TypeError:
        group = rt.group(result["objects"])
        try:
            group.name = group_name
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": "Could not group nodes", "data": {"error": str(exc)}}
    return {
        "success": True,
        "message": "Grouped nodes",
        "data": {"group": node_identity(group), "members": [node_identity(node) for node in result["objects"]]},
    }
