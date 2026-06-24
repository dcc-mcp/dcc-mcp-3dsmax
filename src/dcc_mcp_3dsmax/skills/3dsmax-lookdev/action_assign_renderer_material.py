"""Create and assign a renderer-appropriate material to scene nodes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dcc_mcp_3dsmax._lookdev_utils import lookdev_error, lookdev_success
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
    """Create a renderer-appropriate material and assign to target nodes."""
    runtime = get_runtime()
    resolved_renderer = _resolve_renderer_type(runtime, renderer_type)
    material = _create_renderer_material(runtime, resolved_renderer, material_name, base_color)
    if material is None:
        return lookdev_error(
            "Could not create material for renderer",
            renderer_type=resolved_renderer,
            material_name=material_name,
        )

    warnings: List[str] = []
    if roughness is not None:
        _set_mat_attr(material, "roughness", float(roughness))
    if metalness is not None:
        _set_mat_attr(material, "metalness", float(metalness))

    targets = _resolve_targets(runtime, node_names)
    assigned_count = 0
    for target in targets:
        try:
            setattr(target, "material", material)
            assigned_count += 1
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not assign to {}: {}".format(getattr(target, "name", "?"), exc))

    return lookdev_success(
        "Created and assigned renderer material",
        renderer_type=resolved_renderer,
        material_name=material_name,
        assigned_node_count=assigned_count,
        target_count=len(targets),
        warnings=warnings,
    )


def _resolve_renderer_type(runtime: Any, renderer_type: str) -> str:
    if renderer_type != "auto":
        return renderer_type
    current = getattr(runtime, "currentRenderer", None)
    if current is None:
        return "scanline"
    name = type(current).__name__.lower()
    if "arnold" in name:
        return "arnold"
    if "vray" in name or "v ray" in name:
        return "vray"
    return "scanline"


def _create_renderer_material(
    runtime: Any,
    renderer_type: str,
    name: str,
    base_color: Optional[List[float]],
) -> Any:
    mat_class = None
    if renderer_type == "arnold":
        mat_class = _resolve_class(runtime, ("ArnoldStandard", "ArnoldStandardMaterial", "StandardSurface"))
    elif renderer_type == "vray":
        mat_class = _resolve_class(runtime, ("VRayMtl",))
    if mat_class is None:
        mat_class = _resolve_class(runtime, ("PhysicalMaterial", "StandardMaterial", "Standard"))

    if mat_class is None:
        return None

    try:
        material = mat_class()
    except Exception:  # noqa: BLE001
        return None

    try:
        material.name = name
    except Exception:  # noqa: BLE001
        pass

    if base_color is not None:
        color = _make_color(runtime, base_color)
        if color is not None:
            _set_mat_attr(material, "color", color)
            _set_mat_attr(material, "diffuse", color)
            _set_mat_attr(material, "base_color", color)
            _set_mat_attr(material, "baseColor", color)

    return material


def _resolve_class(runtime: Any, names: tuple) -> Any:
    for name in names:
        try:
            cls = getattr(runtime, name, None)
            if cls is not None:
                return cls
        except Exception:  # noqa: BLE001
            continue
    return None


def _make_color(runtime: Any, rgb: List[float]) -> Any:
    point3 = getattr(runtime, "Point3", None)
    color = getattr(runtime, "Color", None)
    for factory in (point3, color):
        if callable(factory):
            try:
                normalized = [max(0.0, min(255.0, float(c))) / 255.0 for c in rgb]
                return factory(*normalized)
            except Exception:  # noqa: BLE001
                pass
    return None


def _set_mat_attr(material: Any, attr: str, value: Any) -> None:
    for candidate in (attr, "mat_{}".format(attr)):
        try:
            setattr(material, candidate, value)
        except Exception:  # noqa: BLE001
            continue
    for candidate in ("setProperty", "SetProperty", "setParam"):
        method = getattr(material, candidate, None)
        if callable(method):
            try:
                method(attr, value)
            except Exception:  # noqa: BLE001
                continue


def _resolve_targets(runtime: Any, node_names: Optional[List[str]]) -> List[Any]:
    if node_names:
        targets = []
        for name in node_names:
            try:
                obj = getattr(runtime, "objects", None)
                if obj is not None:
                    for node in obj:
                        if str(getattr(node, "name", "")) == name:
                            targets.append(node)
                            break
            except Exception:  # noqa: BLE001
                pass
        return targets
    try:
        sel = getattr(runtime, "selection", None)
        if sel is not None:
            return list(sel)
    except Exception:  # noqa: BLE001
        pass
    return []
