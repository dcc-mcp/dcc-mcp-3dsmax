"""V-Ray specific light and HDRI bitmap helpers for 3ds Max.

V-Ray exposes its light shapes, photometric units, and HDRI projection through
renamed properties across major versions (VRayHDRI -> VRayBitmap, glossiness ->
roughness in V-Ray 5). Rather than guessing one spelling, every control walks an
ordered list of candidate native properties, writes the first one the host
exposes, and then reads it back.

A control the host refuses or silently ignores is reported as an error, never as
a success: an agent must not believe a dome light was wired when the texmap slot
stayed empty.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._camera_light_utils import (
    cam_error,
    cam_success,
    float_matches,
    host_has_property,
    light_summary,
    owned_light,
    point3_value,
    rollback_owned_nodes,
    rollback_summary,
    runtime_color,
)

VRAY_LIGHT_FACTORIES = ("VRayLight", "VRay_Light")

# V-Ray 3+ exposes every light shape through one class; the ``type`` index is the
# documented order (plane, dome, sphere, mesh, disc).
VRAY_LIGHT_SHAPES = {
    "rectangle": 0,
    "plane": 0,
    "environment": 1,
    "dome": 1,
    "sphere": 2,
    "mesh": 3,
    "disk": 4,
    "disc": 4,
}
VRAY_LIGHT_SHAPE_ATTRS = ("type", "shape", "lightShape")
VRAY_LIGHT_SHAPE_NAMES = ("rectangle", "environment", "sphere", "mesh", "disk")

VRAY_LIGHT_UNITS = {
    "renderer": 0,
    "default": 0,
    "lm": 1,
    "luminous_power": 1,
    "cd_m2": 2,
    "luminance": 2,
    "w": 3,
    "radiant_power": 3,
    "radiance": 4,
    "w_sr_m2": 4,
}
VRAY_LIGHT_UNITS_ATTRS = ("units",)
VRAY_LIGHT_UNIT_NAMES = ("renderer", "lm", "cd_m2", "w", "radiance")

VRAY_LIGHT_MULTIPLIER_ATTRS = ("multiplier", "intensity")
VRAY_LIGHT_COLOR_ATTRS = ("color",)
VRAY_LIGHT_SHADOW_ATTRS = ("castShadows", "cast_shadows")
VRAY_LIGHT_NORMALIZE_ATTRS = ("normalizeColor", "normalize_color")
VRAY_LIGHT_TARGETED_ATTRS = ("targeted",)
VRAY_LIGHT_SIZE_U_ATTRS = ("U_size", "u_size", "sizeU", "size0")
VRAY_LIGHT_SIZE_V_ATTRS = ("V_size", "v_size", "sizeV", "size1")
VRAY_LIGHT_TEXMAP_ATTRS = ("texmap", "dome_tex", "domeTex", "texture")

VRAY_BITMAP_FACTORIES = ("VRayBitmap", "VRayHDRI")
VRAY_BITMAP_FILE_ATTRS = ("bitmap", "HDRI", "texmap")
VRAY_BITMAP_FILE_NAME_ATTRS = ("filename", "fileName", "path", "HDRIName")
VRAY_BITMAP_MAP_TYPES = {
    "angular": 0,
    "cubic": 1,
    "spherical": 2,
    "mirrored_ball": 3,
    "max_standard": 4,
}
VRAY_BITMAP_MAP_TYPE_ATTRS = ("HDRIMapType", "mapType", "mappingType")
VRAY_BITMAP_GAMMA_ATTRS = ("gamma", "HDRIGamma")
VRAY_BITMAP_COLOR_SPACE_ATTRS = ("color_space", "colorSpace", "colorspace")
VRAY_BITMAP_ROTATION_ATTRS = ("horizontalRotation", "horizRotation", "horizontal_rotation", "uRotation")

MAX_LIGHTS_PER_CALL = 32

LIGHT_SPEC_FIELDS = (
    "name",
    "shape",
    "position",
    "target_position",
    "targeted",
    "units",
    "multiplier",
    "color",
    "cast_shadows",
    "normalize_color",
    "size_u",
    "size_v",
    "texture_path",
    "map_type",
    "gamma",
    "color_space",
    "horizontal_rotation",
)


def create_vray_lights(runtime: Any, specs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Create one or more V-Ray lights as a single verified transaction."""
    if not specs:
        return cam_error("At least one V-Ray light spec is required", lights=[])
    if len(specs) > MAX_LIGHTS_PER_CALL:
        return cam_error(
            "Too many V-Ray lights in one call",
            requested=len(specs),
            maximum=MAX_LIGHTS_PER_CALL,
        )

    # Validate every spec before the first node exists: a malformed value must
    # never leave an already-created light behind.
    errors: List[Dict[str, Any]] = []
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            errors.append({"index": index, "message": "Light spec must be an object", "spec": spec})
            continue
        unknown = sorted(set(spec) - set(LIGHT_SPEC_FIELDS))
        if unknown:
            errors.append(
                {
                    "index": index,
                    "message": "Unsupported V-Ray light fields: {}".format(", ".join(unknown)),
                }
            )
            continue
        invalid = _invalid_spec_values(spec)
        if invalid:
            errors.append(
                {
                    "index": index,
                    "message": "Invalid V-Ray light values",
                    "fields": invalid,
                }
            )
    if errors:
        return cam_error(
            "Rejected V-Ray light specs before creating anything",
            errors=errors,
            failure_reason="vray_light_spec_invalid",
            **rollback_summary([]),
        )

    created: List[Dict[str, Any]] = []
    # The node is registered the moment it exists, so even an unexpected
    # exception later in the pipeline leaves it tracked for rollback.
    raw_nodes: List[Any] = []
    for index, spec in enumerate(specs):
        try:
            result, node = _create_vray_light_with_node(runtime, spec, sink=raw_nodes)
        except Exception as exc:  # noqa: BLE001 - a crash must still roll back.
            errors.append(
                {
                    "index": index,
                    **cam_error(
                        "V-Ray light creation raised an error",
                        exception_type=type(exc).__name__,
                        error=str(exc),
                    ),
                }
            )
            break
        if not result.get("success"):
            errors.append({"index": index, **result})
            break
        created.append(result["data"]["light"])

    if errors:
        incomplete = rollback_owned_nodes(runtime, _owned_nodes(runtime, raw_nodes))
        return cam_error(
            "Could not create the requested V-Ray lights",
            errors=errors,
            created_light_count=len(created),
            **rollback_summary(incomplete),
        )
    return cam_success("Created V-Ray lights", lights=created, changed_node_count=len(created))


def _owned_nodes(runtime: Any, nodes: Sequence[Any]) -> List[Tuple[Any, Optional[int], Dict[str, Any]]]:
    """Snapshot owned nodes for rollback without letting diagnostics raise."""
    owned: List[Tuple[Any, Optional[int], Dict[str, Any]]] = []
    for node in nodes:
        try:
            owned.append(owned_light(runtime, node))
        except Exception:  # noqa: BLE001 - rollback must still delete the node.
            owned.append((node, getattr(node, "handle", None), {}))
    return owned


def _invalid_spec_values(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return every spec field whose value cannot be used as requested."""
    invalid: List[Dict[str, Any]] = []

    def _reject(field: str, value: Any, reason: str) -> None:
        invalid.append({"field": field, "value": _jsonable(value), "reason": reason})

    def _number(field: str, value: Any) -> None:
        try:
            float(value)
        except (TypeError, ValueError):
            _reject(field, value, "expected a number")

    def _sequence(field: str, value: Any, length: int) -> None:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            _reject(field, value, "expected a list of {} numbers".format(length))
            return
        if len(value) < length:
            _reject(field, value, "expected at least {} numbers".format(length))
            return
        for item in value[:length]:
            try:
                float(item)
            except (TypeError, ValueError):
                _reject(field, value, "expected numeric channels")
                return

    for field in ("name", "texture_path", "color_space"):
        value = spec.get(field)
        if value is not None and not isinstance(value, str):
            _reject(field, value, "expected a string")
    for field in ("targeted", "cast_shadows", "normalize_color"):
        value = spec.get(field)
        if value is not None and not isinstance(value, bool):
            _reject(field, value, "expected a boolean")
    for field in ("multiplier", "size_u", "size_v", "gamma", "horizontal_rotation"):
        if spec.get(field) is not None:
            _number(field, spec[field])
    for field in ("position", "target_position"):
        if spec.get(field) is not None:
            _sequence(field, spec[field], 3)
    if spec.get("color") is not None:
        _sequence("color", spec["color"], 3)
    shape = spec.get("shape")
    if shape is not None and str(shape).strip().lower() not in VRAY_LIGHT_SHAPES:
        _reject("shape", shape, "unsupported V-Ray light shape")
    units = spec.get("units")
    if units is not None and str(units).strip().lower() not in VRAY_LIGHT_UNITS:
        _reject("units", units, "unsupported V-Ray light unit")
    map_type = spec.get("map_type")
    if map_type is not None and str(map_type).strip().lower() not in VRAY_BITMAP_MAP_TYPES:
        _reject("map_type", map_type, "unsupported V-Ray HDRI map type")
    return invalid


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    return str(value)


def _create_vray_light_with_node(
    runtime: Any,
    spec: Dict[str, Any],
    *,
    sink: Optional[List[Any]] = None,
) -> Tuple[Dict[str, Any], Optional[Any]]:
    """Create one V-Ray light, verify every requested control, and return it.

    ``sink`` receives the node as soon as it is constructed so the caller can
    roll it back even when a later step raises.
    """
    factory_errors: List[str] = []
    light = None
    for factory_name in VRAY_LIGHT_FACTORIES:
        factory = getattr(runtime, factory_name, None)
        if not callable(factory):
            continue
        try:
            light = factory()
            break
        except Exception as exc:  # noqa: BLE001 - try the next V-Ray factory spelling.
            factory_errors.append("Could not create {}: {}".format(factory_name, exc))
    if light is not None and sink is not None:
        sink.append(light)
    if light is None:
        return (
            cam_error(
                "No supported V-Ray light factory was available",
                factories=list(VRAY_LIGHT_FACTORIES),
                warnings=factory_errors,
                failure_reason="vray_light_factory_unavailable",
            ),
            None,
        )

    name = str(spec.get("name") or "").strip()
    if name:
        _set_plain(light, "name", name)

    summary = vray_light_summary(light, runtime=runtime)
    if "vray" not in summary["native_class"].lower():
        return (
            cam_error(
                "V-Ray light factory returned an incompatible class",
                native_class=summary["native_class"],
                light=summary,
            ),
            light,
        )

    failures: List[Dict[str, Any]] = []
    warnings: List[str] = []

    shape = spec.get("shape")
    if shape is not None:
        resolved_shape = VRAY_LIGHT_SHAPES.get(str(shape).strip().lower())
        if resolved_shape is None:
            failures.append(
                {
                    "field": "shape",
                    "requested": shape,
                    "supported": sorted(VRAY_LIGHT_SHAPES),
                    "error": "Unsupported V-Ray light shape",
                }
            )
        else:
            result = _apply_attr(runtime, light, VRAY_LIGHT_SHAPE_ATTRS, resolved_shape)
            _collect(result, "shape", failures, warnings)

    units = spec.get("units")
    if units is not None:
        resolved_units = VRAY_LIGHT_UNITS.get(str(units).strip().lower())
        if resolved_units is None:
            failures.append(
                {
                    "field": "units",
                    "requested": units,
                    "supported": sorted(VRAY_LIGHT_UNITS),
                    "error": "Unsupported V-Ray light unit",
                }
            )
        else:
            result = _apply_attr(runtime, light, VRAY_LIGHT_UNITS_ATTRS, resolved_units)
            _collect(result, "units", failures, warnings)

    multiplier = spec.get("multiplier")
    if multiplier is not None:
        result = _apply_attr(runtime, light, VRAY_LIGHT_MULTIPLIER_ATTRS, float(multiplier), numeric=True)
        _collect(result, "multiplier", failures, warnings)

    color = spec.get("color")
    if color is not None:
        result = _apply_attr(runtime, light, VRAY_LIGHT_COLOR_ATTRS, runtime_color(runtime, color), color=True)
        _collect(result, "color", failures, warnings)

    for field, attributes in (
        ("cast_shadows", VRAY_LIGHT_SHADOW_ATTRS),
        ("normalize_color", VRAY_LIGHT_NORMALIZE_ATTRS),
        ("targeted", VRAY_LIGHT_TARGETED_ATTRS),
    ):
        value = spec.get(field)
        if value is None:
            continue
        result = _apply_attr(runtime, light, attributes, bool(value))
        _collect(result, field, failures, warnings)

    for field, attributes in (
        ("size_u", VRAY_LIGHT_SIZE_U_ATTRS),
        ("size_v", VRAY_LIGHT_SIZE_V_ATTRS),
    ):
        value = spec.get(field)
        if value is None:
            continue
        result = _apply_attr(runtime, light, attributes, float(value), numeric=True)
        _collect(result, field, failures, warnings)

    position = spec.get("position")
    if position is not None:
        expected = [float(value) for value in position[:3]]
        _set_plain(light, "position", point3_value(runtime, position))
        _set_plain(light, "pos", point3_value(runtime, position))
        actual = _vector_or_none(_read_attr_any(runtime, light, ("position", "pos")))
        if not _vector_matches(actual, expected):
            failures.append(
                {
                    "field": "position",
                    "requested": expected,
                    "actual": actual,
                    "error": "V-Ray light position readback did not match",
                }
            )

    target_position = spec.get("target_position")
    if target_position is not None:
        expected = [float(value) for value in target_position[:3]]
        target = _resolve_target(runtime, light)
        if target is None:
            failures.append(
                {
                    "field": "target_position",
                    "requested": expected,
                    "error": "V-Ray light exposes no target node to aim",
                }
            )
        else:
            _set_plain(target, "position", point3_value(runtime, target_position))
            _set_plain(target, "pos", point3_value(runtime, target_position))
            actual = _vector_or_none(_read_attr_any(runtime, target, ("position", "pos")))
            if not _vector_matches(actual, expected):
                failures.append(
                    {
                        "field": "target_position",
                        "requested": expected,
                        "actual": actual,
                        "error": "V-Ray light target position readback did not match",
                    }
                )

    name = spec.get("name")
    if name:
        actual_name = _read_attr_any(runtime, light, ("name",))
        if str(actual_name) != str(name):
            failures.append(
                {
                    "field": "name",
                    "requested": str(name),
                    "actual": None if actual_name is None else str(actual_name),
                    "error": "V-Ray light name readback did not match",
                }
            )

    texture_path = spec.get("texture_path")
    bitmap_report: Optional[Dict[str, Any]] = None
    if texture_path:
        bitmap, bitmap_report = create_vray_bitmap(
            runtime,
            str(texture_path),
            map_type=spec.get("map_type"),
            gamma=spec.get("gamma"),
            color_space=spec.get("color_space"),
            horizontal_rotation=spec.get("horizontal_rotation"),
        )
        warnings.extend(bitmap_report.get("warnings", []))
        for error in bitmap_report.get("errors", []):
            failures.append({"field": "texture_path", **error})
        if bitmap is None:
            failures.append(
                {
                    "field": "texture_path",
                    "error": "V-Ray bitmap could not be created",
                    "details": bitmap_report.get("errors", []),
                }
            )
        else:
            result = _apply_attr(runtime, light, VRAY_LIGHT_TEXMAP_ATTRS, bitmap, texture=True)
            if not result.get("applied"):
                _collect(result, "texture_path", failures, warnings)
            else:
                bitmap_report["slot_attribute"] = result.get("attribute")

    summary = vray_light_summary(light, runtime=runtime)
    failures.extend(_verify_summary(spec, summary))

    data = {
        "light": summary,
        "warnings": warnings,
        "bitmap": bitmap_report,
        "changed_node_count": 1,
    }
    if failures:
        return (
            cam_error(
                "V-Ray light controls did not verify",
                light=summary,
                failures=failures,
                warnings=warnings,
                failure_reason="vray_light_readback_failed",
            ),
            light,
        )
    return cam_success("Created V-Ray light", **data), light


def _verify_summary(spec: Dict[str, Any], summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compare the post-write readback against every requested control."""
    failures: List[Dict[str, Any]] = []
    shape = spec.get("shape")
    if shape is not None:
        expected = VRAY_LIGHT_SHAPES.get(str(shape).strip().lower())
        if summary.get("shape_value") != expected:
            failures.append(
                {
                    "field": "shape",
                    "requested": shape,
                    "expected_value": expected,
                    "actual": summary.get("shape_value"),
                    "error": "V-Ray light shape readback did not match",
                }
            )
    units = spec.get("units")
    if units is not None:
        expected = VRAY_LIGHT_UNITS.get(str(units).strip().lower())
        if summary.get("units_value") != expected:
            failures.append(
                {
                    "field": "units",
                    "requested": units,
                    "expected_value": expected,
                    "actual": summary.get("units_value"),
                    "error": "V-Ray light unit readback did not match",
                }
            )
    multiplier = spec.get("multiplier")
    if multiplier is not None and not float_matches(summary.get("intensity"), float(multiplier)):
        failures.append(
            {
                "field": "multiplier",
                "requested": float(multiplier),
                "actual": summary.get("intensity"),
                "error": "V-Ray light multiplier readback did not match",
            }
        )
    color = spec.get("color")
    if color is not None and summary.get("color") != _clamp_color(color):
        failures.append(
            {
                "field": "color",
                "requested": _clamp_color(color),
                "actual": summary.get("color"),
                "error": "V-Ray light color readback did not match",
            }
        )
    for field, key in (
        ("cast_shadows", "shadows"),
        ("normalize_color", "normalize_color"),
        ("targeted", "targeted"),
    ):
        value = spec.get(field)
        if value is None:
            continue
        if summary.get(key) is not bool(value):
            failures.append(
                {
                    "field": field,
                    "requested": bool(value),
                    "actual": summary.get(key),
                    "error": "V-Ray light {} readback did not match".format(field),
                }
            )
    for field, key in (("size_u", "size_u"), ("size_v", "size_v")):
        value = spec.get(field)
        if value is None:
            continue
        if not float_matches(summary.get(key), float(value)):
            failures.append(
                {
                    "field": field,
                    "requested": float(value),
                    "actual": summary.get(key),
                    "error": "V-Ray light {} readback did not match".format(field),
                }
            )
    return failures


def vray_light_summary(node: Any, *, runtime: Any = None) -> Dict[str, Any]:
    """Return the common light summary plus V-Ray specific readbacks."""
    summary = light_summary(node, runtime=runtime)
    shape_value = _read_enum(runtime, node, VRAY_LIGHT_SHAPE_ATTRS)
    units_value = _read_enum(runtime, node, VRAY_LIGHT_UNITS_ATTRS)
    texture = _read_attr_any(runtime, node, VRAY_LIGHT_TEXMAP_ATTRS)
    summary.update(
        {
            "shape": _shape_name(shape_value),
            "shape_value": shape_value,
            "units": _units_name(units_value),
            "units_value": units_value,
            "normalize_color": _read_bool(runtime, node, VRAY_LIGHT_NORMALIZE_ATTRS),
            "targeted": _read_bool(runtime, node, VRAY_LIGHT_TARGETED_ATTRS),
            "size_u": _read_number(runtime, node, VRAY_LIGHT_SIZE_U_ATTRS),
            "size_v": _read_number(runtime, node, VRAY_LIGHT_SIZE_V_ATTRS),
            "texture": _bitmap_path(texture),
            "texture_type": type(texture).__name__ if texture is not None else None,
        }
    )
    return summary


def create_vray_bitmap(
    runtime: Any,
    texture_path: str,
    *,
    map_type: Optional[str] = None,
    gamma: Optional[float] = None,
    color_space: Optional[str] = None,
    horizontal_rotation: Optional[float] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Create a VRayBitmap texmap and apply the requested projection controls."""
    report: Dict[str, Any] = {
        "path": texture_path,
        "type": None,
        "applied": [],
        "errors": [],
        "warnings": [],
    }
    bitmap = None
    factory_errors: List[str] = []
    for factory_name in VRAY_BITMAP_FACTORIES:
        factory = getattr(runtime, factory_name, None)
        if not callable(factory):
            continue
        try:
            bitmap = factory()
            break
        except Exception as exc:  # noqa: BLE001 - try the next V-Ray bitmap spelling.
            factory_errors.append("Could not create {}: {}".format(factory_name, exc))
    if bitmap is None:
        report["errors"].append(
            {
                "field": "texture_path",
                "error": "No supported V-Ray bitmap factory was available",
                "factories": list(VRAY_BITMAP_FACTORIES),
                "warnings": factory_errors,
            }
        )
        return None, report

    report["type"] = type(bitmap).__name__
    file_result = _apply_vray_bitmap_file(runtime, bitmap, texture_path)
    if file_result.get("applied"):
        report["applied"].append({"field": "texture_path", "attribute": file_result.get("attribute")})
    else:
        report["errors"].append(
            {
                "field": "texture_path",
                "error": "V-Ray bitmap did not retain the image path",
                "candidates": list(VRAY_BITMAP_FILE_ATTRS + VRAY_BITMAP_FILE_NAME_ATTRS),
                "warnings": file_result.get("warnings", []),
            }
        )

    if map_type is not None:
        resolved = VRAY_BITMAP_MAP_TYPES.get(str(map_type).strip().lower())
        if resolved is None:
            report["errors"].append(
                {
                    "field": "map_type",
                    "requested": map_type,
                    "supported": sorted(VRAY_BITMAP_MAP_TYPES),
                    "error": "Unsupported V-Ray HDRI map type",
                }
            )
        else:
            result = _apply_attr(runtime, bitmap, VRAY_BITMAP_MAP_TYPE_ATTRS, resolved)
            _collect(result, "map_type", report["errors"], report["warnings"])
            if result.get("applied"):
                report["applied"].append({"field": "map_type", "attribute": result.get("attribute")})

    for field, value, attributes, numeric in (
        ("gamma", gamma, VRAY_BITMAP_GAMMA_ATTRS, True),
        ("horizontal_rotation", horizontal_rotation, VRAY_BITMAP_ROTATION_ATTRS, True),
    ):
        if value is None:
            continue
        result = _apply_attr(runtime, bitmap, attributes, float(value), numeric=numeric)
        _collect(result, field, report["errors"], report["warnings"])
        if result.get("applied"):
            report["applied"].append({"field": field, "attribute": result.get("attribute")})

    if color_space is not None:
        result = _apply_attr(runtime, bitmap, VRAY_BITMAP_COLOR_SPACE_ATTRS, str(color_space))
        _collect(result, "color_space", report["errors"], report["warnings"])
        if result.get("applied"):
            report["applied"].append({"field": "color_space", "attribute": result.get("attribute")})

    return bitmap, report


def _apply_vray_bitmap_file(runtime: Any, bitmap: Any, texture_path: str) -> Dict[str, Any]:
    """Load the image into a VRayBitmap, nesting a native bitmap when required."""
    warnings: List[str] = []
    for attribute in VRAY_BITMAP_FILE_NAME_ATTRS:
        result = _apply_attr(runtime, bitmap, (attribute,), texture_path)
        if result.get("applied"):
            return result
        warnings.extend(result.get("warnings", []))

    source = _create_native_bitmap(runtime, texture_path)
    for attribute in VRAY_BITMAP_FILE_ATTRS:
        result = _apply_attr(runtime, bitmap, (attribute,), source, texture=True)
        if result.get("applied"):
            result.setdefault("warnings", []).extend(warnings)
            return result
        warnings.extend(result.get("warnings", []))
    return {"applied": False, "attribute": None, "warnings": warnings}


def _create_native_bitmap(runtime: Any, texture_path: str) -> Any:
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


def _apply_attr(
    runtime: Any,
    node: Any,
    attributes: Sequence[str],
    value: Any,
    *,
    numeric: bool = False,
    color: bool = False,
    texture: bool = False,
) -> Dict[str, Any]:
    """Write one control onto the first native attribute that accepts it."""
    warnings: List[str] = []
    for attribute in attributes:
        if not _host_has_property(runtime, node, attribute):
            continue
        try:
            setattr(node, attribute, value)
        except Exception as exc:  # noqa: BLE001 - readback is the fail-closed boundary.
            warnings.append("Could not set {}: {}".format(attribute, exc))
            continue
        readback = _read_attr_any(runtime, node, (attribute,))
        if texture:
            if _is_same_map(readback, value):
                return {"applied": True, "attribute": attribute, "warnings": warnings}
        elif color:
            if _color_matches(readback, value):
                return {"applied": True, "attribute": attribute, "warnings": warnings}
        elif numeric:
            if _numeric_matches(readback, value):
                return {"applied": True, "attribute": attribute, "warnings": warnings}
        elif readback == value or (isinstance(value, bool) and readback is value):
            return {"applied": True, "attribute": attribute, "warnings": warnings}
        warnings.append("{} read back {!r} after writing {!r}".format(attribute, readback, value))
    return {
        "applied": False,
        "attribute": None,
        "candidates": list(attributes),
        "warnings": warnings,
    }


def _collect(
    result: Dict[str, Any],
    field: str,
    failures: List[Dict[str, Any]],
    warnings: List[str],
) -> None:
    warnings.extend(result.get("warnings", []))
    if not result.get("applied"):
        failures.append(
            {
                "field": field,
                "candidates": result.get("candidates", []),
                "error": "V-Ray rejected or ignored the {} control".format(field),
            }
        )


def _resolve_target(runtime: Any, light: Any) -> Any:
    for attribute in ("target", "target_object", "targetObject"):
        if not _host_has_property(runtime, light, attribute):
            continue
        target = _read_attr_any(runtime, light, (attribute,))
        if target is not None:
            return target
    return None


def _host_has_property(runtime: Any, node: Any, attribute: str) -> bool:
    if runtime is None:
        return True
    return host_has_property(runtime, node, attribute)


def _read_attr_any(runtime: Any, node: Any, attributes: Sequence[str]) -> Any:
    for attribute in attributes:
        if not _host_has_property(runtime, node, attribute):
            continue
        try:
            return getattr(node, attribute)
        except Exception:  # noqa: BLE001 - pymxs wrappers reject unknown properties.
            continue
    return None


def _read_enum(runtime: Any, node: Any, attributes: Sequence[str]) -> Optional[int]:
    value = _read_attr_any(runtime, node, attributes)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_number(runtime: Any, node: Any, attributes: Sequence[str]) -> Optional[float]:
    value = _read_attr_any(runtime, node, attributes)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_bool(runtime: Any, node: Any, attributes: Sequence[str]) -> Optional[bool]:
    value = _read_attr_any(runtime, node, attributes)
    if value is None:
        return None
    return bool(value)


def _vector_matches(readback: Optional[List[float]], expected: Sequence[float]) -> bool:
    if readback is None or len(readback) < 3:
        return False
    return all(
        math.isclose(float(readback[index]), float(expected[index]), rel_tol=1e-6, abs_tol=1e-6)
        for index in range(3)
    )


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


def _numeric_matches(readback: Any, expected: float) -> bool:
    if readback is None or isinstance(readback, bool):
        return False
    try:
        return math.isclose(float(readback), float(expected), rel_tol=1e-6, abs_tol=1e-6)
    except (TypeError, ValueError):
        return False


def _color_matches(readback: Any, expected: Any) -> bool:
    for value in (readback, expected):
        if value is None:
            return False
    actual = _color_channels(readback)
    target = _color_channels(expected)
    if actual is None or target is None:
        return readback == expected
    return all(math.isclose(actual[index], target[index], rel_tol=1e-3, abs_tol=1e-3) for index in range(3))


def _color_channels(value: Any) -> Optional[List[float]]:
    for names in (("r", "g", "b"), ("red", "green", "blue")):
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


def _is_same_map(readback: Any, expected: Any) -> bool:
    if readback is None or expected is None:
        return False
    if readback is expected:
        return True
    actual_path = _bitmap_path(readback)
    expected_path = _bitmap_path(expected)
    return bool(actual_path) and actual_path == expected_path


def _bitmap_path(value: Any) -> str:
    if value is None:
        return ""
    direct = getattr(value, "filename", "") or getattr(value, "path", "")
    if direct:
        return str(direct)
    for attribute in ("bitmap", "texmap", "normal_map", "normalMap"):
        nested = getattr(value, attribute, None)
        if nested is not None and nested is not value:
            nested_path = _bitmap_path(nested)
            if nested_path:
                return nested_path
    return ""


def _shape_name(value: Optional[int]) -> Optional[str]:
    if value is None or value < 0 or value >= len(VRAY_LIGHT_SHAPE_NAMES):
        return None
    return VRAY_LIGHT_SHAPE_NAMES[value]


def _units_name(value: Optional[int]) -> Optional[str]:
    if value is None or value < 0 or value >= len(VRAY_LIGHT_UNIT_NAMES):
        return None
    return VRAY_LIGHT_UNIT_NAMES[value]


def _clamp_color(value: Sequence[Any]) -> List[int]:
    channels = [int(value[index]) for index in range(3)]
    return [max(0, min(255, channel)) for channel in channels]


def _set_plain(node: Any, attribute: str, value: Any) -> None:
    try:
        setattr(node, attribute, value)
    except Exception:  # noqa: BLE001 - readback remains the authoritative check.
        pass
