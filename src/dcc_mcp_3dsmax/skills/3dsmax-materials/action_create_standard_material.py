"""Create a Standard material in 3ds Max."""

# Import future modules
from __future__ import annotations

# Import local modules
from dcc_mcp_3dsmax._material_utils import create_material, set_material_attribute
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    name: str = "StandardMat",
    diffuse: list = None,
    specular: list = None,
    glossiness: float = 10.0,
) -> dict:
    """Create a Standard material with the given parameters.

    Returns
    -------
    dict
        The action response.
    """
    diffuse = [255, 255, 255] if diffuse is None else diffuse
    specular = [255, 255, 255] if specular is None else specular

    rt = get_runtime()

    mat = create_material(rt, name=name, kind="standard")
    set_material_attribute(mat, "diffuse", diffuse, runtime=rt)
    set_material_attribute(mat, "specular", specular, runtime=rt)
    set_material_attribute(mat, "glossiness", glossiness, runtime=rt)

    return {
        "success": True,
        "message": f"Created material: {name}",
        "data": {
            "material_name": name,
            "diffuse": diffuse,
            "specular": specular,
            "glossiness": glossiness,
        },
    }
