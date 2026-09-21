"""Read, move, set, and align Editable Poly vertices in world coordinates.

``edit_vertices`` is the narrow tool for the most common component edit: "these
vertices should be there". Everything is expressed in world space, because an
agent reasons about a scene in world coordinates, and the object-space mapping
happens through the node transform on the way in and out.

Four actions:

* ``read`` - report world and object positions. Read-only.
* ``move`` - offset every selected vertex by a world-space delta.
* ``set``  - place every selected vertex at an explicit world position.
* ``align`` - flatten the selected vertices onto one value along the chosen
  axes (their mean, minimum, or maximum), which is how "make this edge straight"
  or "sit these feet on the floor" is expressed.

Every write is verified per vertex by reading it back and comparing within a
32-bit-float tolerance. A vertex the host kept where it was fails the call and
is named in ``errors``; it is never reported as a warning on a success.

The batch runs without an undo hold, so the host may leave one undo entry per
vertex or one for the whole call, and the adapter cannot query which - which is
why the tool declares ``batch_call`` undo granularity. Use ``mesh_edit`` when a
multi-edit batch has to collapse into a single undo step.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._curve_utils import (
    positions_match,
    resolve_shape,
    validated_name,
    validated_vectors,
)
from dcc_mcp_3dsmax._poly_utils import (
    MAX_INDICES_PER_OP,
    MAX_VERTICES,
    POSITION_TOLERANCE,
    component_identity,
    ensure_poly,
    make_point,
    mesh_error,
    mesh_success,
    num_verts,
    point_components,
    read_vert,
    set_vert,
    to_object_space,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

ACTIONS = ("read", "move", "set", "align")
SPACES = ("world", "object")
ALIGN_AXES = ("x", "y", "z", "xy", "xz", "yz", "xyz")
ALIGN_MODES = ("mean", "min", "max")

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _validate_indices(value: Any) -> Optional[List[int]]:
    """Validate a list of 1-based vertex indices."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("vertex_indices must be an array of 1-based vertex indices")
    if not 1 <= len(value) <= MAX_INDICES_PER_OP:
        raise ValueError(
            "vertex_indices must contain between 1 and {} entries".format(MAX_INDICES_PER_OP)
        )
    for position, index in enumerate(value):
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            raise ValueError("vertex_indices[{}] must be a positive integer".format(position))
    return [int(index) for index in value]


def _axis_indices(axis: str) -> List[int]:
    return [_AXIS_INDEX[character] for character in axis]


def _align_value(values: Sequence[float], mode: str) -> float:
    if mode == "min":
        return min(values)
    if mode == "max":
        return max(values)
    return sum(values) / len(values)


def _target_object_position(
    rt: Any,
    node: Any,
    *,
    action: str,
    space: str,
    before: Dict[str, Any],
    offset: Optional[Sequence[float]],
    position: Optional[Sequence[float]],
    align_targets: Dict[int, float],
) -> Tuple[Optional[List[float]], Optional[str]]:
    """Return the object-space position one vertex should hold after the write.

    Returns ``(position, error)``. This is the only place a world-space request
    becomes an object-space write, so a host that cannot invert the node
    transform fails the call rather than having world coordinates written into
    an object-space slot.
    """
    if action == "align":
        target = list(before["object"])
        for axis_index, value in align_targets.items():
            target[axis_index] = value
        return target, None

    if action == "move":
        if space == "object":
            return [before["object"][axis] + float(offset[axis]) for axis in range(3)], None
        world = [before["world"][axis] + float(offset[axis]) for axis in range(3)]
    else:
        # action == "set"
        if space == "object":
            return point_components(position), None
        world = list(position)

    local, error = to_object_space(rt, node, world)
    if error:
        return None, error
    return point_components(local), None


@with_max
def main(
    action: str = "read",
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    vertex_indices: Optional[Sequence[int]] = None,
    offset: Optional[Sequence[float]] = None,
    positions: Optional[Sequence[Sequence[float]]] = None,
    axis: Optional[str] = None,
    mode: Optional[str] = None,
    space: Optional[str] = None,
) -> Dict[str, Any]:
    """Read or move Editable Poly vertices in world coordinates."""
    try:
        normalized_action = str(action or "read").strip().lower()
        if normalized_action not in ACTIONS:
            raise ValueError("action must be one of {}".format(", ".join(ACTIONS)))
        normalized_space = str(space or "world").strip().lower()
        if normalized_space not in SPACES:
            raise ValueError("space must be one of {}".format(", ".join(SPACES)))
        normalized_name = validated_name(node_name, "node_name")
        if normalized_name is None and handle is None:
            raise ValueError("node_name or handle is required")
        indices = _validate_indices(vertex_indices)

        normalized_axis = "xyz"
        normalized_mode = "mean"
        if normalized_action == "align":
            normalized_axis = str(axis or "xyz").strip().lower()
            if normalized_axis not in ALIGN_AXES:
                raise ValueError("axis must be one of {}".format(", ".join(ALIGN_AXES)))
            normalized_mode = str(mode or "mean").strip().lower()
            if normalized_mode not in ALIGN_MODES:
                raise ValueError("mode must be one of {}".format(", ".join(ALIGN_MODES)))

        if normalized_action == "move" and offset is None:
            raise ValueError("offset is required for move")
        if normalized_action != "move" and offset is not None:
            raise ValueError("offset is only accepted for move")
        delta: Optional[List[float]] = None
        if normalized_action == "move":
            delta = validated_vectors([offset], "offset", min_items=1, max_items=1, item_length=3)[0]

        if normalized_action == "set" and positions is None:
            raise ValueError("positions is required for set")
        if normalized_action != "set" and positions is not None:
            raise ValueError("positions is only accepted for set")
        target_world: Optional[List[List[float]]] = None
        if normalized_action == "set":
            target_world = validated_vectors(
                positions, "positions", min_items=1, max_items=MAX_VERTICES, item_length=3
            )
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()
    node, error = resolve_shape(rt, node_name=normalized_name, handle=handle)
    if error:
        return error
    poly_error_message = ensure_poly(rt, node)
    if poly_error_message:
        return mesh_error(poly_error_message)

    total, count_error = num_verts(rt, node)
    if count_error:
        return mesh_error(count_error, node=component_identity(node))

    resolved = list(indices) if indices is not None else list(range(1, (total or 0) + 1))
    for index in resolved:
        if index > (total or 0):
            return _validation_error(
                "vertex {} is out of range: the node has {} vertice(s)".format(index, total)
            )
    if normalized_action == "set" and len(target_world or []) != len(resolved):
        return _validation_error(
            "positions has {} entr(ies) but {} vertex/vertices were selected".format(
                len(target_world or []), len(resolved)
            )
        )
    if not resolved:
        return _validation_error("the node has no vertices to {}".format(normalized_action))

    if normalized_action == "read":
        rows: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for index in resolved:
            vertex, vertex_error = read_vert(rt, node, index)
            if vertex_error:
                warnings.append(vertex_error)
                continue
            rows.append(vertex)
        payload: Dict[str, Any] = {
            "node": component_identity(node),
            "action": "read",
            "space": normalized_space,
            "vertices": rows,
            "vertex_count": len(rows),
            # Reported alongside the returned count so a caller can see that a
            # vertex was requested but could not be read, instead of having to
            # notice that `vertex_count` is smaller than the list it passed in.
            "requested_count": len(resolved),
        }
        if warnings:
            payload["warnings"] = warnings
            payload["message_note"] = (
                "{} of {} requested vertice(s) could not be read; "
                "vertex_count is what was actually returned".format(len(warnings), len(resolved))
            )
        return mesh_success("Read {} vertice(s)".format(len(rows)), **payload)

    # ── writes ────────────────────────────────────────────────────────
    before_rows: List[Dict[str, Any]] = []
    for index in resolved:
        vertex, vertex_error = read_vert(rt, node, index)
        if vertex_error:
            return mesh_error(vertex_error, node=component_identity(node))
        before_rows.append(vertex)

    align_targets: Dict[int, float] = {}
    if normalized_action == "align":
        for axis_index in _axis_indices(normalized_axis):
            values = [row["object"][axis_index] for row in before_rows]
            align_targets[axis_index] = _align_value(values, normalized_mode)

    write_errors: List[Dict[str, Any]] = []
    planned: List[List[float]] = []
    for slot, index in enumerate(resolved):
        target, map_error = _target_object_position(
            rt,
            node,
            action=normalized_action,
            space=normalized_space,
            before=before_rows[slot],
            offset=delta,
            position=None if target_world is None else target_world[slot],
            align_targets=align_targets,
        )
        if map_error:
            write_errors.append({"index": index, "error": map_error})
            continue
        point, point_error = make_point(rt, target)
        if point_error:
            write_errors.append({"index": index, "error": point_error})
            continue
        write_error = set_vert(rt, node, index, point)
        if write_error:
            write_errors.append({"index": index, "error": write_error})
            continue
        planned.append(target)

    if write_errors:
        return mesh_error(
            "{} of {} vertex write(s) were not accepted".format(len(write_errors), len(resolved)),
            node=component_identity(node),
            action=normalized_action,
            errors=write_errors[:20],
            error_count=len(write_errors),
            note=(
                "Any write before the failure was already applied and was not rolled back; "
                "use undo_last and re-read the vertices, repeating while they still differ."
            ),
        )

    after_rows: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    for slot, index in enumerate(resolved):
        vertex, vertex_error = read_vert(rt, node, index)
        if vertex_error:
            mismatches.append({"index": index, "error": vertex_error})
            continue
        after_rows.append(vertex)
        if not positions_match(planned[slot], vertex["object"], tolerance=POSITION_TOLERANCE):
            mismatches.append(
                {"index": index, "requested": planned[slot], "readback": vertex["object"]}
            )

    if mismatches:
        return mesh_error(
            "{} of {} vertex/vertices did not end up where they were placed".format(
                len(mismatches), len(resolved)
            ),
            node=component_identity(node),
            action=normalized_action,
            mismatches=mismatches[:20],
            mismatch_count=len(mismatches),
        )

    data: Dict[str, Any] = {
        "node": component_identity(node),
        "action": normalized_action,
        "space": normalized_space,
        "vertex_count": len(resolved),
        "vertices": [
            {
                "index": resolved[slot],
                "before": before_rows[slot]["world"],
                "after": after_rows[slot]["world"],
            }
            for slot in range(len(resolved))
        ],
    }
    if normalized_action == "align":
        data["axis"] = normalized_axis
        data["mode"] = normalized_mode
    return mesh_success(
        "{} {} vertice(s) on {}".format(
            normalized_action.capitalize(), len(resolved), str(getattr(node, "name", "<node>"))
        ),
        **data
    )
