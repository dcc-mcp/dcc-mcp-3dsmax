"""Create a Standard material in 3ds Max."""

# Import future modules
from __future__ import annotations

# Import local modules
from dcc_mcp_3dsmax._material_utils import create_material, material_error, material_identity, material_success
from dcc_mcp_3dsmax._renderer_materials import apply_material_attribute, summarize_attribute_results
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    name: str = "StandardMat",
    diffuse: list = None,
    specular: list = None,
    glossiness: float = 10.0,
) -> dict:
    """Create a Standard material with the given parameters.

    Every requested attribute is verified by readback: a host that ignores one
    of them is reported as a failure instead of a material that silently keeps
    its default value.

    Returns
    -------
    dict
        The action response.
    """
    diffuse = [255, 255, 255] if diffuse is None else diffuse
    specular = [255, 255, 255] if specular is None else specular

    rt = get_runtime()

    mat = create_material(rt, name=name, kind="standard")
    results = [
        apply_material_attribute(mat, attribute, value, runtime=rt)
        for attribute, value in (
            ("diffuse", diffuse),
            ("specular", specular),
            ("glossiness", glossiness),
        )
    ]
    summary = summarize_attribute_results(results)
    data = {
        "material": material_identity(mat, runtime=rt),
        "material_name": name,
        "diffuse": diffuse,
        "specular": specular,
        "glossiness": glossiness,
        "applied": summary["applied"],
        "applied_count": summary["applied_count"],
        "errors": summary["errors"],
        "warnings": summary["warnings"],
    }
    if summary["errors"]:
        return material_error("Could not apply every requested material attribute", **data)
    return material_success("Created material: {}".format(name), **data)
