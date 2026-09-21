"""Create, append to, or replace a spline shape from world-space points."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._curve_utils import (
    curve_error,
    curve_success,
    delete_node,
    positions_match,
    read_spline_state,
    update_shape,
    validated_bool,
    validated_int,
    validated_knot_type,
    validated_name,
    validated_vectors,
    world_to_object,
)
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max

_SEGMENT_TYPES = ("line", "curve")


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


@with_max
def main(
    points: Sequence[Sequence[float]],
    mode: str = "create",
    name: Optional[str] = None,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    spline_index: int = 1,
    closed: bool = False,
    knot_type: str = "corner",
    curve_type: str = "line",
) -> Dict[str, Any]:
    """Create or extend a spline shape from an ordered world-space point list.

    Every requested point is converted into the shape's object space, written,
    committed with ``updateShape``, and read back in world space. A single
    point that does not come back is a failure, and a node this call created is
    deleted again before the failure is reported.
    """
    try:
        normalized_points = validated_vectors(points, "points")
        normalized_name = validated_name(name)
        normalized_node_name = validated_name(node_name, "node_name")
        normalized_knot_type = validated_knot_type(knot_type)
        normalized_closed = validated_bool(closed, "closed")
        normalized_index = validated_int(spline_index, "spline_index", default=1, minimum=1, maximum=64)
        normalized_mode = str(mode or "create").strip().lower()
        if normalized_mode not in ("create", "append", "replace"):
            raise ValueError("mode must be one of create, append, replace")
        normalized_curve_type = str(curve_type or "line").strip().lower()
        if normalized_curve_type not in _SEGMENT_TYPES:
            raise ValueError("curve_type must be one of {}".format(", ".join(_SEGMENT_TYPES)))
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()
    node = None
    created_node = False
    previous_knot_count = 0
    stage = "resolve_target"

    try:
        if normalized_mode == "create":
            if normalized_node_name or handle is not None:
                return _validation_error("node_name and handle are only valid for append or replace")
            stage = "create_shape"
            factory = getattr(rt, "SplineShape", None)
            if not callable(factory):
                return curve_error("3ds Max does not expose the SplineShape constructor")
            node = factory()
            created_node = True
            add_new_spline = getattr(rt, "addNewSpline", None)
            if not callable(add_new_spline):
                raise RuntimeError("3ds Max does not expose addNewSpline")
            add_new_spline(node)
            target_spline = 1
        else:
            from dcc_mcp_3dsmax._curve_utils import resolve_shape

            node, error = resolve_shape(rt, node_name=normalized_node_name, handle=handle)
            if error:
                return error
            from dcc_mcp_3dsmax._curve_utils import spline_count

            count, count_error = spline_count(rt, node)
            if count_error:
                return curve_error(count_error)
            if normalized_mode == "append" and not 1 <= normalized_index <= count + 1:
                return curve_error(
                    "spline_index {} is out of range".format(normalized_index),
                    spline_count=count,
                )
            if normalized_mode == "replace" and not 1 <= normalized_index <= max(count, 1):
                return curve_error(
                    "spline_index {} is out of range".format(normalized_index),
                    spline_count=count,
                )
            target_spline = normalized_index

            if normalized_mode == "append" and normalized_index > count:
                add_new_spline = getattr(rt, "addNewSpline", None)
                if not callable(add_new_spline):
                    return curve_error("3ds Max does not expose addNewSpline")
                add_new_spline(node)
            elif normalized_mode == "append":
                from dcc_mcp_3dsmax._curve_utils import knot_count

                existing, knot_error = knot_count(rt, node, target_spline)
                if knot_error:
                    return curve_error(knot_error)
                previous_knot_count = existing
            elif normalized_mode == "replace":
                delete_spline = getattr(rt, "deleteSpline", None)
                if not callable(delete_spline):
                    return curve_error("3ds Max does not expose deleteSpline")
                delete_spline(node, target_spline)
                add_new_spline = getattr(rt, "addNewSpline", None)
                if not callable(add_new_spline):
                    return curve_error("3ds Max does not expose addNewSpline")
                add_new_spline(node)
                # deleteSpline shifts every higher index down and addNewSpline
                # appends at the end, so the rebuilt spline lands at index
                # `count` - writing to the original index would scribble over a
                # spline the caller never asked to change.
                target_spline = count

        stage = "map_world_points"
        local_points: List[Any] = []
        for index, point in enumerate(normalized_points):
            local, error = world_to_object(rt, node, point)
            if error:
                return curve_error("point[{}] could not be placed: {}".format(index, error))
            local_points.append(local)

        stage = "write_knots"
        from dcc_mcp_3dsmax._curve_utils import append_knots

        error = append_knots(
            rt,
            node,
            target_spline,
            local_points,
            normalized_knot_type,
            curve_type=normalized_curve_type,
        )
        if error:
            return curve_error(error)

        if normalized_closed:
            closer = getattr(rt, "closeSpline", None)
            if not callable(closer):
                return curve_error("3ds Max does not expose closeSpline")
            closer(node, target_spline)

        stage = "commit_shape"
        error = update_shape(rt, node)
        if error:
            return curve_error(error)

        if normalized_name is not None:
            node.name = normalized_name

        stage = "readback_spline"
        state, error = read_spline_state(rt, node)
        if error:
            return curve_error(error)
        spline = next((item for item in state["splines"] if item["index"] == target_spline), None)
        if spline is None:
            return curve_error(
                "the shape has no spline at index {} after the write".format(target_spline),
                spline_count=state["spline_count"],
            )

        expected_count = previous_knot_count + len(normalized_points)

        mismatches: List[Dict[str, Any]] = []
        if spline["knot_count"] != expected_count:
            mismatches.append(
                {
                    "field": "knot_count",
                    "expected": expected_count,
                    "actual": spline["knot_count"],
                }
            )
        else:
            for offset, requested in enumerate(normalized_points):
                actual = spline["knots"][previous_knot_count + offset]["position"]
                if not positions_match(requested, actual):
                    mismatches.append(
                        {
                            "field": "knots[{}].position".format(offset),
                            "expected": requested,
                            "actual": actual,
                        }
                    )
        if normalized_closed and spline["closed"] is not None and spline["closed"] is not True:
            mismatches.append({"field": "closed", "expected": True, "actual": spline["closed"]})

        if mismatches:
            rolled_back = delete_node(rt, node) if created_node else False
            return curve_error(
                "the spline did not pass world-space readback",
                failure_stage="readback_spline",
                node=node_identity(node),
                mismatches=mismatches,
                rolled_back=rolled_back,
            )

        return curve_success(
            "Drew spline: {}".format(str(node.name)),
            node=node_identity(node),
            spline_index=target_spline,
            mode=normalized_mode,
            closed=bool(spline["closed"]) if spline["closed"] is not None else normalized_closed,
            knot_count=spline["knot_count"],
            knots=[{"index": knot["index"], "position": knot["position"]} for knot in spline["knots"]],
            spline_count=state["spline_count"],
        )
    except Exception as exc:  # noqa: BLE001 - host failures return a typed, rolled-back envelope.
        rolled_back = delete_node(rt, node) if (created_node and node is not None) else False
        return curve_error(
            "draw_spline failed during {}".format(stage),
            failure_stage=stage,
            exception_type=type(exc).__name__,
            exception=str(exc),
            rolled_back=rolled_back,
        )
