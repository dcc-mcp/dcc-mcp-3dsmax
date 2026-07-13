"""Apply a material to objects in 3ds Max."""

# Import future modules
from __future__ import annotations

# Import local modules
from dcc_mcp_3dsmax._material_utils import find_material
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(material_name: str = None, node_names: list = None) -> dict:
    """Apply a material to specified objects or selection.

    Returns
    -------
    dict
        The action response.
    """
    if not material_name:
        return {"success": False, "message": "material_name is required", "data": {}}

    rt = get_runtime()

    mat = find_material(rt, material_name)

    if mat is None:
        return {"success": False, "message": f"Material not found: {material_name}", "data": {}}

    # Get target nodes
    if node_names:
        nodes = [rt.getNodeByName(n) for n in node_names]
        nodes = [n for n in nodes if n is not None]
    else:
        # Use current selection
        nodes = list(rt.selection)

    if not nodes:
        return {"success": False, "message": "No nodes to apply material to", "data": {}}

    # Apply material
    applied_count = 0
    for node in nodes:
        node.material = mat
        applied_count += 1

    return {
        "success": True,
        "message": f"Applied material '{material_name}' to {applied_count} node(s)",
        "data": {
            "material_name": material_name,
            "applied_count": applied_count,
        },
    }
