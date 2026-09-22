"""Set light properties, including renderer light shape, units, and color."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._camera_light_utils import cam_error, cam_success, is_light, write_verified_attr
from dcc_mcp_3dsmax._light_providers import apply_light_controls, detect_provider, provider_light_summary
from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_object
from dcc_mcp_3dsmax.api import get_runtime, with_max

CONTROL_FIELDS = (
    "shape",
    "shape_value",
    "units",
    "units_value",
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
    "color_space",
)


@with_max
def main(
    light_name: Optional[str] = None,
    light_handle: Optional[int] = None,
    enabled: Optional[bool] = None,
    intensity: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    shadows: Optional[bool] = None,
    provider: Optional[str] = None,
    shape: Optional[str] = None,
    shape_value: Optional[int] = None,
    units: Optional[str] = None,
    units_value: Optional[int] = None,
    exposure: Optional[float] = None,
    color_temperature: Optional[float] = None,
    size_u: Optional[float] = None,
    size_v: Optional[float] = None,
    radius: Optional[float] = None,
    samples: Optional[float] = None,
    spread: Optional[float] = None,
    normalize_color: Optional[bool] = None,
    color_space: Optional[str] = None,
) -> Dict[str, Any]:
    """Set light properties after validating the target node.

    Common controls (enabled / intensity / color / shadows) work on every light.
    Renderer light controls (shape, units, size, color temperature, color space)
    are written through the provider that owns the target light and are verified
    by readback: a control the light rejects is reported as a failure with the
    attribute candidates, and the previous values are restored.
    """
    runtime = get_runtime()
    result, light = resolve_node_object(runtime, node_name=light_name, handle=light_handle)
    if light is None:
        return cam_error("Could not resolve light target", light=result)
    if not is_light(light, runtime=runtime):
        return cam_error("Target node is not a light", node=node_identity(light))

    values = {
        "shape": shape,
        "shape_value": shape_value,
        "units": units,
        "units_value": units_value,
        "intensity": intensity,
        "exposure": exposure,
        "color": color,
        "color_temperature": color_temperature,
        "cast_shadows": shadows,
        "size_u": size_u,
        "size_v": size_v,
        "radius": radius,
        "samples": samples,
        "spread": spread,
        "normalize_color": normalize_color,
        "color_space": color_space,
    }
    spec = {key: value for key, value in values.items() if value is not None}
    if spec.get("color_space") is not None and not spec.get("texture_path"):
        return cam_error(
            "color_space can only be set together with a texture slot",
            light=node_identity(light),
            hint="Use create_renderer_light to wire a texture with a color space.",
        )

    if not spec and enabled is None:
        return cam_error("No light properties were requested", light=node_identity(light))

    resolved = provider or detect_provider(runtime, light)
    applied: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    warnings: List[str] = []
    changed: List[str] = []

    if enabled is not None:
        enabled_result = write_verified_attr(runtime, light, ("enabled", "on"), bool(enabled))
        warnings.extend(enabled_result.get("warnings", []))
        if enabled_result.get("applied"):
            applied.append({"field": "enabled", "attribute": enabled_result.get("attribute"), "value": bool(enabled)})
            changed.append("enabled")
        else:
            failures.append(
                {
                    "field": "enabled",
                    "requested": bool(enabled),
                    "candidates": enabled_result.get("candidates", ["enabled", "on"]),
                    "error": "The light rejected or ignored the enabled control",
                }
            )

    if spec:
        control_applied, control_failures, control_warnings = apply_light_controls(
            runtime,
            light,
            spec,
            provider=resolved,
            restore_on_failure=True,
        )
        applied.extend(control_applied)
        failures.extend(control_failures)
        warnings.extend(control_warnings)
        changed.extend(entry["field"] for entry in control_applied if "field" in entry)

    summary = provider_light_summary(runtime, light, provider=resolved)
    if failures:
        return cam_error(
            "Light properties did not verify",
            provider=resolved,
            light=summary,
            failures=failures,
            applied=applied,
            warnings=warnings,
            failure_reason="light_readback_failed",
        )
    return cam_success(
        "Updated light properties",
        provider=resolved,
        light=summary,
        changed_fields=["shadows" if field == "cast_shadows" else field for field in changed],
        applied=applied,
        warnings=warnings,
        changed_light_count=1,
    )
