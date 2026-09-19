"""Read a compact set of properties from a 3ds Max node."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._scene_utils import (
    node_bounding_box,
    read_property,
    resolve_node_object,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

DEFAULT_PROPERTIES = (
    "name",
    "pos",
    "rotation",
    "scale",
    "transform",
    "pivot",
    "parent",
    "material",
    "wirecolor",
    "visibility",
    "isHidden",
    "isFrozen",
    "castShadows",
    "receiveShadows",
    "renderable",
    "primaryVisibility",
    "min",
    "max",
)


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    properties: Optional[Sequence[str]] = None,
    include_bounding_box: bool = True,
) -> Dict[str, Any]:
    """Return a compact property snapshot for one node.

    Unknown or unreadable properties are reported in ``data.unavailable`` so an
    agent can tell "property missing" apart from "property read as empty".
    """
    requested: Optional[List[str]] = None
    if properties:
        requested = [str(item) for item in properties if str(item).strip()]
        if not requested:
            return {
                "success": False,
                "message": "properties must contain at least one non-empty property name",
                "data": {"properties": list(properties or [])},
            }

    names = requested if requested is not None else list(DEFAULT_PROPERTIES)

    result, node = resolve_node_object(get_runtime(), node_name=node_name, handle=handle)
    if node is None:
        return {"success": False, "message": result["message"], "data": result}

    values: Dict[str, Any] = {}
    unavailable: List[Dict[str, Any]] = []
    for name in names:
        entry = read_property(node, name)
        if entry.get("available"):
            values[name] = entry["value"]
        else:
            unavailable.append(entry)

    if requested is not None and not values:
        return {
            "success": False,
            "message": "None of the requested properties could be read",
            "data": {"node": result.get("node"), "unavailable": unavailable},
        }

    data: Dict[str, Any] = {
        "node": result.get("node"),
        "properties": values,
        "unavailable": unavailable,
        "requested": list(names),
    }
    if include_bounding_box:
        data["bounding_box"] = node_bounding_box(node)

    message = "Read {} properties".format(len(values))
    if unavailable:
        message = "{} ({} unavailable)".format(message, len(unavailable))
    return {"success": True, "message": message, "data": data}
