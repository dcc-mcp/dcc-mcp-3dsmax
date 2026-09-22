"""Renderer-aware light providers for 3ds Max.

The bundled ``create_light`` tool covers the host-native light classes only.
Renderers that ship their own light objects (Arnold / MAXtoA, Corona) and the
photometric area emitters each need a different factory class and a different
set of native property names, so this module declares one provider per family:

* the factory classes it can build, grouped by light kind (area / dome / sun)
* the native property candidates for every control
* the enum tables the host indexes light shapes and photometric units with

Every write is read back: a control the host rejects or silently ignores is a
failure with the attribute candidates in the payload, never a success. Batch
calls validate every spec before the first node exists and roll every light
they created back when a later light fails.

The enum tables are provider-declared defaults. A host that indexes its shape
dropdown differently is reported by ``lighting_capabilities(probe=True)``, and
callers can bypass the table entirely with ``shape_value`` / ``units_value``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._camera_light_utils import (
    bitmap_path,
    cam_error,
    cam_success,
    color_channels,
    construct_runtime_object,
    host_has_property,
    light_summary,
    native_bitmap,
    owned_light,
    point3_value,
    read_first_attr,
    rollback_owned_nodes,
    rollback_summary,
    runtime_color,
    write_verified_attr,
)
from dcc_mcp_3dsmax._render_utils import current_renderer

MAX_LIGHTS_PER_CALL = 32

PROVIDER_KEYS = ("arnold", "corona", "photometric", "vray", "standard")

# Generic spec fields accepted for arnold / corona / photometric lights.
GENERIC_FIELDS: Tuple[str, ...] = (
    "name",
    "kind",
    "shape",
    "shape_value",
    "units",
    "units_value",
    "position",
    "target_position",
    "targeted",
    "intensity",
    "exposure",
    "color",
    "color_temperature",
    "cast_shadows",
    "size_u",
    "size_v",
    "radius",
    "samples",
    "spread",
    "normalize_color",
    "texture_path",
    "color_space",
)

# Fields the V-Ray provider understands (mapped onto ``_vray_utils`` specs).
VRAY_FIELDS: Tuple[str, ...] = (
    "name",
    "shape",
    "shape_value",
    "units",
    "units_value",
    "position",
    "target_position",
    "targeted",
    "intensity",
    "color",
    "cast_shadows",
    "normalize_color",
    "size_u",
    "size_v",
    "texture_path",
    "color_space",
    "map_type",
    "gamma",
    "horizontal_rotation",
)

VRAY_UNSUPPORTED_FIELDS: Tuple[str, ...] = (
    "kind",
    "exposure",
    "color_temperature",
    "radius",
    "samples",
    "spread",
)

VRAY_ONLY_FIELDS: Tuple[str, ...] = ("map_type", "gamma", "horizontal_rotation")

NUMERIC_FIELDS = ("intensity", "exposure", "size_u", "size_v", "radius", "samples", "spread", "color_temperature")
BOOL_FIELDS = ("cast_shadows", "normalize_color", "targeted")
POSITION_ATTRS = ("position", "pos")
TARGET_ATTRS = ("target", "target_object", "targetObject")

# Write order: enum switches first (they can rename other properties), then
# photometric values, then geometry, then textures.
WRITE_ORDER: Tuple[str, ...] = (
    "shape",
    "shape_value",
    "units",
    "units_value",
    "intensity",
    "exposure",
    "color",
    "color_temperature",
    "cast_shadows",
    "normalize_color",
    "targeted",
    "size_u",
    "size_v",
    "radius",
    "samples",
    "spread",
    "texture_path",
    "color_space",
)


@dataclass(frozen=True)
class LightProvider:
    """One renderer family's light factories, property names, and enums."""

    key: str
    display_name: str
    factories: Mapping[str, Tuple[str, ...]]
    default_kind: str
    shapes: Mapping[str, int]
    shape_names: Tuple[str, ...]
    units: Mapping[str, int]
    unit_names: Tuple[str, ...]
    controls: Mapping[str, Tuple[str, ...]]
    fields: Tuple[str, ...]
    notes: str = ""

    def kind_factories(self, kind: Optional[str]) -> Tuple[str, ...]:
        key = kind or self.default_kind
        return tuple(self.factories.get(key, ()))

    def kinds(self) -> Tuple[str, ...]:
        return tuple(self.factories)


def _arnold() -> LightProvider:
    return LightProvider(
        key="arnold",
        display_name="Arnold (MAXtoA)",
        factories={
            "area": ("aiAreaLight", "Arnold_Light", "ai_area_light"),
            "dome": ("aiSkyDomeLight", "Arnold_Sky", "ai_sky_dome_light"),
            "photometric": ("aiPhotometricLight",),
        },
        default_kind="area",
        shapes={"quad": 0, "rectangle": 0, "plane": 0, "disk": 1, "disc": 1, "cylinder": 2, "sphere": 3},
        shape_names=("quad", "disk", "cylinder", "sphere"),
        units={"renderer": 0, "default": 0, "w": 1, "lm": 2, "cd": 3, "lx": 4, "radiance": 5},
        unit_names=("renderer", "w", "lm", "cd", "lx", "radiance"),
        controls={
            "shape": ("type", "shape", "lightShape", "areaType", "aiAreaType"),
            "units": ("units", "intensityUnits", "unit", "aiUnits"),
            "intensity": ("intensity", "aiIntensity"),
            "exposure": ("exposure", "aiExposure"),
            "color": ("color", "aiColor"),
            "color_temperature": ("color_temperature", "colorTemperature", "kelvin", "temperature"),
            "temperature_enable": ("use_color_temperature", "useColorTemperature", "colorMode", "aiColorMode"),
            "cast_shadows": ("castShadows", "cast_shadows", "affectShadows"),
            "normalize_color": ("normalize", "normalizeColor", "normalize_color"),
            "targeted": ("targeted",),
            "size_u": ("U_size", "sizeU", "size0", "width", "aiWidth"),
            "size_v": ("V_size", "sizeV", "size1", "length", "aiLength"),
            "radius": ("radius", "sizeRadius", "aiRadius"),
            "samples": ("samples", "shadowSamples", "lightSamples", "aiSamples"),
            "spread": ("spread", "aiSpread", "coneAngle"),
            "texture": ("color_texmap", "texmap", "texture", "shader", "dome_tex"),
            "color_space": ("color_space", "colorSpace", "texmap_colorSpace", "texmap_color_space"),
        },
        fields=GENERIC_FIELDS,
        notes="Shape and unit indices are MAXtoA dropdown indices; verify a build with lighting_capabilities(probe=True).",
    )


def _corona() -> LightProvider:
    return LightProvider(
        key="corona",
        display_name="Corona",
        factories={
            "area": ("CoronaLight",),
            "sun": ("CoronaSun",),
            "sky": ("CoronaSky",),
        },
        default_kind="area",
        shapes={"rectangle": 0, "plane": 0, "disc": 1, "disk": 1, "sphere": 2, "cylinder": 3, "mesh": 4},
        shape_names=("rectangle", "disc", "sphere", "cylinder", "mesh"),
        units={"renderer": 0, "default": 0, "w": 1, "lm": 2, "cd": 3, "lx": 4},
        unit_names=("renderer", "w", "lm", "cd", "lx"),
        controls={
            "shape": ("shapeType", "shape", "type", "lightShape"),
            "units": ("intensityUnits", "units", "unit"),
            "intensity": ("intensity", "multiplier"),
            "exposure": ("exposure",),
            "color": ("color", "lightColor"),
            "color_temperature": ("temperature", "colorTemperature", "color_temperature", "kelvin"),
            "temperature_enable": ("colorMode", "useColorTemperature", "use_color_temperature", "blackbody"),
            "cast_shadows": ("castShadows", "cast_shadows", "shadowsOn"),
            "normalize_color": ("normalize", "normalizeColor", "normalize_color"),
            "targeted": ("targeted",),
            "size_u": ("width", "sizeU", "U_size"),
            "size_v": ("length", "sizeV", "V_size"),
            "radius": ("radius", "sizeRadius"),
            "samples": ("samples", "lightSamples", "shadowSamples"),
            "spread": ("spread", "coneAngle", "directionality"),
            "texture": ("texmap", "texture", "color_texmap", "shader"),
            "color_space": ("color_space", "colorSpace"),
        },
        fields=GENERIC_FIELDS,
        notes="CoronaSun / CoronaSky are separate factory classes; select them with kind=sun or kind=sky.",
    )


def _photometric() -> LightProvider:
    return LightProvider(
        key="photometric",
        display_name="3ds Max photometric and area lights",
        factories={
            "area": ("mrAreaOmni", "mr_Area_Omni", "mrAreaSpot", "mr_Area_Spot"),
            "photometric": ("FreeLight", "TargetLight", "PhotometricLight"),
            "sun": ("Sunlight", "IES_Sun", "mr_Sun"),
        },
        default_kind="area",
        shapes={"rectangle": 0, "disc": 1, "disk": 1, "sphere": 2, "cylinder": 3},
        shape_names=("rectangle", "disc", "sphere", "cylinder"),
        units={"renderer": 0, "default": 0, "cd": 1, "candela": 1, "lm": 2, "lumen": 2, "lx": 3, "lux": 3},
        unit_names=("renderer", "cd", "lm", "lx"),
        controls={
            "shape": ("type", "mr_Type", "mr_type", "shape", "lightShape"),
            "units": ("intensityUnits", "luminanceUnits", "units", "unit"),
            "intensity": ("intensity", "resultingIntensity"),
            "exposure": ("exposure",),
            "color": ("color", "filterColor"),
            "color_temperature": ("kelvin", "color_temperature", "colorTemperature", "temperature"),
            "temperature_enable": ("colorType", "color_type", "useKelvin", "use_color_temperature", "colorMode"),
            "cast_shadows": ("shadowsOn", "castShadows", "cast_shadows"),
            "normalize_color": ("normalize", "normalizeColor", "normalize_color"),
            "targeted": ("targeted",),
            "size_u": ("mr_Width", "width", "sizeU", "U_size"),
            "size_v": ("mr_Length", "length", "sizeV", "V_size"),
            "radius": ("mr_Radius", "radius", "sizeRadius"),
            "samples": ("mr_Samples", "samples", "shadowSamples"),
            "spread": ("spread", "coneAngle", "hotspot", "falloff"),
            "texture": ("texmap", "texture", "projectorMap"),
            "color_space": ("color_space", "colorSpace"),
        },
        fields=GENERIC_FIELDS,
        notes="mrAreaOmni / mrAreaSpot carry the rectangular, disc, sphere, and cylinder emitters.",
    )


def _standard() -> LightProvider:
    return LightProvider(
        key="standard",
        display_name="3ds Max host-native lights",
        factories={
            "omni": ("OmniLight", "Omnilight"),
            "spot": ("FreeSpot", "TargetSpot"),
            "directional": ("DirectionalLight", "TargetDirectionalLight"),
            "skylight": ("Skylight", "SkyLight"),
        },
        default_kind="omni",
        shapes={},
        shape_names=(),
        units={},
        unit_names=(),
        controls={
            "intensity": ("multiplier", "intensity"),
            "exposure": ("exposure",),
            "color": ("color",),
            "cast_shadows": ("castShadows", "cast_shadows", "shadows"),
            "normalize_color": ("normalize", "normalizeColor", "normalize_color"),
            "targeted": ("targeted",),
            "size_u": ("width", "U_size", "sizeU"),
            "size_v": ("length", "V_size", "sizeV"),
            "radius": ("radius",),
            "samples": ("samples", "shadowSamples"),
            "spread": ("spread", "hotspot", "falloff"),
            "texture": ("texmap", "projectorMap"),
            "color_space": ("color_space", "colorSpace"),
        },
        fields=GENERIC_FIELDS,
        notes="Host-native lights expose no light shape or photometric unit enum.",
    )


def _vray() -> LightProvider:
    from dcc_mcp_3dsmax._vray_utils import (
        VRAY_BITMAP_MAP_TYPES,
        VRAY_LIGHT_SHAPE_NAMES,
        VRAY_LIGHT_SHAPES,
        VRAY_LIGHT_UNIT_NAMES,
        VRAY_LIGHT_UNITS,
    )

    return LightProvider(
        key="vray",
        display_name="V-Ray",
        factories={"area": ("VRayLight", "VRay_Light"), "sun": ("VRaySun",)},
        default_kind="area",
        shapes=dict(VRAY_LIGHT_SHAPES),
        shape_names=tuple(VRAY_LIGHT_SHAPE_NAMES),
        units=dict(VRAY_LIGHT_UNITS),
        unit_names=tuple(VRAY_LIGHT_UNIT_NAMES),
        controls={
            "shape": ("type", "shape", "lightShape"),
            "units": ("units",),
            "intensity": ("multiplier", "intensity"),
            "color": ("color",),
            "cast_shadows": ("castShadows", "cast_shadows"),
            "normalize_color": ("normalizeColor", "normalize_color"),
            "targeted": ("targeted",),
            "size_u": ("U_size", "u_size", "sizeU", "size0"),
            "size_v": ("V_size", "v_size", "sizeV", "size1"),
            "texture": ("texmap", "dome_tex", "domeTex", "texture"),
            "color_space": ("color_space", "colorSpace", "colorspace"),
        },
        fields=VRAY_FIELDS,
        notes=(
            "V-Ray lights are created through create_vray_light; this provider entry exists so "
            "capability discovery and provider routing stay complete. Supported HDRI map types: "
            + ", ".join(sorted(VRAY_BITMAP_MAP_TYPES))
        ),
    )


def _build_providers() -> Dict[str, LightProvider]:
    return {
        "arnold": _arnold(),
        "corona": _corona(),
        "photometric": _photometric(),
        "vray": _vray(),
        "standard": _standard(),
    }


PROVIDERS: Dict[str, LightProvider] = _build_providers()

# Which provider an active renderer family routes to when the caller says auto.
RENDERER_ROUTING: Dict[str, str] = {
    "arnold": "arnold",
    "corona": "corona",
    "vray": "vray",
    "scanline": "photometric",
    "other": "photometric",
    "unknown": "photometric",
}

_RENDERER_FAMILY_TOKENS: Tuple[Tuple[str, str], ...] = (
    ("arnold", "arnold"),
    ("max_to_a", "arnold"),
    ("corona", "corona"),
    ("vray", "vray"),
    ("v-ray", "vray"),
    ("redshift", "redshift"),
    ("scanline", "scanline"),
    ("default", "scanline"),
)

# Native class name fragments used to detect the provider of an existing node.
_NODE_CLASS_PROVIDERS: Tuple[Tuple[str, str], ...] = (
    ("aiarealight", "arnold"),
    ("aiskydomelight", "arnold"),
    ("aiphotometriclight", "arnold"),
    ("arnold", "arnold"),
    ("coronalight", "corona"),
    ("coronasun", "corona"),
    ("coronasky", "corona"),
    ("vraylight", "vray"),
    ("vraysun", "vray"),
    ("mrareaomni", "photometric"),
    ("mrareaspot", "photometric"),
    ("freelight", "photometric"),
    ("targetlight", "photometric"),
    ("photometric", "photometric"),
    ("sunlight", "photometric"),
    ("mr_sun", "photometric"),
)


# ---------------------------------------------------------------------------
# Capability discovery
# ---------------------------------------------------------------------------


def detect_renderer_family(runtime: Any, requested: Optional[str] = None) -> str:
    """Return the renderer family used for provider routing."""
    if requested:
        normalized = str(requested).strip().lower()
        for token, family in _RENDERER_FAMILY_TOKENS:
            if token in normalized:
                return family
        return "other"
    renderer = current_renderer(runtime)
    if renderer is None:
        return "unknown"
    class_of = getattr(runtime, "classOf", None)
    try:
        name = str(class_of(renderer)) if callable(class_of) else type(renderer).__name__
    except Exception:  # noqa: BLE001 - host wrappers can reject class introspection.
        name = type(renderer).__name__
    return detect_renderer_family(runtime, requested=name)


def detect_provider(runtime: Any, node: Any) -> str:
    """Return the provider key that owns an existing light node."""
    summary = light_summary(node, runtime=runtime)
    names = [str(summary.get("native_class") or ""), _node_kind_name(node)]
    text = " ".join(names).lower().replace(" ", "").replace("_", "")
    normalized = text.replace("-", "")
    for token, provider in _NODE_CLASS_PROVIDERS:
        if token in normalized:
            return provider
    return "standard"


def _node_kind_name(node: Any) -> str:
    return str(getattr(node, "className", type(node).__name__) or "")


def lighting_capabilities(
    runtime: Any,
    *,
    renderer: Optional[str] = None,
    providers: Optional[Sequence[str]] = None,
    probe: bool = False,
) -> Dict[str, Any]:
    """Report the light providers the host can build and how calls are routed."""
    requested = [str(item).strip().lower() for item in providers] if providers else []
    unknown = [name for name in requested if name not in PROVIDERS]
    if unknown:
        return cam_error(
            "Unsupported light providers requested",
            requested=sorted(requested),
            unsupported=sorted(unknown),
            supported=sorted(PROVIDERS),
        )

    family = detect_renderer_family(runtime, requested=renderer)
    route = RENDERER_ROUTING.get(family, "photometric")
    keys = requested or PROVIDER_KEYS
    probe_errors: List[Dict[str, Any]] = []
    report: List[Dict[str, Any]] = []
    for key in keys:
        provider = PROVIDERS[key]
        entry = _provider_capabilities(runtime, provider)
        if probe:
            try:
                entry["probe"] = probe_provider(runtime, provider.key)
            except Exception as exc:  # noqa: BLE001 - a probe failure stays informational.
                probe_errors.append(
                    {
                        "provider": provider.key,
                        "exception_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
        report.append(entry)

    renderer_object = current_renderer(runtime)
    class_of = getattr(runtime, "classOf", None)
    try:
        renderer_class = str(class_of(renderer_object)) if callable(class_of) else type(renderer_object).__name__
    except Exception:  # noqa: BLE001 - host wrappers can reject class introspection.
        renderer_class = type(renderer_object).__name__

    return cam_success(
        "Reported lighting capabilities",
        renderer={
            "family": family,
            "class": renderer_class,
            "detected": renderer_object is not None,
        },
        requested_renderer=str(renderer) if renderer else None,
        default_provider=route,
        routing=dict(RENDERER_ROUTING),
        max_lights_per_call=MAX_LIGHTS_PER_CALL,
        providers=report,
        probe_performed=bool(probe),
        probe_errors=probe_errors,
    )


def _provider_capabilities(runtime: Any, provider: LightProvider) -> Dict[str, Any]:
    """Describe one provider without creating anything."""
    kinds: Dict[str, Any] = {}
    available = False
    for kind, factories in provider.factories.items():
        present = [name for name in factories if callable(getattr(runtime, name, None))]
        kinds[kind] = {
            "available": bool(present),
            "factory_candidates": list(factories),
            "available_factories": present,
        }
        available = available or bool(present)
    return {
        "key": provider.key,
        "display_name": provider.display_name,
        "available": available,
        "kinds": kinds,
        "default_kind": provider.default_kind,
        "shapes": dict(provider.shapes),
        "shape_names": list(provider.shape_names),
        "units": dict(provider.units),
        "unit_names": list(provider.unit_names),
        "controls": {name: list(attrs) for name, attrs in provider.controls.items()},
        "fields": list(provider.fields),
        "notes": provider.notes,
    }


def probe_provider(runtime: Any, provider_key: str) -> Dict[str, Any]:
    """Build one throwaway light per kind and record what the host accepts.

    The probe light is deleted and the deletion is verified, so the scene is
    unchanged. Everything the probe learns is reported as data: a probe is
    allowed to fail, it is never allowed to claim support it did not observe.
    """
    provider = PROVIDERS.get(provider_key)
    if provider is None:
        return {"status": "unsupported_provider", "provider": provider_key, "supported": sorted(PROVIDERS)}
    if provider_key == "vray":
        return {
            "status": "skipped",
            "provider": provider_key,
            "reason": "V-Ray lights are created through create_vray_light, which verifies every control itself.",
        }

    kinds: Dict[str, Any] = {}
    for kind in provider.kinds():
        factories = provider.kind_factories(kind)
        if not [name for name in factories if callable(getattr(runtime, name, None))]:
            kinds[kind] = {"status": "unavailable", "factory_candidates": list(factories)}
            continue
        node, warnings = construct_runtime_object(runtime, factories)
        if node is None:
            kinds[kind] = {
                "status": "factory_unavailable",
                "factory_candidates": list(factories),
                "warnings": warnings,
            }
            continue
        entry: Dict[str, Any] = {}
        try:
            entry = _probe_node(runtime, provider, node)
        except Exception as exc:  # noqa: BLE001 - a probe failure stays informational.
            entry = {"status": "error", "exception_type": type(exc).__name__, "error": str(exc)}
        finally:
            incomplete = rollback_owned_nodes(runtime, [_safe_owned(runtime, node)])
            entry["rolled_back"] = not incomplete
            if incomplete:
                entry["rollback_incomplete_handles"] = [
                    handle for _node, handle, _summary in incomplete if handle is not None
                ]
            entry["warnings"] = warnings
            kinds[kind] = entry
    return {"status": "ok", "provider": provider_key, "kinds": kinds}


def _probe_node(runtime: Any, provider: LightProvider, node: Any) -> Dict[str, Any]:
    controls: Dict[str, Any] = {}
    for name, attributes in provider.controls.items():
        if name == "temperature_enable":
            continue
        exposed = [attribute for attribute in attributes if host_has_property(runtime, node, attribute)]
        controls[name] = {"exposed": bool(exposed), "attributes": exposed}
    return {
        "status": "ok",
        "native_class": _native_class(runtime, node),
        "controls": controls,
        "shapes": _probe_enum(runtime, node, provider.controls.get("shape", ()), len(provider.shape_names)),
        "units": _probe_enum(runtime, node, provider.controls.get("units", ()), len(provider.unit_names)),
    }


def _probe_enum(
    runtime: Any,
    node: Any,
    attributes: Sequence[str],
    count: int,
) -> Dict[str, Any]:
    """Report which enum indices the host actually accepts."""
    if not attributes:
        return {"exposed": False, "accepted": []}
    if not any(host_has_property(runtime, node, attribute) for attribute in attributes):
        return {"exposed": False, "accepted": []}
    accepted: List[int] = []
    for index in range(max(0, count)):
        result = write_verified_attr(runtime, node, attributes, index, mode="number")
        if result.get("applied"):
            accepted.append(index)
    return {"exposed": True, "accepted": accepted, "declared_count": count}


# ---------------------------------------------------------------------------
# Batch creation
# ---------------------------------------------------------------------------


def create_renderer_lights(
    runtime: Any,
    specs: Sequence[Dict[str, Any]],
    *,
    provider: str = "auto",
) -> Dict[str, Any]:
    """Create one or more provider lights as a single verified transaction."""
    if not specs:
        return cam_error("At least one light spec is required", lights=[])
    if len(specs) > MAX_LIGHTS_PER_CALL:
        return cam_error(
            "Too many lights in one call",
            requested=len(specs),
            maximum=MAX_LIGHTS_PER_CALL,
        )

    resolved, resolve_error = _resolve_provider(runtime, specs, provider)
    if resolve_error is not None:
        return resolve_error

    if resolved == "vray":
        return _create_vray_lights(runtime, specs)

    definition = PROVIDERS[resolved]
    errors = _validate_specs(definition, specs)
    if errors:
        return cam_error(
            "Rejected light specs before creating anything",
            provider=resolved,
            errors=errors,
            failure_reason="light_spec_invalid",
            **rollback_summary([]),
        )

    created: List[Dict[str, Any]] = []
    raw_nodes: List[Any] = []
    for index, spec in enumerate(specs):
        try:
            result, _node = _create_provider_light(runtime, definition, spec, sink=raw_nodes)
        except Exception as exc:  # noqa: BLE001 - a crash must still roll back.
            result = cam_error(
                "Light creation raised an error",
                exception_type=type(exc).__name__,
                error=str(exc),
            )
        if not result.get("success"):
            errors.append({"index": index, **result})
            break
        created.append(result["data"]["light"])

    if errors:
        incomplete = rollback_owned_nodes(runtime, [_safe_owned(runtime, node) for node in raw_nodes])
        return cam_error(
            "Could not create the requested lights",
            provider=resolved,
            errors=errors,
            created_light_count=len(created),
            **rollback_summary(incomplete),
        )
    return cam_success(
        "Created lights",
        provider=resolved,
        lights=created,
        changed_node_count=len(created),
    )


def _resolve_provider(
    runtime: Any,
    specs: Sequence[Dict[str, Any]],
    provider: str,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve one provider for the whole call, or explain why that is impossible."""
    requested = str(provider or "auto").strip().lower()
    spec_providers = sorted({str(spec.get("provider")).strip().lower() for spec in specs if spec.get("provider")})
    if requested == "auto" and spec_providers:
        if len(spec_providers) > 1:
            return None, cam_error(
                "Mixed light providers in one call are not supported",
                providers=spec_providers,
                supported=sorted(PROVIDERS),
                failure_reason="mixed_providers_unsupported",
            )
        requested = spec_providers[0]
    if requested in ("auto", "", "none"):
        requested = RENDERER_ROUTING.get(detect_renderer_family(runtime), "photometric")
    if requested not in PROVIDERS:
        return None, cam_error(
            "Unsupported light provider",
            provider=requested,
            supported=sorted(PROVIDERS),
            failure_reason="unknown_provider",
        )
    if spec_providers and any(name != requested for name in spec_providers):
        return None, cam_error(
            "Light provider conflicts with the requested provider",
            provider=requested,
            spec_providers=spec_providers,
            failure_reason="provider_conflict",
        )
    return requested, None


def _create_vray_lights(runtime: Any, specs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Delegate V-Ray specs to the verified V-Ray implementation."""
    from dcc_mcp_3dsmax._vray_utils import create_vray_lights

    errors: List[Dict[str, Any]] = []
    for index, spec in enumerate(specs):
        unknown = set(spec) - set(VRAY_FIELDS) - {"provider"}
        unsupported = set(spec) & set(VRAY_UNSUPPORTED_FIELDS)
        unsupported = sorted(unknown | unsupported)
        if unsupported:
            errors.append(
                {
                    "index": index,
                    "message": "Unsupported V-Ray light fields: {}".format(", ".join(unsupported)),
                }
            )
    if errors:
        return cam_error(
            "Rejected light specs before creating anything",
            provider="vray",
            errors=errors,
            failure_reason="light_spec_invalid",
            **rollback_summary([]),
        )

    mapped: List[Dict[str, Any]] = []
    for spec in specs:
        item = {key: value for key, value in spec.items() if key != "provider"}
        if "intensity" in item:
            item["multiplier"] = item.pop("intensity")
        if "shape_value" in item:
            item.pop("shape_value")
        if "units_value" in item:
            item.pop("units_value")
        mapped.append(item)
    return create_vray_lights(runtime, mapped)


def _validate_specs(provider: LightProvider, specs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reject malformed values before any node exists."""
    errors: List[Dict[str, Any]] = []
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            errors.append({"index": index, "message": "Light spec must be an object", "spec": spec})
            continue
        unknown = sorted(set(spec) - set(provider.fields) - {"provider"})
        if unknown:
            errors.append(
                {
                    "index": index,
                    "message": "Unsupported {} light fields: {}".format(provider.key, ", ".join(unknown)),
                }
            )
            continue
        invalid = _invalid_spec_values(provider, spec)
        if invalid:
            errors.append({"index": index, "message": "Invalid light values", "fields": invalid})
    return errors


def _invalid_spec_values(provider: LightProvider, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    invalid: List[Dict[str, Any]] = []

    def reject(field_name: str, value: Any, reason: str) -> None:
        invalid.append({"field": field_name, "value": _jsonable(value), "reason": reason})

    for single, raw_field in (("shape", "shape_value"), ("units", "units_value")):
        if spec.get(single) is not None and spec.get(raw_field) is not None:
            reject(raw_field, spec[raw_field], "pass either {} or {}, not both".format(single, raw_field))
    if spec.get("color_space") is not None and not spec.get("texture_path"):
        reject("color_space", spec["color_space"], "color_space requires texture_path")

    def expect_number(field_name: str, value: Any) -> None:
        if isinstance(value, bool):
            reject(field_name, value, "expected a number")
            return
        try:
            float(value)
        except (TypeError, ValueError):
            reject(field_name, value, "expected a number")

    def expect_vector(field_name: str, value: Any) -> None:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            reject(field_name, value, "expected a list of three numbers")
            return
        if len(value) < 3:
            reject(field_name, value, "expected three numbers")
            return
        for item in value[:3]:
            try:
                float(item)
            except (TypeError, ValueError):
                reject(field_name, value, "expected numeric channels")
                return

    for field_name in ("name", "texture_path", "color_space", "kind"):
        value = spec.get(field_name)
        if value is not None and not isinstance(value, str):
            reject(field_name, value, "expected a string")
    for field_name in BOOL_FIELDS:
        value = spec.get(field_name)
        if value is not None and not isinstance(value, bool):
            reject(field_name, value, "expected a boolean")
    for field_name in NUMERIC_FIELDS + ("shape_value", "units_value"):
        value = spec.get(field_name)
        if value is not None:
            expect_number(field_name, value)
    for field_name in ("position", "target_position"):
        if spec.get(field_name) is not None:
            expect_vector(field_name, spec[field_name])
    if spec.get("color") is not None:
        expect_vector("color", spec["color"])

    kind = spec.get("kind")
    if kind is not None and kind not in provider.factories:
        reject("kind", kind, "unsupported light kind for provider {}".format(provider.key))
    shape = spec.get("shape")
    if shape is not None and str(shape).strip().lower() not in provider.shapes:
        reject("shape", shape, "unsupported light shape for provider {}".format(provider.key))
    units = spec.get("units")
    if units is not None and str(units).strip().lower() not in provider.units:
        reject("units", units, "unsupported light unit for provider {}".format(provider.key))
    return invalid


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _create_provider_light(
    runtime: Any,
    provider: LightProvider,
    spec: Dict[str, Any],
    *,
    sink: Optional[List[Any]] = None,
) -> Tuple[Dict[str, Any], Optional[Any]]:
    """Create one provider light, verify every requested control, and return it."""
    kind = spec.get("kind") or provider.default_kind
    factories = provider.kind_factories(kind)
    if not factories:
        return (
            cam_error(
                "Unsupported light kind for provider",
                provider=provider.key,
                kind=kind,
                supported=list(provider.factories),
                failure_reason="unsupported_kind",
            ),
            None,
        )
    node, factory_warnings = construct_runtime_object(runtime, factories)
    if node is None:
        return (
            cam_error(
                "No supported light factory was available",
                provider=provider.key,
                kind=kind,
                factories=list(factories),
                warnings=factory_warnings,
                failure_reason="light_factory_unavailable",
            ),
            None,
        )
    if sink is not None:
        sink.append(node)

    name = str(spec.get("name") or "").strip()
    if name:
        write_verified_attr(runtime, node, ("name",), name)

    applied, failures, warnings = apply_light_controls(
        runtime,
        node,
        spec,
        provider=provider.key,
        restore_on_failure=False,
    )
    warnings = list(factory_warnings) + list(warnings)
    summary = provider_light_summary(runtime, node, provider=provider.key)
    if failures:
        return (
            cam_error(
                "Light controls did not verify",
                provider=provider.key,
                kind=kind,
                light=summary,
                failures=failures,
                applied=applied,
                warnings=warnings,
                failure_reason="light_readback_failed",
            ),
            node,
        )
    return (
        cam_success(
            "Created light",
            light=summary,
            provider=provider.key,
            kind=kind,
            applied=applied,
            warnings=warnings,
            changed_node_count=1,
        ),
        node,
    )


# ---------------------------------------------------------------------------
# Applying controls to a new or existing light
# ---------------------------------------------------------------------------


def apply_light_controls(
    runtime: Any,
    node: Any,
    spec: Mapping[str, Any],
    *,
    provider: Optional[str] = None,
    restore_on_failure: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Write every requested control onto ``node`` and verify the readback.

    Returns ``(applied, failures, warnings)``. When ``restore_on_failure`` is
    set, an existing light is put back the way it was before the failed call and
    the restore result is reported in the failure payload, so a half-applied
    light can never be mistaken for a successful edit.
    """
    resolved = provider or detect_provider(runtime, node)
    definition = PROVIDERS.get(resolved)
    if definition is None:
        failure = {
            "field": "provider",
            "requested": resolved,
            "supported": sorted(PROVIDERS),
            "error": "No light provider is registered for this node",
        }
        return ([], [failure], [])

    failures: List[Dict[str, Any]] = []
    warnings: List[str] = []
    applied: List[Dict[str, Any]] = []
    previous: Dict[str, Tuple[str, Any]] = {}

    for field in WRITE_ORDER:
        if spec.get(field) is None:
            continue
        if field == "color_space" and spec.get("texture_path"):
            # Applied onto the bitmap itself while the texture is wired up.
            continue
        target = _field_target(runtime, definition, spec, field)
        if target is None:
            failures.append(
                {
                    "field": field,
                    "requested": _jsonable(spec[field]),
                    "error": "Provider {} declares no {} control".format(resolved, field),
                }
            )
            continue
        value, mode, attributes = target
        if not attributes:
            failures.append(
                {
                    "field": field,
                    "requested": _jsonable(spec[field]),
                    "error": "Provider {} declares no native attribute for {}".format(resolved, field),
                }
            )
            continue

        if field in ("shape", "units") and value is None:
            failures.append(
                {
                    "field": field,
                    "requested": _jsonable(spec[field]),
                    "supported": sorted(definition.shapes if field == "shape" else definition.units),
                    "error": "Unsupported {} value for provider {}".format(field, resolved),
                }
            )
            continue

        if field == "color_temperature":
            _enable_temperature(runtime, node, definition, warnings)

        if field == "texture_path":
            result = _apply_texture(runtime, node, definition, spec, warnings)
        else:
            before = _snapshot(runtime, node, attributes)
            result = write_verified_attr(runtime, node, attributes, value, mode=mode)
            if result.get("applied"):
                previous[field] = (str(result["attribute"]), before)
        warnings.extend(result.get("warnings", []))
        if result.get("applied"):
            applied.append({"field": field, "attribute": result.get("attribute"), "value": _jsonable(value)})
        else:
            failed_field = result.get("field", field)
            failures.append(
                {
                    "field": failed_field,
                    "requested": _jsonable(spec.get(failed_field, spec[field])),
                    "candidates": result.get("candidates", list(attributes)),
                    "error": "The light rejected or ignored the {} control".format(failed_field),
                }
            )

    failures.extend(_verify_spec(runtime, node, definition, spec))

    position_failure = _apply_position(runtime, node, spec, "position", node, field_name="position")
    if position_failure is not None:
        failures.append(position_failure)

    target_failure = _apply_target(runtime, node, spec)
    if target_failure is not None:
        failures.append(target_failure)

    name = spec.get("name")
    if name:
        actual_name = read_first_attr(runtime, node, ("name",))
        if str(actual_name) != str(name):
            failures.append(
                {
                    "field": "name",
                    "requested": str(name),
                    "actual": None if actual_name is None else str(actual_name),
                    "error": "Light name readback did not match",
                }
            )

    if failures and restore_on_failure and previous:
        restored, restore_failures = _restore(runtime, node, previous)
        for failure in failures:
            failure["restored_previous_values"] = restored
        if restore_failures:
            warnings.extend(restore_failures)
    return applied, failures, warnings


def _field_target(
    runtime: Any,
    provider: LightProvider,
    spec: Mapping[str, Any],
    field: str,
) -> Optional[Tuple[Any, str, Tuple[str, ...]]]:
    """Return ``(value, write mode, attribute candidates)`` for one spec field."""
    if field == "texture_path":
        return str(spec["texture_path"]), "texture", tuple(provider.controls.get("texture", ()))
    # The raw ``*_value`` fields write the same native enum as their name field.
    if field == "shape_value":
        return int(spec["shape_value"]), "number", tuple(provider.controls.get("shape", ()))
    if field == "units_value":
        return int(spec["units_value"]), "number", tuple(provider.controls.get("units", ()))
    attributes = tuple(provider.controls.get(field, ()))
    if field == "shape":
        name = spec.get("shape")
        if name is None:
            return None
        resolved = provider.shapes.get(str(name).strip().lower())
        if resolved is None:
            return None
        return resolved, "number", attributes
    if field == "units":
        name = spec.get("units")
        if name is None:
            return None
        resolved = provider.units.get(str(name).strip().lower())
        if resolved is None:
            return None
        return resolved, "number", attributes
    if field == "color":
        return runtime_color(runtime, spec["color"]), "color", attributes
    if field in NUMERIC_FIELDS:
        return float(spec[field]), "number", attributes
    if field in BOOL_FIELDS:
        return bool(spec[field]), "exact", attributes
    if field == "color_space":
        return str(spec["color_space"]), "exact", attributes
    return None


def _enable_temperature(runtime: Any, node: Any, provider: LightProvider, warnings: List[str]) -> None:
    """Switch a light into Kelvin mode when it exposes a mode switch."""
    attributes = tuple(provider.controls.get("temperature_enable", ()))
    if not attributes:
        return
    for value in (True, 1):
        result = write_verified_attr(runtime, node, attributes, value, mode="exact")
        if result.get("applied"):
            return
        warnings.extend(result.get("warnings", []))


def _apply_texture(
    runtime: Any,
    node: Any,
    provider: LightProvider,
    spec: Mapping[str, Any],
    warnings: List[str],
) -> Dict[str, Any]:
    """Create a bitmap for ``texture_path`` and wire it into the light slot."""
    path = str(spec["texture_path"])
    bitmap = native_bitmap(runtime, path)
    if bitmap_path(bitmap) != path:
        warnings.append("Bitmap did not retain the image path: {}".format(path))
    color_space = spec.get("color_space")
    if color_space:
        space_result = write_verified_attr(
            runtime,
            bitmap,
            provider.controls.get("color_space", ()),
            str(color_space),
        )
        warnings.extend(space_result.get("warnings", []))
        if not space_result.get("applied"):
            return {
                "applied": False,
                "candidates": space_result.get("candidates", []),
                "warnings": warnings,
                "field": "color_space",
            }
    return write_verified_attr(runtime, node, provider.controls.get("texture", ()), bitmap, mode="texture")


def _apply_position(
    runtime: Any,
    node: Any,
    spec: Mapping[str, Any],
    field: str,
    target: Any,
    *,
    field_name: str = "position",
) -> Optional[Dict[str, Any]]:
    value = spec.get(field)
    if value is None:
        return None
    expected = [float(item) for item in value[:3]]
    for attribute in POSITION_ATTRS:
        try:
            setattr(target, attribute, point3_value(runtime, value))
        except Exception:  # noqa: BLE001 - readback is the authoritative check.
            continue
    actual = _vector_or_none(read_first_attr(runtime, target, POSITION_ATTRS))
    if actual is None or not all(
        math.isclose(actual[index], expected[index], rel_tol=1e-6, abs_tol=1e-6) for index in range(3)
    ):
        return {
            "field": field_name,
            "requested": expected,
            "actual": actual,
            "error": "Light {} readback did not match".format(field_name),
        }
    return None


def _apply_target(runtime: Any, node: Any, spec: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    value = spec.get("target_position")
    if value is None:
        return None
    expected = [float(item) for item in value[:3]]
    target = None
    for attribute in TARGET_ATTRS:
        if not host_has_property(runtime, node, attribute):
            continue
        target = read_first_attr(runtime, node, (attribute,))
        if target is not None:
            break
    if target is None:
        return {
            "field": "target_position",
            "requested": expected,
            "error": "The light exposes no target node to aim",
        }
    return _apply_position(
        runtime,
        node,
        {"position": value},
        "position",
        target,
        field_name="target_position",
    )


def _snapshot(runtime: Any, node: Any, attributes: Sequence[str]) -> Any:
    return read_first_attr(runtime, node, tuple(attributes))


def _restore(
    runtime: Any,
    node: Any,
    previous: Mapping[str, Tuple[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Put previously written attributes back and report what did not restore."""
    restored: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for field_name, (attribute, value) in previous.items():
        if value is None:
            warnings.append("No previous {} value was captured, so it was left as written".format(field_name))
            continue
        result = write_verified_attr(runtime, node, (attribute,), value, mode="exact")
        if result.get("applied"):
            restored.append({"field": field_name, "attribute": attribute, "value": _jsonable(value)})
            continue
        fallback = write_verified_attr(runtime, node, (attribute,), value, mode="number")
        if fallback.get("applied"):
            restored.append({"field": field_name, "attribute": attribute, "value": _jsonable(value)})
            continue
        warnings.append("Could not restore {} on {}: {}".format(field_name, attribute, value))
    return restored, warnings


def _verify_spec(
    runtime: Any,
    node: Any,
    provider: LightProvider,
    spec: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Re-read every requested control and compare it with the request."""
    failures: List[Dict[str, Any]] = []
    for field in WRITE_ORDER:
        if spec.get(field) is None:
            continue
        if field in ("texture_path", "color_space"):
            continue
        expected = _expected_readback(provider, spec, field)
        if expected is None:
            continue
        actual = _field_readback(runtime, node, provider, field)
        if not _values_match(field, actual, expected):
            failures.append(
                {
                    "field": field,
                    "requested": _jsonable(expected),
                    "actual": _jsonable(actual),
                    "error": "Light {} readback did not match".format(field),
                }
            )
    return failures


def _expected_readback(provider: LightProvider, spec: Mapping[str, Any], field: str) -> Any:
    if field == "shape":
        name = str(spec.get("shape") or "").strip().lower()
        return provider.shapes.get(name)
    if field == "shape_value":
        return None if spec.get("shape") is not None else int(spec["shape_value"])
    if field == "units":
        name = str(spec.get("units") or "").strip().lower()
        return provider.units.get(name)
    if field == "units_value":
        return None if spec.get("units") is not None else int(spec["units_value"])
    if field == "color":
        return [float(channel) for channel in _clamp_color(spec["color"])]
    if field in NUMERIC_FIELDS:
        return float(spec[field])
    if field in BOOL_FIELDS:
        return bool(spec[field])
    return None


def _field_readback(runtime: Any, node: Any, provider: LightProvider, field: str) -> Any:
    # The raw ``*_value`` fields write through the same native enum property.
    if field == "shape_value":
        field = "shape"
    elif field == "units_value":
        field = "units"
    attributes = tuple(provider.controls.get(field, ()))
    if field in ("shape", "units"):
        return _read_enum(runtime, node, attributes)
    if field == "color":
        return color_channels(read_first_attr(runtime, node, attributes))
    if field in NUMERIC_FIELDS:
        return _read_number(runtime, node, attributes)
    if field in BOOL_FIELDS:
        value = read_first_attr(runtime, node, attributes)
        return None if value is None else bool(value)
    return read_first_attr(runtime, node, attributes)


def _values_match(field: str, actual: Any, expected: Any) -> bool:
    if field == "color":
        if actual is None or expected is None or len(actual) < 3 or len(expected) < 3:
            return False
        return all(
            math.isclose(float(actual[index]), float(expected[index]), rel_tol=1e-3, abs_tol=1e-3) for index in range(3)
        )
    if field in BOOL_FIELDS:
        return actual is not None and bool(actual) is bool(expected)
    if field in NUMERIC_FIELDS or field in ("shape", "units", "shape_value", "units_value"):
        if actual is None or isinstance(actual, bool):
            return False
        # Hosts quantize Kelvin and sample counts to whole units.
        tolerance = 0.5 if field in ("color_temperature", "samples") else 1e-6
        try:
            return math.isclose(float(actual), float(expected), rel_tol=1e-6, abs_tol=tolerance)
        except (TypeError, ValueError):
            return False
    return actual == expected


# ---------------------------------------------------------------------------
# Readback helpers
# ---------------------------------------------------------------------------


def provider_light_summary(runtime: Any, node: Any, *, provider: Optional[str] = None) -> Dict[str, Any]:
    """Return the common light summary plus provider-specific readbacks."""
    resolved = provider or detect_provider(runtime, node)
    summary = light_summary(node, runtime=runtime)
    definition = PROVIDERS.get(resolved)
    if definition is None:
        summary["provider"] = resolved
        return summary
    shape_value = _read_enum(runtime, node, definition.controls.get("shape", ()))
    units_value = _read_enum(runtime, node, definition.controls.get("units", ()))
    texture = read_first_attr(runtime, node, definition.controls.get("texture", ()))
    summary.update(
        {
            "provider": resolved,
            "shape": _enum_name(definition.shape_names, shape_value),
            "shape_value": shape_value,
            "units": _enum_name(definition.unit_names, units_value),
            "units_value": units_value,
            "exposure": _read_number(runtime, node, definition.controls.get("exposure", ())),
            "color_temperature": _read_number(runtime, node, definition.controls.get("color_temperature", ())),
            "size_u": _read_number(runtime, node, definition.controls.get("size_u", ())),
            "size_v": _read_number(runtime, node, definition.controls.get("size_v", ())),
            "radius": _read_number(runtime, node, definition.controls.get("radius", ())),
            "samples": _read_number(runtime, node, definition.controls.get("samples", ())),
            "spread": _read_number(runtime, node, definition.controls.get("spread", ())),
            "normalize_color": _read_bool(runtime, node, definition.controls.get("normalize_color", ())),
            "targeted": _read_bool(runtime, node, definition.controls.get("targeted", ())),
            "texture": bitmap_path(texture),
            "texture_type": type(texture).__name__ if texture is not None else None,
            "color_space": (
                None if texture is None else _read_text(runtime, texture, definition.controls.get("color_space", ()))
            ),
        }
    )
    return summary


def _read_enum(runtime: Any, node: Any, attributes: Sequence[str]) -> Optional[int]:
    value = read_first_attr(runtime, node, tuple(attributes))
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_number(runtime: Any, node: Any, attributes: Sequence[str]) -> Optional[float]:
    value = read_first_attr(runtime, node, tuple(attributes))
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_bool(runtime: Any, node: Any, attributes: Sequence[str]) -> Optional[bool]:
    value = read_first_attr(runtime, node, tuple(attributes))
    if value is None:
        return None
    return bool(value)


def _read_text(runtime: Any, node: Any, attributes: Sequence[str]) -> Optional[str]:
    value = read_first_attr(runtime, node, tuple(attributes))
    if value is None:
        return None
    return str(value)


def _enum_name(names: Sequence[str], value: Optional[int]) -> Optional[str]:
    if value is None or value < 0 or value >= len(names):
        return None
    return names[value]


def _clamp_color(value: Sequence[Any]) -> List[int]:
    channels = [int(value[index]) for index in range(3)]
    return [max(0, min(255, channel)) for channel in channels]


def _vector_or_none(value: Any) -> Optional[List[float]]:
    if value is None:
        return None
    for names in (("x", "y", "z"), ("r", "g", "b")):
        try:
            return [float(getattr(value, name)) for name in names]
        except (AttributeError, TypeError, ValueError):
            continue
    if isinstance(value, (list, tuple)) and len(value) >= 3 and not isinstance(value, (str, bytes)):
        try:
            return [float(value[0]), float(value[1]), float(value[2])]
        except (TypeError, ValueError):
            return None
    return None


def _native_class(runtime: Any, node: Any) -> str:
    class_of = getattr(runtime, "classOf", None)
    if callable(class_of):
        try:
            return str(class_of(node))
        except Exception:  # noqa: BLE001 - host wrappers can reject introspection.
            pass
    return type(node).__name__


def _safe_owned(runtime: Any, node: Any) -> Tuple[Any, Optional[int], Dict[str, Any]]:
    try:
        return owned_light(runtime, node)
    except Exception:  # noqa: BLE001 - rollback must still delete the node.
        return node, getattr(node, "handle", None), {}
