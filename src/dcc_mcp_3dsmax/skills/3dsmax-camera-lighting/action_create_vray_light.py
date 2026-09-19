"""Create one or more V-Ray lights as a single verified transaction."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._camera_light_utils import cam_error
from dcc_mcp_3dsmax._vray_utils import create_vray_lights
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    lights: Optional[List[Dict[str, Any]]] = None,
    name: Optional[str] = None,
    shape: Optional[str] = None,
    position: Optional[Sequence[float]] = None,
    target_position: Optional[Sequence[float]] = None,
    targeted: Optional[bool] = None,
    units: Optional[str] = None,
    multiplier: Optional[float] = None,
    color: Optional[Sequence[int]] = None,
    cast_shadows: Optional[bool] = None,
    normalize_color: Optional[bool] = None,
    size_u: Optional[float] = None,
    size_v: Optional[float] = None,
    texture_path: Optional[str] = None,
    map_type: Optional[str] = None,
    gamma: Optional[float] = None,
    color_space: Optional[str] = None,
    horizontal_rotation: Optional[float] = None,
) -> Dict[str, Any]:
    """Create V-Ray rectangle, dome, sphere, or disk lights.

    Pass ``lights`` to create up to 32 lights in one transaction, or use the
    flat arguments to create a single light. Every requested control is read
    back from the host; any mismatch rolls the whole call back.
    """
    specs: List[Dict[str, Any]] = [dict(item) for item in lights] if lights else []
    flat = {
        "name": name,
        "shape": shape,
        "position": position,
        "target_position": target_position,
        "targeted": targeted,
        "units": units,
        "multiplier": multiplier,
        "color": color,
        "cast_shadows": cast_shadows,
        "normalize_color": normalize_color,
        "size_u": size_u,
        "size_v": size_v,
        "texture_path": texture_path,
        "map_type": map_type,
        "gamma": gamma,
        "color_space": color_space,
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
    return create_vray_lights(get_runtime(), specs)
