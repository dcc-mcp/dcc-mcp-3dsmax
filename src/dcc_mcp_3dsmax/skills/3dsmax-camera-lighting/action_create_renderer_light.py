"""Create Arnold, Corona, or photometric lights as one verified transaction."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._camera_light_utils import cam_error
from dcc_mcp_3dsmax._light_providers import (
    GENERIC_FIELDS,
    VRAY_FIELDS,
    create_renderer_lights,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

FLAT_FIELDS = tuple(dict.fromkeys(GENERIC_FIELDS + VRAY_FIELDS)) + ("provider",)


@with_max
def main(
    lights: Optional[List[Dict[str, Any]]] = None,
    provider: str = "auto",
    name: Optional[str] = None,
    kind: Optional[str] = None,
    shape: Optional[str] = None,
    shape_value: Optional[int] = None,
    units: Optional[str] = None,
    units_value: Optional[int] = None,
    position: Optional[Sequence[float]] = None,
    target_position: Optional[Sequence[float]] = None,
    targeted: Optional[bool] = None,
    intensity: Optional[float] = None,
    exposure: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    color_temperature: Optional[float] = None,
    cast_shadows: Optional[bool] = None,
    size_u: Optional[float] = None,
    size_v: Optional[float] = None,
    radius: Optional[float] = None,
    samples: Optional[float] = None,
    spread: Optional[float] = None,
    normalize_color: Optional[bool] = None,
    texture_path: Optional[str] = None,
    color_space: Optional[str] = None,
    map_type: Optional[str] = None,
    gamma: Optional[float] = None,
    horizontal_rotation: Optional[float] = None,
) -> Dict[str, Any]:
    """Create up to 32 lights in one transaction.

    Pass ``lights`` for a batch, or the flat arguments for a single light.
    Every requested control is read back from the host; anything the host
    rejects or silently ignores fails the call and rolls every light it created
    back.
    """
    specs: List[Dict[str, Any]] = [dict(item) for item in lights] if lights else []
    flat = {
        "name": name,
        "kind": kind,
        "shape": shape,
        "shape_value": shape_value,
        "units": units,
        "units_value": units_value,
        "position": position,
        "target_position": target_position,
        "targeted": targeted,
        "intensity": intensity,
        "exposure": exposure,
        "color": color,
        "color_temperature": color_temperature,
        "cast_shadows": cast_shadows,
        "size_u": size_u,
        "size_v": size_v,
        "radius": radius,
        "samples": samples,
        "spread": spread,
        "normalize_color": normalize_color,
        "texture_path": texture_path,
        "color_space": color_space,
        "map_type": map_type,
        "gamma": gamma,
        "horizontal_rotation": horizontal_rotation,
    }
    provided = {key: value for key, value in flat.items() if value is not None}
    if specs and provided:
        return cam_error(
            "Pass either lights or single-light arguments, not both",
            provided_single_light_fields=sorted(provided),
        )
    if not specs:
        specs = [provided] if provided else []
    return create_renderer_lights(get_runtime(), specs, provider=provider or "auto")
