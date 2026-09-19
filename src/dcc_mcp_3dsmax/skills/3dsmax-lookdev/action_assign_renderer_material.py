"""Create and assign a renderer-appropriate material to scene nodes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dcc_mcp_3dsmax._lookdev_utils import lookdev_error, lookdev_success
from dcc_mcp_3dsmax._renderer_materials import (
    create_renderer_material,
    detect_renderer_family,
    restore_node_materials,
    rollback_material,
    set_material_color,
    set_material_number,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    material_name: str,
    renderer_type: str = "auto",
    base_color: Optional[List[float]] = None,
    roughness: Optional[float] = None,
    metalness: Optional[float] = None,
    node_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a renderer-appropriate material and assign it to target nodes.

    Roughness, metalness, and base color are written through the renderer-native
    property (for example V-Ray's ``reflectionRoughness`` behind
    ``brdf_useRoughness``) and verified by readback. A parameter the material
    does not accept fails the call and rolls the new material back instead of
    reporting a silent success.
    """
    runtime = get_runtime()
    requested = None if renderer_type in (None, "auto") else renderer_type
    family = detect_renderer_family(runtime, requested=requested)
    material = create_renderer_material(runtime, family, material_name)
    if material is None:
        return lookdev_error(
            "Could not create material for renderer",
            renderer_type=family,
            material_name=material_name,
            failure_reason="material_constructor_unavailable",
        )
    family = detect_renderer_family(runtime, material=material, requested=requested)

    applied: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if base_color is not None:
        result = set_material_color(material, "base_color", base_color, runtime=runtime, renderer=family)
        _collect(result, "base_color", applied, errors, warnings)

    for parameter, value in (("roughness", roughness), ("metalness", metalness)):
        if value is None:
            continue
        result = set_material_number(material, parameter, float(value), runtime=runtime, renderer=family)
        _collect(result, parameter, applied, errors, warnings)

    data = {
        "renderer_type": family,
        "material_name": material_name,
        "material": {
            "name": str(getattr(material, "name", material_name)),
            "type": type(material).__name__,
        },
        "applied_parameters": applied,
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        data["rollback"] = rollback_material(runtime, material)
        data["assigned_node_count"] = 0
        return lookdev_error("Could not apply every requested material parameter", **data)

    targets, target_errors = _resolve_targets(runtime, node_names)
    assigned: List[Dict[str, Any]] = []
    snapshots: List[Tuple[Any, Any]] = []
    for target in targets:
        snapshots.append((target, getattr(target, "material", None)))
        try:
            target.material = material
        except Exception as exc:  # noqa: BLE001 - readback is the fail-closed boundary.
            target_errors.append("Could not assign to {}: {}".format(getattr(target, "name", "?"), exc))
            continue
        if getattr(target, "material", None) is not material:
            target_errors.append("Material readback failed on {}".format(getattr(target, "name", "?")))
            continue
        assigned.append({"node_name": str(getattr(target, "name", ""))})
    data["assigned_node_count"] = len(assigned)
    data["assigned_nodes"] = assigned
    data["target_count"] = len(targets)
    data["assignment_errors"] = target_errors
    data["warnings"] = warnings
    if target_errors:
        # Restore the previous materials first: deleting the new material while
        # nodes still reference it would leave them pointing at nothing.
        data["restore"] = restore_node_materials(runtime, snapshots)
        data["rollback"] = rollback_material(runtime, material)
        return lookdev_error("Could not assign the new material to every target", **data)
    return lookdev_success("Created and assigned renderer material", **data)


def _collect(
    result: Dict[str, Any],
    parameter: str,
    applied: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    warnings: List[str],
) -> None:
    warnings.extend(result.get("warnings", []))
    if result.get("applied"):
        applied.append(
            {
                "parameter": parameter,
                "attribute": result.get("attribute"),
                "renderer": result.get("renderer"),
                "prerequisites": result.get("prerequisites", []),
                "transform": result.get("transform"),
            }
        )
        return
    errors.append(
        {
            "parameter": parameter,
            "renderer": result.get("renderer"),
            "candidates": result.get("candidates", []),
            "error": result.get("error", "parameter_not_applied"),
        }
    )


def _resolve_targets(runtime: Any, node_names: Optional[List[str]]) -> tuple:
    errors: List[str] = []
    if node_names:
        targets = []
        lookup = getattr(runtime, "getNodeByName", None)
        for name in node_names:
            node = None
            if callable(lookup):
                try:
                    node = lookup(name)
                except Exception:  # noqa: BLE001 - fall back to a scene scan.
                    node = None
            if node is None:
                for candidate in getattr(runtime, "objects", None) or []:
                    if str(getattr(candidate, "name", "")) == str(name):
                        node = candidate
                        break
            if node is None:
                errors.append("Node not found: {}".format(name))
                continue
            targets.append(node)
        return targets, errors
    try:
        return list(getattr(runtime, "selection", None) or []), errors
    except Exception:  # noqa: BLE001 - an empty selection is not a failure here.
        return [], errors
