"""Create a box primitive in 3ds Max."""

# Import future modules
from __future__ import annotations

# Import built-in modules
import math
from typing import Any

# Import local modules
from dcc_mcp_3dsmax._scene_utils import set_node_position
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    width: float = 100.0,
    height: float = 100.0,
    depth: float = 100.0,
    name: str = None,
    position: Any = None,
) -> dict:
    """Create a box in scene system units; depth maps to native length."""
    requested = {"width": width, "height": height, "depth": depth}
    for dimension, value in requested.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return {"success": False, "message": "{} must be a positive finite number".format(dimension), "data": {}}

    rt = get_runtime()
    box_obj = rt.Box(width=width, height=height, length=depth)

    if name:
        box_obj.name = name
    applied_position = set_node_position(rt, box_obj, position)
    data = {
        "node_name": str(box_obj.name),
        "object_id": int(box_obj.handle) if hasattr(box_obj, "handle") else None,
        "position": applied_position,
    }
    try:
        dimensions = {"width": float(box_obj.width), "height": float(box_obj.height), "depth": float(box_obj.length)}
        if not all(math.isfinite(value) for value in dimensions.values()):
            raise ValueError("3ds Max returned non-finite box dimensions")
    except Exception as exc:
        return {"success": False, "message": "Box dimensions could not be read back: {}".format(exc), "data": data}

    data.update(dimensions)
    # Native dimensions use single precision, so compare relative to the scene-unit value.
    if not all(math.isclose(dimensions[key], value, rel_tol=1e-6, abs_tol=0.0) for key, value in requested.items()):
        data["requested"] = requested
        return {"success": False, "message": "Box dimensions did not match the request", "data": data}

    return {"success": True, "message": f"Created box: {box_obj.name}", "data": data}
