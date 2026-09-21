"""Edit spline knots and handles under stale-token protection."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._curve_utils import (
    curve_error,
    curve_success,
    curve_token,
    knot_count,
    positions_match,
    read_knot,
    read_spline_state,
    resolve_shape,
    set_knot_value,
    update_shape,
    validated_bool,
    validated_int,
    validated_knot_type,
    validated_vectors,
    world_to_object,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _snapshot(runtime: Any, node: Any, spline_index: int, knot_total: int) -> List[Dict[str, Any]]:
    """Capture the local knot state so a failed edit can be restored."""
    snapshot: List[Dict[str, Any]] = []
    for knot_index in range(1, knot_total + 1):
        knot, error = read_knot(runtime, node, spline_index, knot_index)
        if error:
            break
        snapshot.append(knot)
    return snapshot


def _restore(runtime: Any, node: Any, spline_index: int, snapshot: Sequence[Dict[str, Any]]) -> bool:
    """Write a captured knot state back onto the spline."""
    restored = True
    for knot in snapshot:
        make_point = getattr(runtime, "Point3", None)
        if not callable(make_point):
            return False
        error = set_knot_value(
            runtime,
            node,
            spline_index,
            int(knot["index"]),
            position=make_point(*[float(component) for component in knot["local_position"]]),
            in_vec=make_point(*[float(component) for component in knot["in_vec"]]),
            out_vec=make_point(*[float(component) for component in knot["out_vec"]]),
        )
        if error:
            restored = False
    update_shape(runtime, node)
    return restored


@with_max
def main(
    token: str = "",
    knots: Optional[Sequence[Any]] = None,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    spline_index: int = 1,
    closed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply knot edits and verify every requested field by reading it back.

    ``token`` must be the value ``inspect_curve`` returned for the current
    geometry. A mismatched token fails before anything is written, so an edit
    can never land on a spline the caller has not just read.
    """
    try:
        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise ValueError("token is required: call inspect_curve first")
        normalized_index = validated_int(spline_index, "spline_index", default=1, minimum=1, maximum=64)

        if isinstance(knots, (str, bytes)) or knots is None or not isinstance(knots, Sequence):
            raise ValueError("knots must be an array of knot edits")
        if not 1 <= len(knots) <= 256:
            raise ValueError("knots must contain between 1 and 256 entries")

        edits: List[Dict[str, Any]] = []
        for position, entry in enumerate(knots):
            label = "knots[{}]".format(position)
            if not isinstance(entry, dict):
                raise ValueError("{} must be an object".format(label))
            index = entry.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("{}.index must be an integer".format(label))
            if index < 1:
                raise ValueError("{}.index must be at least 1".format(label))
            edit: Dict[str, Any] = {"index": index}
            if "position" in entry and entry["position"] is not None:
                edit["position"] = validated_vectors([entry["position"]], label + ".position", min_items=1, max_items=1, item_length=3)[0]
            if "in_vec" in entry and entry["in_vec"] is not None:
                edit["in_vec"] = validated_vectors([entry["in_vec"]], label + ".in_vec", min_items=1, max_items=1, item_length=3)[0]
            if "out_vec" in entry and entry["out_vec"] is not None:
                edit["out_vec"] = validated_vectors([entry["out_vec"]], label + ".out_vec", min_items=1, max_items=1, item_length=3)[0]
            if "knot_type" in entry and entry["knot_type"] is not None:
                edit["knot_type"] = validated_knot_type(entry["knot_type"], label + ".knot_type")
            if not any(key in edit for key in ("position", "in_vec", "out_vec", "knot_type")):
                raise ValueError("{} must change at least one of position, in_vec, out_vec, knot_type".format(label))
            edits.append(edit)

        normalized_closed = validated_bool(closed, "closed") if closed is not None else None
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()
    node, error = resolve_shape(rt, node_name=node_name, handle=handle)
    if error:
        return error

    before, error = read_spline_state(rt, node)
    if error:
        return curve_error(error)

    current_token = curve_token(node, before)
    if current_token != normalized_token:
        return curve_error(
            "the curve changed since the token was issued; call inspect_curve again",
            failure_stage="stale_token",
            node=before["node"],
            expected_token=normalized_token,
            current_token=current_token,
        )

    total, error = knot_count(rt, node, normalized_index)
    if error:
        return curve_error(error)
    if total < 1:
        return curve_error("spline {} has no knots to edit".format(normalized_index))

    out_of_range = [edit["index"] for edit in edits if edit["index"] > total]
    if out_of_range:
        return curve_error(
            "knot index out of range",
            spline_index=normalized_index,
            knot_count=total,
            requested_indices=sorted(set(out_of_range)),
        )

    snapshot = _snapshot(rt, node, normalized_index, total)
    if len(snapshot) != total:
        return curve_error(
            "the spline state could not be captured, so the edit is not reversible",
            spline_index=normalized_index,
            knot_count=total,
            captured=len(snapshot),
        )

    try:
        for edit in edits:
            make_point = getattr(rt, "Point3", None)
            if not callable(make_point):
                raise RuntimeError("the host does not expose Point3")
            local_position = None
            if "position" in edit:
                local_position, map_error = world_to_object(rt, node, edit["position"])
                if map_error:
                    raise RuntimeError("knots[{}] could not be placed: {}".format(edit["index"], map_error))
            error = set_knot_value(
                rt,
                node,
                normalized_index,
                edit["index"],
                position=local_position,
                in_vec=make_point(*edit["in_vec"]) if "in_vec" in edit else None,
                out_vec=make_point(*edit["out_vec"]) if "out_vec" in edit else None,
                knot_type=edit.get("knot_type"),
            )
            if error:
                raise RuntimeError(error)

        if normalized_closed is not None:
            if normalized_closed:
                closer = getattr(rt, "closeSpline", None)
                if not callable(closer):
                    raise RuntimeError("3ds Max does not expose closeSpline")
                closer(node, normalized_index)
            else:
                opener = getattr(rt, "openSpline", None)
                if not callable(opener):
                    raise RuntimeError("3ds Max does not expose openSpline")
                opener(node, normalized_index)

        error = update_shape(rt, node)
        if error:
            raise RuntimeError(error)
    except Exception as exc:  # noqa: BLE001 - host failures restore the captured state.
        restored = _restore(rt, node, normalized_index, snapshot)
        return curve_error(
            "edit_curve failed before the scene was changed",
            failure_stage="apply_edits",
            exception_type=type(exc).__name__,
            exception=str(exc),
            restored=restored,
        )

    after, error = read_spline_state(rt, node)
    if error:
        return curve_error(error)

    spline_after = next((item for item in after["splines"] if item["index"] == normalized_index), None)
    if spline_after is None:
        return curve_error("spline {} disappeared during the edit".format(normalized_index))

    mismatches: List[Dict[str, Any]] = []
    for edit in edits:
        knot = next((item for item in spline_after["knots"] if item["index"] == edit["index"]), None)
        if knot is None:
            mismatches.append({"index": edit["index"], "field": "knot", "expected": "present", "actual": "missing"})
            continue
        if "position" in edit and not positions_match(edit["position"], knot["position"]):
            mismatches.append(
                {
                    "index": edit["index"],
                    "field": "position",
                    "expected": edit["position"],
                    "actual": knot["position"],
                }
            )
        if "in_vec" in edit and not positions_match(edit["in_vec"], knot["in_vec"]):
            mismatches.append(
                {"index": edit["index"], "field": "in_vec", "expected": edit["in_vec"], "actual": knot["in_vec"]}
            )
        if "out_vec" in edit and not positions_match(edit["out_vec"], knot["out_vec"]):
            mismatches.append(
                {"index": edit["index"], "field": "out_vec", "expected": edit["out_vec"], "actual": knot["out_vec"]}
            )
        if "knot_type" in edit:
            actual_type = (knot.get("knot_type") or "").lower()
            if actual_type and actual_type != edit["knot_type"].lower():
                mismatches.append(
                    {
                        "index": edit["index"],
                        "field": "knot_type",
                        "expected": edit["knot_type"],
                        "actual": knot.get("knot_type"),
                    }
                )
    if normalized_closed is not None and spline_after["closed"] is not None:
        if bool(spline_after["closed"]) is not normalized_closed:
            mismatches.append(
                {"field": "closed", "expected": normalized_closed, "actual": spline_after["closed"]}
            )

    if mismatches:
        restored = _restore(rt, node, normalized_index, snapshot)
        return curve_error(
            "the edited knots did not pass world-space readback",
            failure_stage="readback_edit",
            node=after["node"],
            mismatches=mismatches,
            restored=restored,
        )

    return curve_success(
        "Edited curve: {}".format(str(getattr(node, "name", ""))),
        node=after["node"],
        spline_index=normalized_index,
        edited_knot_count=len(edits),
        knot_count=spline_after["knot_count"],
        closed=spline_after["closed"],
        token=curve_token(node, after),
        knots=spline_after["knots"],
    )
