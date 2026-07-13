"""Set a keyframe on an object in 3ds Max."""

# Import future modules
from __future__ import annotations

# Import local modules
from dcc_mcp_3dsmax._animation_utils import set_transform_key
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_name: str = None,
    time: int = None,
    property: str = "position",
    value: list = None,
) -> dict:
    """Set a keyframe on the specified object.

    Returns
    -------
    dict
        The action response.
    """
    if not node_name:
        return {"success": False, "message": "node_name is required", "data": {}}

    if time is None:
        return {"success": False, "message": "time (frame) is required", "data": {}}

    rt = get_runtime()

    # Get the node
    node = rt.getNodeByName(node_name)
    if node is None:
        return {"success": False, "message": f"Node not found: {node_name}", "data": {}}

    if not value or len(value) < 3:
        return {"success": False, "message": "value must contain at least three numbers", "data": {}}
    result = set_transform_key(rt, node, frame=time, property_name=property, value=value)
    if not result.get("success"):
        return result

    return {
        "success": True,
        "message": f"Set keyframe on {node_name}.{property} at frame {time}",
        "data": {
            "node_name": node_name,
            "time": time,
            "property": property,
        },
    }
