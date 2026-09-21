"""Read the world-space knots and handles of an editable spline."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._curve_utils import (
    curve_error,
    curve_success,
    curve_token,
    read_spline_state,
    resolve_shape,
    validated_int,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    spline_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Report spline knots in world space plus a token for ``edit_curve``.

    The token is a digest of the geometry this call read. Passing it to
    ``edit_curve`` proves the caller is editing the spline it inspected: any
    change in between - from another tool, from the viewport, or from the host
    - changes the digest and fails the pending edit instead of applying it.
    """
    try:
        normalized_index = validated_int(spline_index, "spline_index", default=0, minimum=1, maximum=64)
    except ValueError as exc:
        return {"success": False, "status": "error", "message": str(exc), "data": {}}
    if normalized_index == 0:
        normalized_index = None

    rt = get_runtime()
    node, error = resolve_shape(rt, node_name=node_name, handle=handle)
    if error:
        return error

    state, error = read_spline_state(rt, node)
    if error:
        return curve_error(error)

    splines = state["splines"]
    if normalized_index is not None:
        splines = [item for item in splines if item["index"] == normalized_index]
        if not splines:
            return curve_error(
                "the shape has no spline at index {}".format(normalized_index),
                spline_count=state["spline_count"],
            )

    return curve_success(
        "Inspected curve: {}".format(str(getattr(node, "name", ""))),
        node=state["node"],
        spline_count=state["spline_count"],
        splines=splines,
        token=curve_token(node, state),
        token_usage="Pass this token to edit_curve; it is rejected once the geometry changes.",
    )
