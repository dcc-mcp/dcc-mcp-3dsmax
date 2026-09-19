"""Renderer-aware material parameters, texture slots, and texture-set wiring.

This module is the single place that knows how a canonical PBR parameter or map
slot maps onto a concrete 3ds Max material class (VRayMtl, Arnold standard
surface, Physical Material, Standard). Every write goes through the same
fail-closed pipeline:

1. Resolve the renderer family (explicit request, material class, or renderer).
2. Walk an ordered list of candidate native attributes for that family.
3. Skip candidates the host does not expose (``isProperty`` gate).
4. Set the value, then read it back and compare it with the request.
5. Report the applied attribute, or an explicit error when nothing took.

A write that the host silently ignores is reported as an error, never as a
success. That is the contract that keeps agents from believing a shader change
landed when it did not.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._material_utils import (
    COLOR_ATTRS as LEGACY_COLOR_ATTRS,
)
from dcc_mcp_3dsmax._material_utils import (
    MAP_SLOTS as LEGACY_MAP_SLOTS,
)
from dcc_mcp_3dsmax._material_utils import (
    NUMERIC_ATTRS as LEGACY_NUMERIC_ATTRS,
)
from dcc_mcp_3dsmax._material_utils import bitmap_connections, wrap_normal_map
from dcc_mcp_3dsmax._render_utils import current_renderer
from dcc_mcp_3dsmax._scene_utils import node_identity

RENDERER_FAMILIES = ("vray", "arnold", "physical", "standard")

MATERIAL_CLASS_HINTS = (
    ("vray", ("vray",)),
    ("arnold", ("arnold", "standardsurface", "standard_surface")),
    ("physical", ("physical",)),
    ("standard", ("standard",)),
)

MATERIAL_CONSTRUCTORS = {
    "vray": ("VRayMtl", "VRayMtlWrapper"),
    "arnold": ("ArnoldStandardSurface", "ArnoldStandard", "ArnoldStandardMaterial"),
    "physical": ("PhysicalMaterial", "Physical_Material", "StandardMaterial"),
    "standard": ("StandardMaterial", "Standard"),
}

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".tga",
    ".bmp",
    ".exr",
    ".hdr",
    ".dds",
    ".tx",
    ".psd",
)

# Canonical slot -> filename tokens that identify that map inside a texture set.
TEXTURE_TOKENS: Dict[str, Tuple[str, ...]] = {
    "metalness": ("metalness", "metallic", "metal", "mtl"),
    "displacement": ("displacement", "disp", "height"),
    "roughness": ("roughness", "rough", "rgh"),
    "diffuse": ("diffuse", "basecolor", "base_color", "albedo", "color", "colour", "col", "diff"),
    "bump": ("bump", "bmp"),
    "normal": ("normal", "normalgl", "nrm", "normals"),
    "opacity": ("opacity", "alpha", "transparency", "trans"),
    "specular": ("specular", "specularcolor", "spec"),
    "emission": ("emission", "emissive", "emiss", "selfillum", "selfillumination"),
    "reflection": ("reflection", "reflect", "refl"),
    "ao": ("ao", "ambientocclusion", "occlusion"),
}

# Slots that can be wired on the renderers we support. Keys are canonical slots.
MAP_SLOT_PLANS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "vray": {
        "diffuse": ("texmap_diffuse", "texmap_diffuseMultiplier"),
        "base_color": ("texmap_diffuse",),
        "roughness": ("texmap_roughness", "texmap_reflectionRoughness", "texmap_reflectionGlossiness"),
        "metalness": ("texmap_metalness",),
        "bump": ("texmap_bump",),
        "normal": ("texmap_bump",),
        "displacement": ("texmap_displacement",),
        "opacity": ("texmap_opacity",),
        "reflection": ("texmap_reflection",),
        "refraction": ("texmap_refraction",),
        "emission": ("texmap_emission", "texmap_selfIllumination"),
    },
    "arnold": {
        "diffuse": ("baseColorMap", "base_color_map", "color_map", "diffuseMap"),
        "base_color": ("baseColorMap", "base_color_map", "color_map", "diffuseMap"),
        "roughness": ("specularRoughnessMap", "specular_roughness_map", "roughnessMap"),
        "metalness": ("metalnessMap", "metalness_map", "metallicMap"),
        "bump": ("bumpMap", "bump_map"),
        "normal": ("normalMap", "normal_map"),
        "displacement": ("displacementMap", "displacement_map"),
        "opacity": ("opacityMap", "opacity_map"),
        "emission": ("emissionMap", "emission_map"),
        "specular": ("specularMap", "specular_map"),
    },
    "physical": {
        "diffuse": ("base_color_map", "baseColorMap", "diffuseMap"),
        "base_color": ("base_color_map", "baseColorMap", "diffuseMap"),
        "roughness": ("base_roughness_map", "roughnessMap"),
        "metalness": ("base_metalness_map", "metalnessMap"),
        "bump": ("bump_map", "bumpMap"),
        "normal": ("bump_map", "normal_map"),
        "displacement": ("displacement_map", "displacementMap"),
        "opacity": ("cutout_map", "opacityMap"),
        "emission": ("emission_map", "emissionMap"),
    },
    "standard": {
        "diffuse": ("diffuseMap",),
        "base_color": ("diffuseMap",),
        "roughness": ("roughnessMap",),
        "glossiness": ("glossinessMap",),
        "metalness": ("metalnessMap", "metallicMap"),
        "bump": ("bumpMap",),
        "normal": ("normalMap", "bumpMap"),
        "displacement": ("displacementMap",),
        "opacity": ("opacityMap",),
        "specular": ("specularMap",),
        "emission": ("selfIllumMap", "emissionMap"),
    },
}

# Canonical numeric parameter -> ordered write plans.
#
# A plan is ``(attribute, transform, prerequisites)``; ``transform`` converts the
# canonical value into the value the native property expects, and prerequisites
# are ``(attribute, value)`` pairs the native property needs before it is
# honoured (V-Ray 5+ ignores ``reflectionRoughness`` while
# ``brdf_useRoughness`` is false).
NUMERIC_PLANS: Dict[str, Dict[str, Tuple[Tuple[str, Optional[str], Tuple[Tuple[str, Any], ...]], ...]]] = {
    "vray": {
        "roughness": (
            ("reflectionRoughness", None, (("brdf_useRoughness", True),)),
            ("reflection_glossiness", "inverse", (("brdf_useRoughness", False),)),
            ("roughness", None, ()),
        ),
        "glossiness": (
            ("reflection_glossiness", None, (("brdf_useRoughness", False),)),
            ("reflectionRoughness", "inverse", (("brdf_useRoughness", True),)),
        ),
        "metalness": (("metalness", None, ()),),
        "opacity": (("opacity", None, ()),),
        "ior": (("ior", None, ()), ("refraction_ior", None, ())),
    },
    "arnold": {
        "roughness": (
            ("specular_roughness", None, ()),
            ("specularRoughness", None, ()),
            ("roughness", None, ()),
        ),
        "metalness": (("metalness", None, ()), ("metallic", None, ())),
        "opacity": (("opacity", None, ()),),
        "ior": (("ior", None, ()),),
    },
    "physical": {
        "roughness": (
            ("base_roughness", None, ()),
            ("roughness", None, ()),
            ("roughnessValue", None, ()),
        ),
        "metalness": (
            ("base_metalness", None, ()),
            ("metalness", None, ()),
            ("metallic", None, ()),
        ),
        "opacity": (("opacity", None, ()),),
        "glossiness": (("glossiness", None, ()),),
        "ior": (("base_ior", None, ()), ("ior", None, ())),
    },
    "standard": {
        "roughness": (("roughness", None, ()), ("roughnessValue", None, ())),
        "metalness": (("metalness", None, ()), ("metallic", None, ())),
        "opacity": (("opacity", None, ()),),
        "glossiness": (("glossiness", None, ()),),
        "ior": (("ior", None, ()),),
    },
}

COLOR_PLANS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "vray": {
        "diffuse": ("diffuse", "base_color"),
        "base_color": ("diffuse", "base_color"),
        "reflection_color": ("reflection",),
        "emission": ("selfIllumination", "emission"),
    },
    "arnold": {
        "diffuse": ("base_color", "baseColor", "diffuse"),
        "base_color": ("base_color", "baseColor", "diffuse"),
        "specular": ("specular",),
        "emission": ("emission_color", "emissionColor"),
    },
    "physical": {
        "diffuse": ("base_color", "baseColor", "diffuse"),
        "base_color": ("base_color", "baseColor", "diffuse"),
        "specular": ("reflect_color", "specular"),
        "emission": ("emission_color", "emissionColor"),
    },
    "standard": {
        "diffuse": ("diffuse", "base_color"),
        "base_color": ("diffuse", "base_color"),
        "specular": ("specular",),
        "emission": ("selfIllumColor", "emission"),
    },
}

# Slots whose bitmap must be wrapped in a native Normal Bump texmap first.
NORMAL_WRAPPED_SLOTS = ("normal",)


# Candidate resolution always tries the historical generic spellings first so
# existing scenes and host doubles keep their behaviour, then appends the
# renderer-native spelling. On a live host ``isProperty`` removes the generic
# spellings the material class does not own, so the native name wins there.


def map_slot_candidates(family: str, slot: str) -> Tuple[str, ...]:
    """Return ordered native attribute candidates for one canonical map slot."""
    return _merge(LEGACY_MAP_SLOTS.get(slot, ()), MAP_SLOT_PLANS.get(family, {}).get(slot, ()))


def numeric_plans(family: str, parameter: str) -> Tuple[Tuple[str, Optional[str], Tuple[Tuple[str, Any], ...]], ...]:
    """Return ordered write plans for one canonical numeric parameter.

    Native plans keep their declaration order: flipping the order can change
    which native property a host that exposes both spellings ends up using, and
    with it the BRDF mode (see the V-Ray glossiness/roughness pair).
    """
    native: Dict[str, Tuple[str, Optional[str], Tuple[Tuple[str, Any], ...]]] = {}
    native_order: List[str] = []
    for attribute, transform, prerequisites in NUMERIC_PLANS.get(family, {}).get(parameter, ()):
        native[attribute] = (attribute, transform, prerequisites)
        if attribute not in native_order:
            native_order.append(attribute)
    plans: List[Tuple[str, Optional[str], Tuple[Tuple[str, Any], ...]]] = []
    for attribute in LEGACY_NUMERIC_ATTRS.get(parameter, ()):
        plans.append(native.pop(attribute, (attribute, None, ())))
    for attribute in native_order:
        if attribute in native:
            plans.append(native[attribute])
    return tuple(plans)


def color_candidates(family: str, parameter: str) -> Tuple[str, ...]:
    """Return ordered native attribute candidates for one canonical color."""
    return _merge(LEGACY_COLOR_ATTRS.get(parameter, ()), COLOR_PLANS.get(family, {}).get(parameter, ()))


def generic_attribute_candidates(parameter: str) -> Tuple[str, ...]:
    """Return the historical generic attribute names for one parameter."""
    if parameter in LEGACY_COLOR_ATTRS:
        return tuple(LEGACY_COLOR_ATTRS[parameter])
    if parameter in LEGACY_NUMERIC_ATTRS:
        return tuple(LEGACY_NUMERIC_ATTRS[parameter])
    return (parameter,)


def verify_generic_attribute(
    material: Any,
    parameter: str,
    value: Any,
    *,
    runtime: Any = None,
) -> Dict[str, Any]:
    """Confirm a generic attribute write actually landed on the material.

    ``set_material_attribute`` returns warnings instead of raising, so a host
    that rejects every candidate still looks like a success. This reads the
    value back through the same candidate list and reports the native attribute
    that really holds it, or an explicit failure when none does.
    """
    candidates = generic_attribute_candidates(parameter)
    is_color = parameter in LEGACY_COLOR_ATTRS
    expected = _coerce_channels(value) if is_color else _coerce_scalar(value)
    warnings: List[str] = []
    for attribute in candidates:
        if not _host_has_property(runtime, material, attribute):
            continue
        if is_color:
            readback = _read_channels(runtime, material, attribute)
        else:
            readback = _read_number(runtime, material, attribute)
        matched = (
            _channels_match(readback, expected)
            if is_color
            else (readback is not None and math.isclose(readback, expected, rel_tol=1e-6, abs_tol=1e-6))
        )
        if matched:
            return {"applied": True, "attribute": attribute, "warnings": warnings}
        warnings.append("{} read back {!r} after writing {}".format(attribute, readback, expected))
    return {
        "applied": False,
        "attribute": None,
        "candidates": list(candidates),
        "warnings": warnings,
        "error": "No native attribute holds the requested {} value".format(parameter),
    }


def renderer_bitmap_connections(
    material: Any,
    *,
    runtime: Any = None,
    renderer: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return bitmap connections including the renderer-native slot names."""
    family = detect_renderer_family(runtime, material=material, requested=renderer)
    extra = {slot: map_slot_candidates(family, slot) for slot in MAP_SLOT_PLANS.get(family, {})}
    return bitmap_connections(material, extra_slots=extra)


def _merge(legacy: Sequence[str], native: Sequence[str]) -> Tuple[str, ...]:
    merged: List[str] = []
    for attribute in tuple(legacy) + tuple(native):
        if attribute not in merged:
            merged.append(attribute)
    return tuple(merged)


def renderer_material_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def renderer_material_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def material_class_names(material: Any, *, runtime: Any = None) -> List[str]:
    """Return every class name the host reports for one material."""
    names: List[str] = []
    if runtime is not None:
        for method_name in ("classOf", "superClassOf"):
            method = getattr(runtime, method_name, None)
            if not callable(method):
                continue
            try:
                names.append(str(method(material)))
            except Exception:  # noqa: BLE001 - host wrappers reject introspection.
                continue
    names.append(type(material).__name__)
    return names


def detect_renderer_family(
    runtime: Any = None,
    material: Any = None,
    requested: Optional[str] = None,
) -> str:
    """Resolve the renderer family from an explicit request, material, or host."""
    if requested and str(requested).strip().lower() not in ("", "auto"):
        normalized = str(requested).strip().lower().replace("-", "").replace(" ", "")
        for family in RENDERER_FAMILIES:
            if normalized == family:
                return family
        for family, hints in MATERIAL_CLASS_HINTS:
            if any(hint in normalized for hint in hints):
                return family
        if "scanline" in normalized or normalized in {"default", "art"}:
            return "standard"
        return "physical"

    if material is not None:
        text = " ".join(material_class_names(material, runtime=runtime)).lower()
        for family, hints in MATERIAL_CLASS_HINTS:
            if any(hint in text for hint in hints):
                return family

    if runtime is not None:
        renderer = current_renderer(runtime)
        if renderer is not None:
            class_of = getattr(runtime, "classOf", None)
            try:
                name = str(class_of(renderer)) if callable(class_of) else type(renderer).__name__
            except Exception:  # noqa: BLE001 - fall back to the Python type name.
                name = type(renderer).__name__
            normalized = name.lower().replace("-", "").replace(" ", "")
            for family, hints in MATERIAL_CLASS_HINTS:
                if any(hint in normalized for hint in hints):
                    return family
    return "physical"


def create_renderer_material(runtime: Any, family: str, name: str) -> Optional[Any]:
    """Create a material instance for one renderer family."""
    for constructor_name in MATERIAL_CONSTRUCTORS.get(family, ()):
        constructor = getattr(runtime, constructor_name, None)
        if not callable(constructor):
            continue
        try:
            material = constructor()
        except Exception:  # noqa: BLE001 - try the next constructor in the chain.
            continue
        try:
            material.name = name
        except Exception:  # noqa: BLE001 - naming is best effort.
            pass
        return material
    return None


def supported_map_slots(family: str) -> Tuple[str, ...]:
    """Return the canonical map slots a renderer family can wire."""
    return tuple(sorted(MAP_SLOT_PLANS.get(family, {})))


def supported_parameters(family: str) -> Dict[str, Any]:
    """Return the canonical parameters a renderer family can write."""
    return {
        "numeric": tuple(sorted(NUMERIC_PLANS.get(family, {}))),
        "color": tuple(sorted(COLOR_PLANS.get(family, {}))),
        "maps": supported_map_slots(family),
    }


def set_material_number(
    material: Any,
    parameter: str,
    value: Any,
    *,
    runtime: Any = None,
    renderer: Optional[str] = None,
) -> Dict[str, Any]:
    """Write one canonical numeric parameter with prerequisite and readback checks."""
    family = detect_renderer_family(runtime, material=material, requested=renderer)
    plans = numeric_plans(family, parameter)
    if not plans:
        return {
            "applied": False,
            "parameter": parameter,
            "renderer": family,
            "attribute": None,
            "error": "Renderer {} does not support the {} parameter".format(family, parameter),
        }
    try:
        requested = float(value)
    except (TypeError, ValueError):
        return {
            "applied": False,
            "parameter": parameter,
            "renderer": family,
            "attribute": None,
            "error": "Parameter {} requires a numeric value, got {!r}".format(parameter, value),
        }

    warnings: List[str] = []
    for attribute, transform, prerequisites in plans:
        if not _host_has_property(runtime, material, attribute):
            continue
        expected = _apply_transform(requested, transform)
        prereq_warning = _apply_prerequisites(runtime, material, prerequisites)
        if prereq_warning is not None:
            warnings.append(prereq_warning)
            continue
        try:
            setattr(material, attribute, expected)
        except Exception as exc:  # noqa: BLE001 - readback is the fail-closed boundary.
            warnings.append("Could not set {}: {}".format(attribute, exc))
            continue
        readback = _read_number(runtime, material, attribute)
        if readback is None or not math.isclose(readback, expected, rel_tol=1e-6, abs_tol=1e-6):
            warnings.append(
                "{} read back {!r} after writing {}".format(attribute, readback, expected),
            )
            continue
        return {
            "applied": True,
            "parameter": parameter,
            "renderer": family,
            "attribute": attribute,
            "transform": transform,
            "requested": requested,
            "written": expected,
            "prerequisites": [attribute for attribute, _value in prerequisites],
            "warnings": warnings,
        }
    return {
        "applied": False,
        "parameter": parameter,
        "renderer": family,
        "attribute": None,
        "candidates": [attribute for attribute, _transform, _prereqs in plans],
        "warnings": warnings,
        "error": "No native {} attribute accepted {} on this material".format(parameter, requested),
    }


def set_material_color(
    material: Any,
    parameter: str,
    value: Sequence[float],
    *,
    runtime: Any = None,
    renderer: Optional[str] = None,
) -> Dict[str, Any]:
    """Write one canonical color parameter and verify it through readback."""
    family = detect_renderer_family(runtime, material=material, requested=renderer)
    attributes = color_candidates(family, parameter)
    if not attributes:
        return {
            "applied": False,
            "parameter": parameter,
            "renderer": family,
            "attribute": None,
            "error": "Renderer {} does not support the {} parameter".format(family, parameter),
        }
    channels = _coerce_channels(value)
    warnings: List[str] = []
    for attribute in attributes:
        if not _host_has_property(runtime, material, attribute):
            continue
        converted = _convert_color(runtime, channels)
        try:
            setattr(material, attribute, converted)
        except Exception as exc:  # noqa: BLE001 - readback is the fail-closed boundary.
            warnings.append("Could not set {}: {}".format(attribute, exc))
            continue
        readback = _read_channels(runtime, material, attribute)
        if not _channels_match(readback, channels):
            warnings.append("{} read back {!r} after writing {}".format(attribute, readback, channels))
            continue
        return {
            "applied": True,
            "parameter": parameter,
            "renderer": family,
            "attribute": attribute,
            "requested": channels,
            "warnings": warnings,
        }
    return {
        "applied": False,
        "parameter": parameter,
        "renderer": family,
        "attribute": None,
        "candidates": list(attributes),
        "warnings": warnings,
        "error": "No native {} attribute accepted this color on this material".format(parameter),
    }


def set_material_map(
    material: Any,
    slot: str,
    bitmap: Any,
    *,
    runtime: Any = None,
    renderer: Optional[str] = None,
    wrap_normal: bool = True,
) -> Dict[str, Any]:
    """Wire one bitmap into the renderer-native slot for a canonical map type."""
    family = detect_renderer_family(runtime, material=material, requested=renderer)
    attributes = map_slot_candidates(family, slot)
    if not attributes:
        return {
            "applied": False,
            "slot": slot,
            "renderer": family,
            "attribute": None,
            "error": "Renderer {} does not support the {} map slot".format(family, slot),
        }

    warnings: List[str] = []
    payload = bitmap
    if slot in NORMAL_WRAPPED_SLOTS and wrap_normal and runtime is not None:
        payload, wrap_warnings = wrap_normal_map(runtime, bitmap)
        warnings.extend(wrap_warnings)

    for attribute in attributes:
        if not _host_has_property(runtime, material, attribute):
            continue
        try:
            setattr(material, attribute, payload)
        except Exception as exc:  # noqa: BLE001 - readback is the fail-closed boundary.
            warnings.append("Could not set {}: {}".format(attribute, exc))
            continue
        readback = _read_attr(runtime, material, attribute)
        if not _is_same_map(readback, payload):
            warnings.append("{} did not retain the assigned {} map".format(attribute, slot))
            continue
        return {
            "applied": True,
            "slot": slot,
            "renderer": family,
            "attribute": attribute,
            "map_type": type(payload).__name__,
            "path": str(getattr(payload, "filename", getattr(payload, "path", "")) or ""),
            "warnings": warnings,
        }
    return {
        "applied": False,
        "slot": slot,
        "renderer": family,
        "attribute": None,
        "candidates": list(attributes),
        "warnings": warnings,
        "error": "No native {} slot accepted this map on this material".format(slot),
    }


def match_texture_set(paths: Sequence[Path]) -> Dict[str, Any]:
    """Group texture files into canonical map slots by filename tokens."""
    matched: Dict[str, Dict[str, Any]] = {}
    unmatched: List[str] = []
    ambiguous: List[Dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: str(item)):
        slot, confidence = _classify_texture(path)
        if slot is None:
            unmatched.append(str(path))
            continue
        existing = matched.get(slot)
        if existing is not None:
            ambiguous.append(
                {
                    "slot": slot,
                    "kept": existing["path"],
                    "skipped": str(path),
                    "reason": "multiple files matched this slot",
                }
            )
            continue
        matched[slot] = {"slot": slot, "path": str(path), "file_name": path.name, "match": confidence}
    return {
        "matched": [matched[slot] for slot in sorted(matched)],
        "unmatched": unmatched,
        "ambiguous": ambiguous,
    }


def collect_texture_files(directory: Path, *, recursive: bool = False) -> List[Path]:
    """Return image files inside one texture directory."""
    if not directory.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    files = []
    for path in sorted(directory.glob(pattern)):
        if not path.is_file():
            continue
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            files.append(path)
    return files


def build_material_from_texture_set(
    runtime: Any,
    *,
    paths: Sequence[Path],
    name: str,
    renderer: Optional[str] = None,
    slots: Optional[Sequence[str]] = None,
    node_names: Optional[Sequence[str]] = None,
    assign: bool = False,
    allow_missing: bool = False,
) -> Dict[str, Any]:
    """Create a renderer material and wire every recognised texture into it."""
    family = detect_renderer_family(runtime, requested=renderer)
    files = [Path(str(path)).expanduser() for path in paths]
    if not allow_missing:
        missing = [str(path) for path in files if not path.is_file()]
        if missing:
            return renderer_material_error(
                "Texture files do not exist",
                missing=missing,
                renderer=family,
                material_name=name,
            )
    material = create_renderer_material(runtime, family, name)
    if material is None:
        return renderer_material_error(
            "No supported material constructor was available",
            renderer=family,
            material_name=name,
            constructors=list(MATERIAL_CONSTRUCTORS.get(family, ())),
            failure_reason="material_constructor_unavailable",
        )
    family = detect_renderer_family(runtime, material=material, requested=renderer)

    grouped = match_texture_set(files)
    supported = set(MAP_SLOT_PLANS.get(family, {}))
    requested_slots = set(slots) if slots else supported
    wired: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for entry in grouped["ambiguous"]:
        warnings.append(
            "Skipped {} for slot {}: {}".format(entry["skipped"], entry["slot"], entry["reason"]),
        )

    used_attributes: Dict[str, str] = {}
    for entry in grouped["matched"]:
        slot = entry["slot"]
        path = Path(entry["path"])
        if slot not in requested_slots:
            warnings.append("Slot {} was not requested; skipped".format(slot))
            continue
        if slot not in supported:
            warnings.append("No native {} slot exists on {} materials".format(slot, family))
            continue
        bitmap = _create_bitmap(runtime, str(path))
        result = set_material_map(material, slot, bitmap, runtime=runtime, renderer=family)
        result["path"] = str(path)
        warnings.extend(result.get("warnings", []))
        if result["applied"]:
            attribute = result.get("attribute")
            shared_with = used_attributes.get(attribute)
            if shared_with is not None:
                warnings.append(
                    "Slot {} shares native attribute {} with {}".format(slot, attribute, shared_with)
                )
            else:
                used_attributes[attribute] = slot
            wired.append(result)
        else:
            errors.append(result)

    data = {
        "renderer": family,
        "material": {
            "name": str(getattr(material, "name", name)),
            "type": material_class_names(material, runtime=runtime)[0],
        },
        "texture_count": len(files),
        "wired": wired,
        "wired_count": len(wired),
        "errors": errors,
        "warnings": warnings,
        "unmatched": grouped["unmatched"],
        "ambiguous": grouped["ambiguous"],
        "supported_slots": sorted(supported),
        "changed_material_count": 0 if errors else 1,
    }

    if errors:
        rollback = rollback_material(runtime, material)
        data["rollback"] = rollback
        data["changed_material_count"] = 0
        return renderer_material_error(
            "Could not wire every texture onto the new material",
            **data,
        )

    if assign:
        assignment = _assign_to_nodes(runtime, material, node_names)
        data["assigned_nodes"] = assignment["assigned"]
        data["assigned_node_count"] = len(assignment["assigned"])
        data["assignment_errors"] = assignment["errors"]
        warnings.extend(assignment["warnings"])
        if assignment["errors"]:
            restore = restore_node_materials(runtime, assignment["snapshots"])
            rollback = rollback_material(runtime, material)
            data["restore"] = restore
            data["rollback"] = rollback
            data["changed_material_count"] = 0
            return renderer_material_error("Could not assign the new material to every target", **data)

    return renderer_material_success("Created material from texture set", **data)


def _assign_to_nodes(
    runtime: Any,
    material: Any,
    node_names: Optional[Sequence[str]],
) -> Dict[str, Any]:
    targets: List[Any] = []
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if node_names:
        lookup = getattr(runtime, "getNodeByName", None)
        objects = getattr(runtime, "objects", None) or []
        for node_name in node_names:
            found = None
            if callable(lookup):
                try:
                    found = lookup(node_name)
                except Exception:  # noqa: BLE001 - fall back to a scene scan.
                    found = None
            if found is None:
                for node in objects:
                    if str(getattr(node, "name", "")) == str(node_name):
                        found = node
                        break
            if found is None:
                errors.append({"node_name": str(node_name), "error": "Node not found"})
                continue
            targets.append(found)
    else:
        try:
            targets = list(getattr(runtime, "selection", None) or [])
        except Exception:  # noqa: BLE001 - empty selection is an explicit error.
            targets = []
        if not targets:
            warnings.append("No nodes were selected; the material was created without assignment")

    assigned: List[Dict[str, Any]] = []
    snapshots: List[Tuple[Any, Any]] = []
    for node in targets:
        snapshots.append((node, getattr(node, "material", None)))
        try:
            node.material = material
        except Exception as exc:  # noqa: BLE001 - readback is the fail-closed boundary.
            errors.append({"node": node_identity(node), "error": str(exc)})
            continue
        if getattr(node, "material", None) is not material:
            errors.append({"node": node_identity(node), "error": "Material readback did not match"})
            continue
        assigned.append(node_identity(node))
    return {"assigned": assigned, "errors": errors, "warnings": warnings, "snapshots": snapshots}


def restore_node_materials(
    runtime: Any,
    snapshots: Sequence[Tuple[Any, Any]],
) -> Dict[str, Any]:
    """Restore each node's previous material after a failed assignment.

    Deleting a newly created material without this leaves every node that was
    already switched pointing at a material that no longer exists, which is a
    worse scene state than leaving the failure alone.
    """
    restored: List[str] = []
    failed: List[Dict[str, Any]] = []
    for node, previous in snapshots:
        name = str(getattr(node, "name", ""))
        try:
            node.material = previous
        except Exception as exc:  # noqa: BLE001 - readback is the fail-closed boundary.
            failed.append({"node": name, "error": str(exc)})
            continue
        if getattr(node, "material", None) is not previous:
            failed.append({"node": name, "error": "Material readback did not match the snapshot"})
            continue
        restored.append(name)
    return {"restored": restored, "failed": failed}


def rollback_material(runtime: Any, material: Any) -> Dict[str, Any]:
    """Delete a material this call created and verify it is gone."""
    delete = getattr(runtime, "delete", None)
    error = None
    if callable(delete):
        try:
            delete(material)
        except Exception as exc:  # noqa: BLE001 - verification remains authoritative.
            error = str(exc)
    else:
        error = "The host exposes no delete operation"
    for attribute in ("sceneMaterials", "materials"):
        values = getattr(runtime, attribute, None)
        if values is None:
            continue
        try:
            if material in values:
                values.remove(material)
        except Exception as exc:  # noqa: BLE001 - verification remains authoritative.
            error = error or str(exc)
    still_present = False
    for attribute in ("sceneMaterials", "materials"):
        values = getattr(runtime, attribute, None)
        if values is None:
            continue
        try:
            if material in values:
                still_present = True
        except Exception:  # noqa: BLE001 - unverifiable removal must fail closed.
            still_present = True
    return {
        "rolled_back": not still_present,
        "error": error,
        "material": str(getattr(material, "name", "")),
    }


def _create_bitmap(runtime: Any, texture_path: str) -> Any:
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


def _classify_texture(path: Path) -> Tuple[Optional[str], str]:
    stem = path.stem.lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", stem) if token]
    for slot in sorted(TEXTURE_TOKENS, key=lambda key: -max(len(token) for token in TEXTURE_TOKENS[key])):
        for token in TEXTURE_TOKENS[slot]:
            if token in tokens:
                return slot, "token"
            if len(token) >= 5 and token in stem:
                return slot, "substring"
    return None, "none"


def _host_has_property(runtime: Any, material: Any, attribute: str) -> bool:
    """Probe host property support; unknown hosts and plain test doubles pass."""
    checker = getattr(runtime, "isProperty", None)
    if not callable(checker):
        return True
    name_factory = getattr(runtime, "Name", None)
    name = name_factory(attribute) if callable(name_factory) else attribute
    try:
        return bool(checker(material, name))
    except Exception:  # noqa: BLE001 - property probing must fail closed.
        return False


def _read_attr(runtime: Any, material: Any, attribute: str) -> Any:
    try:
        return getattr(material, attribute)
    except Exception:  # noqa: BLE001 - pymxs wrappers reject unknown properties.
        return None


def _read_number(runtime: Any, material: Any, attribute: str) -> Optional[float]:
    value = _read_attr(runtime, material, attribute)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_channels(runtime: Any, material: Any, attribute: str) -> Optional[List[float]]:
    value = _read_attr(runtime, material, attribute)
    if value is None:
        return None
    for channels in (_object_channels(value), _sequence_channels(value)):
        if channels is not None:
            return channels
    return None


def _object_channels(value: Any) -> Optional[List[float]]:
    for names in (("r", "g", "b"), ("red", "green", "blue"), ("x", "y", "z")):
        try:
            return [float(getattr(value, name)) for name in names]
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def _sequence_channels(value: Any) -> Optional[List[float]]:
    if isinstance(value, (str, bytes)):
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return [float(value[0]), float(value[1]), float(value[2])]
        except (TypeError, ValueError):
            return None
    return None


def _channels_match(readback: Optional[List[float]], expected: List[float]) -> bool:
    if readback is None:
        return False
    if len(readback) < 3:
        return False
    return all(
        math.isclose(readback[index], expected[index], rel_tol=1e-3, abs_tol=1e-3) for index in range(3)
    )


def _coerce_scalar(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_channels(value: Sequence[float]) -> List[float]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return [float(value[0]), float(value[1]), float(value[2])]
    try:
        scalar = float(value)
    except (TypeError, ValueError):
        return [0.0, 0.0, 0.0]
    return [scalar, scalar, scalar]


def _convert_color(runtime: Any, channels: List[float]) -> Any:
    for factory_name in ("color", "Color"):
        factory = getattr(runtime, factory_name, None)
        if not callable(factory):
            continue
        try:
            return factory(channels[0], channels[1], channels[2])
        except Exception:  # noqa: BLE001 - the readback check catches bad values.
            continue
    return list(channels)


def _apply_transform(value: float, transform: Optional[str]) -> float:
    if transform == "inverse":
        return 1.0 - float(value)
    return float(value)


def _apply_prerequisites(
    runtime: Any,
    material: Any,
    prerequisites: Sequence[Tuple[str, Any]],
) -> Optional[str]:
    """Enable the native switch a property needs; report when it cannot be set."""
    for attribute, value in prerequisites:
        if not _host_has_property(runtime, material, attribute):
            continue
        try:
            setattr(material, attribute, value)
        except Exception as exc:  # noqa: BLE001 - prerequisite failure invalidates the write.
            return "Could not set prerequisite {}: {}".format(attribute, exc)
        readback = _read_attr(runtime, material, attribute)
        if readback != value:
            return "Prerequisite {} read back {!r} after writing {}".format(attribute, readback, value)
    return None


def _is_same_map(readback: Any, payload: Any) -> bool:
    if readback is None:
        return False
    if readback is payload:
        return True
    return _map_path(readback) == _map_path(payload) and bool(_map_path(payload))


def _map_path(value: Any) -> str:
    direct = getattr(value, "filename", "") or getattr(value, "path", "")
    if direct:
        return str(direct)
    for attribute in ("normal_map", "normalMap", "bitmap", "texmap"):
        nested = getattr(value, attribute, None)
        if nested is not None and nested is not value:
            return _map_path(nested)
    return ""
