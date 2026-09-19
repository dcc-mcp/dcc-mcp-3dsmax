"""Move, rotate, and scale 3ds Max nodes in world or local space."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._scene_utils import (
    coerce_vector3,
    matrix_equal,
    matrix_rows,
    node_identity,
    point3_to_list,
    resolve_node_objects,
    vector_equal,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

SPACES = ("world", "local")


def _point3(runtime: Any, values: Sequence[float]) -> Any:
    return runtime.Point3(float(values[0]), float(values[1]), float(values[2]))


def _rotation_matrix(runtime: Any, values: Sequence[float]) -> Any:
    """Build the XYZ-order euler rotation matrix used by 3ds Max nodes."""
    matrix = runtime.rotateXMatrix(float(values[0]))
    matrix = matrix * runtime.rotateYMatrix(float(values[1]))
    return matrix * runtime.rotateZMatrix(float(values[2]))


def _optional_vector(value: Any, name: str) -> Optional[List[float]]:
    if value is None:
        return None
    return coerce_vector3(value, name)


@with_max
def main(
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    move: Any = None,
    rotate: Any = None,
    scale: Any = None,
    space: str = "world",
    relative: bool = True,
) -> Dict[str, Any]:
    """Apply a move, rotate, and/or scale transform and verify the result.

    ``move`` and ``rotate`` are offsets by default; with ``relative=false``
    they are interpreted as an absolute world position and absolute euler
    degrees. ``scale`` is always a per-axis multiplier applied to the node's
    current scale. Every write is confirmed by readback, so a transform the
    host did not accept is reported as a failure.
    """
    normalized_space = str(space or "world").strip().lower()
    if normalized_space not in SPACES:
        return {
            "success": False,
            "message": "Unsupported transform space",
            "data": {"space": space, "supported": list(SPACES)},
        }

    try:
        move_values = _optional_vector(move, "move")
        rotate_values = _optional_vector(rotate, "rotate")
        scale_values = _optional_vector(scale, "scale")
    except ValueError as exc:
        return {"success": False, "message": str(exc), "data": {}}

    if move_values is None and rotate_values is None and scale_values is None:
        return {
            "success": False,
            "message": "At least one of move, rotate, or scale is required",
            "data": {},
        }

    if not relative and (move_values is None and rotate_values is None):
        return {
            "success": False,
            "message": "relative=false requires move or rotate",
            "data": {},
        }

    rt = get_runtime()
    result = resolve_node_objects(rt, node_names=node_names, handles=handles)
    if not result.get("success"):
        return {"success": False, "message": result["message"], "data": result}

    transformed: List[Dict[str, Any]] = []
    for node in result["objects"]:
        identity = node_identity(node)
        record: Dict[str, Any] = {
            "node": identity,
            "space": normalized_space,
            "relative": bool(relative),
            "before": {
                "position": point3_to_list(getattr(node, "pos", None)),
                "rotation": point3_to_list(getattr(node, "rotation", None)),
                "scale": point3_to_list(getattr(node, "scale", None)),
            },
        }

        if scale_values is not None:
            current_scale = getattr(node, "scale", None)
            current_values = point3_to_list(current_scale)
            if current_values is None:
                return {
                    "success": False,
                    "message": "Node scale is not readable",
                    "data": {"node": identity, "transformed": transformed},
                }
            target_scale = _point3(
                rt,
                [
                    current_values[0] * scale_values[0],
                    current_values[1] * scale_values[1],
                    current_values[2] * scale_values[2],
                ],
            )
            try:
                node.scale = target_scale
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "message": "3ds Max rejected the scale value",
                    "data": {
                        "node": identity,
                        "error": "{}: {}".format(type(exc).__name__, exc),
                        "transformed": transformed,
                    },
                }
            if not vector_equal(getattr(node, "scale", None), target_scale):
                return {
                    "success": False,
                    "message": "Scale write did not take effect",
                    "data": {
                        "node": identity,
                        "requested": point3_to_list(target_scale),
                        "readback": point3_to_list(getattr(node, "scale", None)),
                        "transformed": transformed,
                    },
                }
            record["scale_multiplier"] = list(scale_values)

        if move_values is not None or rotate_values is not None:
            if not relative:
                target_transform = None
                try:
                    if move_values is not None:
                        node.pos = _point3(rt, move_values)
                    if rotate_values is not None:
                        node.rotation = rt.EulerAngles(
                            float(rotate_values[0]), float(rotate_values[1]), float(rotate_values[2])
                        )
                except Exception as exc:  # noqa: BLE001
                    return {
                        "success": False,
                        "message": "3ds Max rejected the absolute transform",
                        "data": {
                            "node": identity,
                            "error": "{}: {}".format(type(exc).__name__, exc),
                            "transformed": transformed,
                        },
                    }
                record["requested"] = {"position": move_values, "rotation": rotate_values}
            else:
                current = getattr(node, "transform", None)
                if matrix_rows(current) is None:
                    return {
                        "success": False,
                        "message": "Node transform matrix is not readable",
                        "data": {"node": identity, "transformed": transformed},
                    }
                target_transform = current
                if move_values is not None:
                    translation = rt.transMatrix(_point3(rt, move_values))
                    target_transform = (
                        translation * target_transform
                        if normalized_space == "local"
                        else target_transform * translation
                    )
                if rotate_values is not None:
                    rotation = _rotation_matrix(rt, rotate_values)
                    target_transform = (
                        rotation * target_transform if normalized_space == "local" else target_transform * rotation
                    )
                try:
                    node.transform = target_transform
                except Exception as exc:  # noqa: BLE001
                    return {
                        "success": False,
                        "message": "3ds Max rejected the transform",
                        "data": {
                            "node": identity,
                            "error": "{}: {}".format(type(exc).__name__, exc),
                            "transformed": transformed,
                        },
                    }
                if not matrix_equal(getattr(node, "transform", None), target_transform):
                    return {
                        "success": False,
                        "message": "Transform write did not take effect",
                        "data": {
                            "node": identity,
                            "requested": matrix_rows(target_transform),
                            "readback": matrix_rows(getattr(node, "transform", None)),
                            "transformed": transformed,
                        },
                    }
                record["requested_transform"] = matrix_rows(target_transform)

        record["after"] = {
            "position": point3_to_list(getattr(node, "pos", None)),
            "rotation": point3_to_list(getattr(node, "rotation", None)),
            "scale": point3_to_list(getattr(node, "scale", None)),
        }
        transformed.append(record)

    return {
        "success": True,
        "message": "Transformed {} node(s)".format(len(transformed)),
        "data": {
            "space": normalized_space,
            "relative": bool(relative),
            "count": len(transformed),
            "nodes": transformed,
        },
    }
