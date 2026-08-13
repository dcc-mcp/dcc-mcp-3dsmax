"""Helpers for 3ds Max material and map skill scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes, node_identity, resolve_node_objects

COLOR_ATTRS = {
    "diffuse": ("diffuse", "diffuseColor", "base_color", "baseColor"),
    "base_color": ("base_color", "baseColor", "diffuse", "diffuseColor"),
    "specular": ("specular", "specularColor"),
}
NUMERIC_ATTRS = {
    "roughness": ("roughness", "roughnessValue"),
    "metalness": ("metalness", "metalnessValue", "metallic"),
    "opacity": ("opacity", "opacityValue"),
    "glossiness": ("glossiness", "glossinessValue"),
}
MAP_SLOTS = {
    "diffuse": ("diffuseMap", "baseColorMap"),
    "base_color": ("baseColorMap", "diffuseMap"),
    "normal": ("normalMap", "bumpMap"),
    "bump": ("bumpMap", "normalMap"),
    "roughness": ("roughnessMap",),
    "metalness": ("metalnessMap", "metallicMap"),
    "opacity": ("opacityMap",),
}
_MATERIAL_REGISTRY: Dict[int, Dict[str, Any]] = {}


def material_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def material_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def iter_scene_materials(runtime: Any) -> List[Any]:
    """Return known scene materials as a plain list."""
    for attr in ("sceneMaterials", "materials"):
        try:
            values = list(getattr(runtime, attr))
            if values:
                return values
        except Exception:  # noqa: BLE001
            continue
    materials = []
    for node in iter_scene_nodes(runtime):
        material = getattr(node, "material", None)
        if material is not None and material not in materials:
            materials.append(material)
    return materials


def material_identity(material: Any) -> Dict[str, Any]:
    """Return JSON-safe material metadata."""
    return {
        "name": str(getattr(material, "name", "")),
        "type": type(material).__name__,
        "diffuse": _color_value(material, "diffuse"),
        "base_color": _color_value(material, "base_color"),
        "specular": _color_value(material, "specular"),
        "roughness": _numeric_value(material, "roughness"),
        "metalness": _numeric_value(material, "metalness"),
        "opacity": _numeric_value(material, "opacity"),
        "glossiness": _numeric_value(material, "glossiness"),
        "maps": bitmap_connections(material),
    }


def bitmap_connections(material: Any) -> List[Dict[str, Any]]:
    """Return bitmap/map slot connections for one material."""
    rows = []
    seen = set()
    for slot, attrs in MAP_SLOTS.items():
        for attr in attrs:
            if attr in seen:
                continue
            seen.add(attr)
            bitmap = getattr(material, attr, None)
            if bitmap is None:
                continue
            path = _bitmap_path(bitmap)
            rows.append(
                {
                    "slot": slot,
                    "attribute": attr,
                    "map_type": type(bitmap).__name__,
                    "path": path,
                    "exists": Path(path).expanduser().is_file() if path else None,
                }
            )
    return rows


def find_material(runtime: Any, name: str) -> Optional[Any]:
    """Find a material by exact name."""
    for material in iter_scene_materials(runtime):
        if str(getattr(material, "name", "")) == str(name):
            return material
    return _MATERIAL_REGISTRY.get(id(runtime), {}).get(str(name))


def resolve_material(runtime: Any, material_name: str) -> Dict[str, Any]:
    """Resolve a material by name."""
    material = find_material(runtime, material_name)
    if material is None:
        return material_error("Material not found", material_name=material_name)
    return material_success("Resolved material", material=material_identity(material), object=material)


def resolve_material_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Resolve explicit node targets or an explicitly requested selection."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001
            selected = []
        if not selected:
            return material_error("Current selection is empty", objects=[])
        return {"success": True, "message": "Resolved selected nodes", "objects": selected}
    result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
    if not result.get("success"):
        return material_error(result["message"], errors=result.get("errors", []), objects=[])
    result["status"] = "success"
    return result


def create_material(runtime: Any, *, name: str, kind: str, color: Optional[Sequence[float]] = None) -> Any:
    """Create a native material with a conservative fallback chain."""
    constructors = {
        "physical": ("PhysicalMaterial", "Physical_Material", "StandardMaterial"),
        "pbr": ("PhysicalMaterial", "Physical_Material", "StandardMaterial"),
        "standard": ("StandardMaterial",),
    }[kind]
    material = None
    for constructor_name in constructors:
        constructor = getattr(runtime, constructor_name, None)
        if callable(constructor):
            material = constructor()
            break
    if material is None:
        material = type("Material", (), {})()
    material.name = name
    if color is not None:
        set_material_attribute(material, "base_color", color, runtime=runtime)
    _register_material(runtime, material)
    return material


def set_material_attribute(material: Any, attribute: str, value: Any, *, runtime: Any = None) -> List[str]:
    """Set one common material attribute."""
    warnings = []
    if attribute in COLOR_ATTRS:
        converted = _coerce_color(value, runtime=runtime)
        for attr in COLOR_ATTRS[attribute]:
            try:
                setattr(material, attr, converted)
                return warnings
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not set {}: {}".format(attr, exc))
        return warnings
    if attribute in NUMERIC_ATTRS:
        for attr in NUMERIC_ATTRS[attribute]:
            try:
                setattr(material, attr, float(value))
                return warnings
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not set {}: {}".format(attr, exc))
        return warnings
    return ["Unsupported material attribute: {}".format(attribute)]


def assign_material(nodes: Sequence[Any], material: Any) -> List[Dict[str, Any]]:
    """Assign material to nodes and return node summaries."""
    rows = []
    for node in nodes:
        node.material = material
        rows.append({"node": node_identity(node), "material": material_identity(material)})
    return rows


def reset_materials(nodes: Sequence[Any], default_material: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Reset nodes to a default or empty material assignment."""
    rows = []
    for node in nodes:
        node.material = default_material
        rows.append(
            {
                "node": node_identity(node),
                "material": material_identity(default_material) if default_material is not None else None,
            }
        )
    return rows


def create_bitmap(runtime: Any, texture_path: str) -> Any:
    """Create a native bitmap texture with a fallback object."""
    constructor = getattr(runtime, "Bitmaptexture", None) or getattr(runtime, "BitmapTexture", None)
    if callable(constructor):
        try:
            return constructor(filename=texture_path)
        except TypeError:
            bitmap = constructor()
            bitmap.filename = texture_path
            return bitmap
    bitmap = type("BitmapTexture", (), {})()
    bitmap.filename = texture_path
    return bitmap


def assign_bitmap(material: Any, slot: str, bitmap: Any, *, runtime: Any = None) -> List[str]:
    """Assign a bitmap to one common map slot."""
    attrs = MAP_SLOTS.get(slot)
    if not attrs:
        return ["Unsupported map slot: {}".format(slot)]
    warnings = []
    assigned_map = bitmap
    if slot == "normal" and runtime is not None:
        assigned_map, wrapper_warnings = _normal_map(runtime, bitmap)
        warnings.extend(wrapper_warnings)
    for attr in attrs:
        try:
            setattr(material, attr, assigned_map)
            return warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not set map slot {}: {}".format(attr, exc))
    return warnings


def _normal_map(runtime: Any, bitmap: Any) -> Tuple[Any, List[str]]:
    """Wrap a bitmap in Max's native Normal Bump texmap when available."""
    warnings = []
    for constructor_name in ("Normal_Bump", "NormalBump"):
        constructor = getattr(runtime, constructor_name, None)
        if not callable(constructor):
            continue
        try:
            wrapper = constructor()
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not create {}: {}".format(constructor_name, exc))
            continue
        for attr in ("normal_map", "normalMap"):
            try:
                setattr(wrapper, attr, bitmap)
                return wrapper, warnings
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not set Normal Bump {}: {}".format(attr, exc))
    warnings.append("Normal Bump texmap unavailable; assigned the bitmap directly")
    return bitmap, warnings


def _bitmap_path(value: Any) -> str:
    """Resolve a bitmap filename through direct or Normal Bump connections."""
    direct = getattr(value, "filename", "") or getattr(value, "path", "")
    if direct:
        return str(direct)
    for attr in ("normal_map", "normalMap", "bump_map", "bumpMap"):
        nested = getattr(value, attr, None)
        if nested is not None and nested is not value:
            path = getattr(nested, "filename", "") or getattr(nested, "path", "")
            if path:
                return str(path)
    return ""


def missing_textures(materials: Sequence[Any]) -> List[Dict[str, Any]]:
    """Return missing bitmap paths for material maps."""
    rows = []
    for material in materials:
        for connection in bitmap_connections(material):
            if connection["path"] and connection["exists"] is False:
                rows.append({"material": material_identity(material), "connection": connection})
    return rows


def _register_material(runtime: Any, material: Any) -> None:
    _MATERIAL_REGISTRY.setdefault(id(runtime), {})[str(getattr(material, "name", ""))] = material
    for attr in ("sceneMaterials", "materials"):
        values = getattr(runtime, attr, None)
        if values is None:
            continue
        try:
            if material in values:
                return
        except Exception:  # noqa: BLE001
            pass
        if isinstance(values, list):
            values.append(material)
            return
        append = getattr(runtime, "append", None)
        if callable(append):
            append(values, material)
            return


def _coerce_color(value: Any, *, runtime: Any = None) -> Any:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        channels = [float(value[0]), float(value[1]), float(value[2])]
    else:
        channels = [float(value), float(value), float(value)]
    color = getattr(runtime, "color", None) if runtime is not None else None
    return color(*channels) if callable(color) else channels


def _color_value(material: Any, attribute: str) -> Optional[List[float]]:
    for attr in COLOR_ATTRS[attribute]:
        value = getattr(material, attr, None)
        if value is None:
            continue
        if all(hasattr(value, channel) for channel in ("r", "g", "b")):
            return [float(value.r), float(value.g), float(value.b)]
        try:
            return _coerce_color(value)
        except Exception:  # noqa: BLE001
            return [str(value)]
    return None


def _numeric_value(material: Any, attribute: str) -> Optional[float]:
    for attr in NUMERIC_ATTRS[attribute]:
        value = getattr(material, attr, None)
        if value is None:
            continue
        try:
            return float(value)
        except Exception:  # noqa: BLE001
            return None
    return None
