"""Group scene nodes into instance sets.

An instance is a node that derives from the same object as another node, so
editing one edits all of them. Knowing where that is true is the difference
between moving one prop and moving forty, which is why the tool refuses to
answer when the host cannot tell rather than reporting "no instances".
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
from typing import Any, Dict, Optional

# Import local modules
from dcc_mcp_3dsmax._scene_query import SceneQueryError, find_instances, scene_nodes
from dcc_mcp_3dsmax._scene_utils import resolve_node_object
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    include_unique: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """Report instance sets in the current scene.

    With ``node_name`` or ``handle`` the answer is the instance set that node
    belongs to; without them it is every set with more than one member, unless
    ``include_unique`` is true.
    """
    rt = get_runtime()
    try:
        nodes = scene_nodes(rt)
    except SceneQueryError as exc:
        return {"success": False, "message": str(exc), "data": {"failure_reason": "scene_unavailable", "groups": []}}

    target = None
    if node_name or handle is not None:
        resolved, node = resolve_node_object(rt, node_name=node_name, handle=handle)
        if node is None:
            return {
                "success": False,
                "message": resolved.get("message") or "No matching node found",
                "data": {"failure_reason": "node_not_found", "matches": resolved.get("matches", []), "groups": []},
            }
        target = node

    try:
        data = find_instances(nodes, runtime=rt, target=target, include_unique=include_unique, limit=limit)
    except SceneQueryError as exc:
        return {
            "success": False,
            "message": str(exc),
            "data": {"failure_reason": "instance_query_unavailable", "groups": [], "warnings": []},
        }

    if target is not None:
        return {
            "success": True,
            "message": "{} shares its object with {} node(s)".format(
                data["node"]["node_name"], max(data["instance_count"] - 1, 0)
            ),
            "data": data,
        }
    return {
        "success": True,
        "message": "Found {} instance group(s) covering {} node(s)".format(
            data["group_count"], data["instanced_node_count"]
        ),
        "data": data,
    }
