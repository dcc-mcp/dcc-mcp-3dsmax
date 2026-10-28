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


def _capture_splines(rt: Any, node: Any) -> Optional[Dict[str, Any]]:
    """Snapshot the shape (splines + name) so a failed append/replace can be rolled back.

    The node name is part of the reversible state: this call may rename the node
    before readback, so a failed write must also put the original name back.
    """
    state, error = read_spline_state(rt, node)
    if error:
        return None
    return {"splines": state["splines"], "name": str(getattr(node, "name", ""))}


def _spline_states_match(captured: Sequence[Dict[str, Any]], actual: Sequence[Dict[str, Any]]) -> bool:
    """Compare two ``read_spline_state`` spline lists by every writable field.

    Positions and both Bezier handle vectors are compared with the same
    tolerance used for world-space readback. Ignoring the handles would let a
    rollback that changed a curve's in/out tangents report ``rolled_back=True``
    while the shape was no longer the one the caller had.
    """
    if len(captured) != len(actual):
        return False
    for before, after in zip(captured, actual):
        before_closed, after_closed = before.get("closed"), after.get("closed")
        if (
            before_closed is not None
            and after_closed is not None
            and bool(before_closed) != bool(after_closed)
        ):
            return False
        before_knots = before.get("knots") or []
        after_knots = after.get("knots") or []
        if len(before_knots) != len(after_knots):
            return False
        for a, b in zip(before_knots, after_knots):
            if not positions_match(a.get("position") or [0, 0, 0], b.get("position") or [0, 0, 0]):
                return False
            a_type, b_type = (a.get("knot_type") or "").lower(), (b.get("knot_type") or "").lower()
            if a_type and b_type and a_type != b_type:
                return False
            # Bezier in/out handles are writable spline state. A rollback that
            # rebuilds a curved spline must reproduce them, and a readback that
            # silently changed them must fail the verification instead of
            # claiming the scene is untouched.
            for field in ("in_vec", "out_vec"):
                a_vec = a.get(field) or [0.0, 0.0, 0.0]
                b_vec = b.get(field) or [0.0, 0.0, 0.0]
                if not positions_match(a_vec, b_vec):
                    return False
    return True


def _spline_curve_type(spline: Dict[str, Any]) -> str:
    """Return the native segment type that preserves a captured spline's shape.

    A corner-only spline is a straight polyline; any bezier/smooth knot means
    curved segments, which must be rebuilt as ``curve`` rather than ``line``.
    The in/out tangents themselves are rewritten by ``set_knot_value``.
    """
    for knot in spline.get("knots") or []:
        if (knot.get("knot_type") or "").lower() in ("bezier", "beziercorner", "smooth"):
            return "curve"
    return "line"


def _restore_splines(rt: Any, node: Any, splines: Sequence[Dict[str, Any]]) -> bool:
    """Rebuild a captured spline state with native calls and verify it.

    Returns True only when the whole shape (knots, handles, types and closed
    flags) reads back equal to ``splines``. Any host failure returns False, so
    the caller reports an unverified rollback rather than an untouched scene.
    """
    from dcc_mcp_3dsmax._curve_utils import append_knots, set_knot_value, spline_count

    delete_spline = getattr(rt, "deleteSpline", None)
    add_new_spline = getattr(rt, "addNewSpline", None)
    make_point = getattr(rt, "Point3", None)
    if not callable(delete_spline) or not callable(add_new_spline) or not callable(make_point):
        return False
    try:
        current, error = spline_count(rt, node)
        if error or current is None:
            return False
        for _ in range(int(current)):
            delete_spline(node, 1)
        for spline in splines:
            add_new_spline(node)
            count, count_error = spline_count(rt, node)
            if count_error or count is None:
                return False
            index = int(count)
            knots = spline.get("knots") or []
            # Rebuild the spline with its original segment type: rebuilding a
            # curved spline as straight ``line`` segments would degrade it even
            # when the positions, types and handles all read back correctly.
            curve_type = _spline_curve_type(spline)
            # Create the knots first: the position/handle writers need them to exist.
            for knot in knots:
                local = knot.get("local_position")
                if local is None:
                    return False
                if append_knots(
                    rt, node, index, [make_point(*local)],
                    knot.get("knot_type") or "corner", curve_type=curve_type,
                ):
                    return False
            for knot in knots:
                if set_knot_value(
                    rt, node, index, int(knot["index"]),
                    position=make_point(*knot["local_position"]),
                    in_vec=make_point(*(knot.get("in_vec") or [0.0, 0.0, 0.0])),
                    out_vec=make_point(*(knot.get("out_vec") or [0.0, 0.0, 0.0])),
                    knot_type=knot.get("knot_type"),
                ):
                    return False
            closed = spline.get("closed")
            if closed is not None:
                toggle = getattr(rt, "close" if closed else "open", None)
                if not callable(toggle):
                    return False
                toggle(node, index)
            if update_shape(rt, node):
                return False
    except Exception:  # noqa: BLE001 - an unverifiable rollback must fail closed.
        return False

    after, error = read_spline_state(rt, node)
    if error:
        return False
    return _spline_states_match(splines, after["splines"])


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
    existing_splines: Optional[Dict[str, Any]] = None
    stage = "resolve_target"

    def _fail(message: str, **details: Any) -> Dict[str, Any]:
        """Roll back whatever this call mutated and report the verified result."""
        if created_node and node is not None:
            rolled_back = delete_node(rt, node)
        elif existing_splines is not None and node is not None:
            rolled_back = _restore_splines(rt, node, existing_splines["splines"])
            # A rename applied before readback must not survive a failed write.
            # Only claim a verified rollback when the original name is back too.
            original_name = existing_splines.get("name")
            if original_name is not None:
                try:
                    if str(getattr(node, "name", "")) != original_name:
                        node.name = original_name
                    name_restored = str(getattr(node, "name", "")) == original_name
                except Exception:  # noqa: BLE001 - an unverifiable rename revert fails closed.
                    name_restored = False
                rolled_back = rolled_back and name_restored
        else:
            rolled_back = False
        details.setdefault("node", node_identity(node) if node is not None else None)
        details["rolled_back"] = rolled_back
        return curve_error(message, **details)

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
            # Capture the whole shape before any mutation. Every failure below
            # (mapping, knot write, close, commit, readback) must be able to put
            # an existing shape back exactly as it was.
            existing_splines = _capture_splines(rt, node)

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
                    return _fail("3ds Max does not expose addNewSpline")
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
                return _fail("point[{}] could not be placed: {}".format(index, error))
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
            return _fail(error)

        if normalized_closed:
            closer = getattr(rt, "close", None)
            if not callable(closer):
                return _fail("3ds Max does not expose close")
            closer(node, target_spline)

        stage = "commit_shape"
        error = update_shape(rt, node)
        if error:
            return _fail(error)

        if normalized_name is not None:
            node.name = normalized_name

        stage = "readback_spline"
        state, error = read_spline_state(rt, node)
        if error:
            return _fail(error)
        spline = next((item for item in state["splines"] if item["index"] == target_spline), None)
        if spline is None:
            return _fail(
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
            return _fail(
                "the spline did not pass world-space readback",
                failure_stage="readback_spline",
                node=node_identity(node),
                mismatches=mismatches,
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
        return _fail(
            "draw_spline failed during {}".format(stage),
            failure_stage=stage,
            exception_type=type(exc).__name__,
            exception=str(exc),
        )
