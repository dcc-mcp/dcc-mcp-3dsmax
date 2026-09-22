"""Set material attributes through renderer-aware native properties."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._material_utils import (
    find_material,
    material_error,
    material_identity,
    material_success,
)
from dcc_mcp_3dsmax._renderer_materials import (
    apply_material_attribute,
    detect_renderer_family,
    summarize_attribute_results,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    material_name: str,
    attributes: Dict[str, Any],
    renderer: str = "auto",
) -> Dict[str, Any]:
    """Set common and renderer-native material attributes."""
    runtime = get_runtime()
    material = find_material(runtime, material_name)
    if material is None:
        return material_error("Material not found", material_name=material_name)

    requested = None if renderer == "auto" else renderer
    family = detect_renderer_family(runtime, material=material, requested=requested)
    results = [
        apply_material_attribute(material, attribute, value, runtime=runtime, renderer=family)
        for attribute, value in attributes.items()
    ]
    summary = summarize_attribute_results(results)
    applied = summary["applied"]
    errors = summary["errors"]
    warnings = summary["warnings"]

    data = {
        "material": material_identity(material),
        "renderer": family,
        "applied": applied,
        "applied_attribute_count": len(applied),
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        return material_error("Could not apply every requested material attribute", **data)
    return material_success("Updated material attributes", **data)
