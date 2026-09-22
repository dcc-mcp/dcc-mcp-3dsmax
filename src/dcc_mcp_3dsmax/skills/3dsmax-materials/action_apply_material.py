"""Apply a material to objects in 3ds Max."""

# Import future modules
from __future__ import annotations

# Import local modules
from dcc_mcp_3dsmax._material_utils import assign_material, find_material, material_error, material_success
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(material_name: str = None, node_names: list = None) -> dict:
    """Apply a material to specified objects or selection.

    Nodes that cannot be resolved and nodes whose assignment the host did not
    keep are reported instead of being dropped from the count, so
    ``applied_count`` always means \"nodes that verify as carrying the
    material\".

    Returns
    -------
    dict
        The action response.
    """
    if not material_name:
        return material_error("material_name is required")

    rt = get_runtime()

    mat = find_material(rt, material_name)

    if mat is None:
        return material_error("Material not found", material_name=material_name)

    # Get target nodes
    skipped = []
    if node_names:
        nodes = []
        for node_name in node_names:
            node = rt.getNodeByName(node_name)
            if node is None:
                skipped.append(str(node_name))
                continue
            nodes.append(node)
    else:
        # Use current selection
        nodes = list(rt.selection)

    if not nodes:
        return material_error("No nodes to apply material to", material_name=material_name, skipped=skipped)

    applied, errors = assign_material(nodes, mat)
    data = {
        "material_name": material_name,
        "applied": applied,
        "applied_count": len(applied),
        "requested_count": len(nodes),
        "skipped": skipped,
        "errors": errors,
    }
    if errors or skipped:
        return material_error("Could not apply the material to every requested node", **data)
    return material_success("Applied material '{}' to {} node(s)".format(material_name, len(applied)), **data)
