"""Report pivot, bounding box, local axes, and world transform for one node."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from dcc_mcp_3dsmax._scene_utils import (
    matrix_rows,
    node_bounding_box,
    point3_to_list,
    resolve_node_object,
    vector_equal,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


def _read(node: Any, name: str) -> Dict[str, Any]:
    try:
        value = getattr(node, name)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": "{}: {}".format(type(exc).__name__, exc)}
    if callable(value):
        return {"available": False, "error": "{} is a method".format(name)}
    return {"available": True, "value": value}


def _axis_alignment(rows: Optional[List[List[float]]]) -> Optional[Dict[str, Any]]:
    """Measure how far each local axis has drifted from its world axis."""
    if not rows or len(rows) < 3:
        return None

    axes = {}
    for index, label in enumerate(("x", "y", "z")):
        row = rows[index]
        length = math.sqrt(row[0] ** 2 + row[1] ** 2 + row[2] ** 2)
        if length <= 1e-9:
            axes[label] = {"angle_deg": None, "error": "degenerate axis"}
            continue
        unit = [row[0] / length, row[1] / length, row[2] / length]
        dot = min(1.0, max(-1.0, unit[index]))
        axes[label] = {
            "vector": [round(item, 6) for item in unit],
            "length": round(length, 6),
            "angle_deg": round(math.degrees(math.acos(dot)), 4),
        }
    return axes


@with_max
def main(node_name: Optional[str] = None, handle: Optional[int] = None) -> Dict[str, Any]:
    """Summarize the orientation of one node.

    Reads the pivot, node and world bounding boxes, local axis directions, and
    the world matrix. Every field reports ``available: false`` with a reason
    when the host does not expose it, so a partial scene never looks empty.
    """
    result, node = resolve_node_object(get_runtime(), node_name=node_name, handle=handle)
    if node is None:
        return {"success": False, "message": result["message"], "data": result}

    transform = _read(node, "transform")
    position = _read(node, "pos")
    rotation = _read(node, "rotation")
    scale = _read(node, "scale")
    pivot = _read(node, "pivot")
    object_offset = _read(node, "objectOffsetPos")
    parent = _read(node, "parent")

    rows = matrix_rows(transform.get("value")) if transform.get("available") else None
    local_axes = _axis_alignment(rows)

    identity_rows = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]]
    axis_aligned = bool(local_axes) and all(
        item.get("angle_deg") is not None and abs(item["angle_deg"]) <= 1e-3 for item in local_axes.values()
    )

    bounding_box = node_bounding_box(node)
    has_bbox = bounding_box.get("min") is not None and bounding_box.get("max") is not None

    data: Dict[str, Any] = {
        "node": result.get("node"),
        "parent": str(getattr(parent.get("value"), "name", "")) if parent.get("available") else None,
        "pivot": {
            "available": pivot.get("available"),
            "value": point3_to_list(pivot.get("value")) if pivot.get("available") else None,
            "error": pivot.get("error"),
        },
        "object_offset": {
            "available": object_offset.get("available"),
            "value": point3_to_list(object_offset.get("value")) if object_offset.get("available") else None,
            "error": object_offset.get("error"),
        },
        "transform": {
            "available": transform.get("available"),
            "rows": rows,
            "error": transform.get("error"),
        },
        "position": point3_to_list(position.get("value")) if position.get("available") else None,
        "rotation": point3_to_list(rotation.get("value")) if rotation.get("available") else None,
        "scale": point3_to_list(scale.get("value")) if scale.get("available") else None,
        "local_axes": local_axes,
        "bounding_box": bounding_box if has_bbox else None,
        "identity_transform": bool(
            transform.get("available") and vector_equal(rows[3], [0.0, 0.0, 0.0]) and axis_aligned
        )
        if rows is not None
        else None,
        "axis_aligned": axis_aligned if local_axes else None,
        "reference_identity_rows": identity_rows,
        "unavailable": [
            name
            for name, entry in (
                ("transform", transform),
                ("pos", position),
                ("rotation", rotation),
                ("scale", scale),
                ("pivot", pivot),
            )
            if not entry.get("available")
        ],
    }

    return {
        "success": True,
        "message": "Analyzed orientation for {}".format(result.get("node", {}).get("node_name")),
        "data": data,
    }
