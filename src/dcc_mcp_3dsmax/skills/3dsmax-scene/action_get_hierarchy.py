"""Report the recursive parent/child tree of the current 3ds Max scene.

``list_scene_nodes`` answers with a flat list that carries a parent name, which
is enough to ask "who is my parent" but not "what is under this node". This tool
walks the parent links and answers the second question.
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
from typing import Any, Dict, Optional

# Import local modules
from dcc_mcp_3dsmax._scene_query import SceneQueryError, build_hierarchy, scene_nodes
from dcc_mcp_3dsmax._scene_utils import resolve_node_object
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    max_depth: Optional[int] = None,
    include_hidden: bool = True,
    limit: int = 500,
) -> Dict[str, Any]:
    """Return the scene hierarchy as a nested tree.

    ``node_name`` or ``handle`` selects the subtree root; when neither is given
    every parentless node starts a tree.
    """
    rt = get_runtime()
    try:
        nodes = scene_nodes(rt)
    except SceneQueryError as exc:
        return {"success": False, "message": str(exc), "data": {"failure_reason": "scene_unavailable", "tree": []}}

    root = None
    if node_name or handle is not None:
        resolved, node = resolve_node_object(rt, node_name=node_name, handle=handle)
        if node is None:
            return {
                "success": False,
                "message": resolved.get("message") or "No matching node found",
                "data": {"failure_reason": "node_not_found", "matches": resolved.get("matches", []), "tree": []},
            }
        root = node

    try:
        data = build_hierarchy(
            nodes,
            runtime=rt,
            root=root,
            max_depth=max_depth,
            include_hidden=include_hidden,
            limit=limit,
        )
    except SceneQueryError as exc:
        return {"success": False, "message": str(exc), "data": {"failure_reason": "invalid_request", "tree": []}}

    return {
        "success": True,
        "message": "Built a hierarchy of {} node(s) under {} root(s)".format(data["node_count"], data["root_count"]),
        "data": data,
    }
