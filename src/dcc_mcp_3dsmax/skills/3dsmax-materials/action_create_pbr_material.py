"""Create PBR-friendly materials."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._material_utils import create_material, material_error, material_identity, material_success
from dcc_mcp_3dsmax._renderer_materials import apply_material_attribute, summarize_attribute_results
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    name: str,
    base_color: Optional[list] = None,
    roughness: Optional[float] = None,
    metalness: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a PBR-friendly material.

    Every requested attribute is verified by readback so the response can never
    describe a value the material does not hold.
    """
    runtime = get_runtime()
    material = create_material(runtime, name=name, kind="pbr")
    results = [
        apply_material_attribute(material, attribute, value, runtime=runtime)
        for attribute, value in (
            ("base_color", base_color),
            ("roughness", roughness),
            ("metalness", metalness),
        )
        if value is not None
    ]
    summary = summarize_attribute_results(results)
    data = {
        "material": material_identity(material, runtime=runtime),
        "applied": summary["applied"],
        "applied_count": summary["applied_count"],
        "errors": summary["errors"],
        "warnings": summary["warnings"],
    }
    if summary["errors"]:
        return material_error("Could not apply every requested material attribute", **data)
    return material_success("Created PBR material", **data)
