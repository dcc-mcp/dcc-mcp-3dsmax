"""Read the dependency graph around one node through the ``refs`` interface.

``refs.dependents`` answers "what references this", ``refs.dependentnodes`` the
same question restricted to nodes and followed recursively, and
``refs.dependsOn`` the reverse. A material shared by forty meshes, an instanced
base object, and a node parented to a helper are all dependency edges, and none
of them are visible in a flat node list.

A host that does not expose ``refs`` - or whose ``refs`` calls all fail - gets a
refusal rather than an empty graph, because "nothing depends on this" and "I
could not look" are different answers.
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
from typing import Any, Dict, Optional

# Import local modules
from dcc_mcp_3dsmax._scene_query import SceneQueryError, collect_dependencies, scene_nodes
from dcc_mcp_3dsmax._scene_utils import resolve_node_object
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """Report what a node depends on and what depends on it."""
    rt = get_runtime()
    if not node_name and handle is None:
        return {
            "success": False,
            "message": "node_name or handle is required",
            "data": {"failure_reason": "missing_target"},
        }

    try:
        nodes = scene_nodes(rt)
    except SceneQueryError as exc:
        return {"success": False, "message": str(exc), "data": {"failure_reason": "scene_unavailable"}}

    resolved, node = resolve_node_object(rt, node_name=node_name, handle=handle)
    if node is None:
        return {
            "success": False,
            "message": resolved.get("message") or "No matching node found",
            "data": {"failure_reason": "node_not_found", "matches": resolved.get("matches", [])},
        }
    if not any(candidate is node for candidate in nodes):
        # ``resolve_node_object`` can fall back to getNodeByName, which returns
        # a wrapper the scene walk never produced. Say so instead of reading
        # dependencies through an object the enumeration does not know about.
        return {
            "success": False,
            "message": "{} is not in the enumerated scene nodes, so its dependencies cannot be read".format(
                resolved["node"]["node_name"]
            ),
            "data": {"failure_reason": "node_not_in_scene", "node": resolved["node"]},
        }

    try:
        data = collect_dependencies(rt, node, limit=limit)
    except SceneQueryError as exc:
        return {"success": False, "message": str(exc), "data": {"failure_reason": "refs_unavailable"}}

    sections = [data["direct_dependents"], data["dependent_nodes"], data["depends_on"]]
    resolved_sections = [section for section in sections if section["available"]]
    return {
        "success": True,
        "message": "Read {} dependency section(s) for {}".format(len(resolved_sections), data["node"]["node_name"]),
        "data": data,
    }
