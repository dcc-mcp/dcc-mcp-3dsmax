"""Set material attributes through renderer-aware native properties."""

from __future__ import annotations

from typing import Any, Dict, List

from dcc_mcp_3dsmax._material_utils import (
    find_material,
    material_error,
    material_identity,
    material_success,
    set_material_attribute,
)
from dcc_mcp_3dsmax._renderer_materials import (
    COLOR_PLANS,
    NUMERIC_PLANS,
    detect_renderer_family,
    set_material_color,
    set_material_number,
    verify_generic_attribute,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

COLOR_PARAMS = ("diffuse", "base_color", "specular", "emission", "reflection_color")
NUMERIC_PARAMS = ("roughness", "metalness", "opacity", "glossiness", "ior")


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
    applied: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for attribute, value in attributes.items():
        result = _apply_attribute(runtime, material, attribute, value, family=family)
        warnings.extend(result.get("warnings", []))
        if result.get("applied"):
            applied.append(
                {
                    "attribute": attribute,
                    "native_attribute": result.get("native_attribute"),
                    "renderer": result.get("renderer", family),
                }
            )
            continue
        errors.append(result)

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


def _apply_attribute(
    runtime: Any,
    material: Any,
    attribute: str,
    value: Any,
    *,
    family: str,
) -> Dict[str, Any]:
    """Write one attribute, preferring the renderer-native property path."""
    if attribute in NUMERIC_PARAMS and attribute in NUMERIC_PLANS.get(family, {}):
        result = set_material_number(material, attribute, value, runtime=runtime, renderer=family)
        return {
            "applied": result.get("applied", False),
            "attribute": attribute,
            "native_attribute": result.get("attribute"),
            "renderer": result.get("renderer", family),
            "warnings": result.get("warnings", []),
            "error": result.get("error"),
        }
    if attribute in COLOR_PARAMS and attribute in COLOR_PLANS.get(family, {}):
        result = set_material_color(material, attribute, value, runtime=runtime, renderer=family)
        return {
            "applied": result.get("applied", False),
            "attribute": attribute,
            "native_attribute": result.get("attribute"),
            "renderer": result.get("renderer", family),
            "warnings": result.get("warnings", []),
            "error": result.get("error"),
        }
    warnings = set_material_attribute(material, attribute, value, runtime=runtime)
    unsupported = [item for item in warnings if item.startswith("Unsupported material attribute")]
    if unsupported:
        return {
            "applied": False,
            "attribute": attribute,
            "native_attribute": None,
            "renderer": family,
            "warnings": warnings,
            "error": unsupported[0],
        }
    # ``set_material_attribute`` reports host rejections as warnings, so a host
    # that refused every candidate would still look like a success. Verify the
    # write by reading it back before reporting anything as applied.
    verification = verify_generic_attribute(material, attribute, value, runtime=runtime)
    warnings.extend(verification.get("warnings", []))
    if not verification.get("applied"):
        return {
            "applied": False,
            "attribute": attribute,
            "native_attribute": None,
            "renderer": family,
            "candidates": verification.get("candidates", []),
            "warnings": warnings,
            "error": verification.get("error", "attribute_not_applied"),
        }
    return {
        "applied": True,
        "attribute": attribute,
        "native_attribute": verification.get("attribute"),
        "renderer": family,
        "warnings": warnings,
        "error": None,
    }
