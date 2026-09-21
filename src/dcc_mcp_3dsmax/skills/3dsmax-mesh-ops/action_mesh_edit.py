"""Apply a batch of Editable Poly component edits as one atomic, reversible step.

``mesh_edit`` exists for the edit pattern no single-purpose tool covers: an
agent that has computed a set of vertex moves, face deletions, and shading
assignments and wants them applied as one operation that can be undone with one
``undo_last`` call.

Three properties make that safe:

**Preflight.** Every op is resolved and range-checked against the live node
*before* the first write. A bad component index, a value that does not parse, or
an op whose arguments do not line up fails the whole call and nothing is
written, so a partial application can never be reported as a success.

**Single undo step.** The writes run inside :func:`_undo_utils.undo_step`, the
adapter-side equivalent of the MAXScript ``undo "label" ( ...)`` wrapper. One
call therefore leaves one host undo entry, and a failure part-way through
cancels the hold so the host rolls the whole batch back.

**Honest rollback.** The hold is a capability, not a given. When the host cannot
open one, the batch is refused unless ``allow_ungrouped`` explicitly accepts
per-op undo entries - and the result then says the batch was *not* grouped and
that the applied ops could not be rolled back, rather than reporting a clean
``rolled_back`` over a partially edited mesh.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax import _undo_utils
from dcc_mcp_3dsmax._curve_utils import (
    resolve_shape,
    validated_name,
    validated_vectors,
)
from dcc_mcp_3dsmax._poly_utils import (
    MAX_INDICES_PER_OP,
    MAX_OPS,
    MAX_VERTICES,
    component_counts,
    component_identity,
    delete_edges,
    delete_faces,
    delete_verts,
    detach_faces_to_node,
    ensure_poly,
    make_point,
    mesh_error,
    num_verts,
    point_components,
    read_vert,
    set_face_mat_id,
    set_face_smoothing_group,
    set_vert,
    to_object_space,
    weld_verts,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

# Ops that move or reshape geometry.
OP_MOVE_VERTICES = "move_vertices"
OP_SET_VERTICES = "set_vertices"
OP_WELD_VERTICES = "weld_vertices"
OP_ALIGN_VERTICES = "align_vertices"
OP_DELETE_VERTICES = "delete_vertices"
OP_DELETE_EDGES = "delete_edges"
OP_DELETE_FACES = "delete_faces"
OP_DETACH_FACES = "detach_faces"
OP_SET_FACE_MATERIAL_ID = "set_face_material_id"
OP_SET_FACE_SMOOTHING_GROUP = "set_face_smoothing_group"

OPERATIONS = (
    OP_MOVE_VERTICES,
    OP_SET_VERTICES,
    OP_WELD_VERTICES,
    OP_ALIGN_VERTICES,
    OP_DELETE_VERTICES,
    OP_DELETE_EDGES,
    OP_DELETE_FACES,
    OP_DETACH_FACES,
    OP_SET_FACE_MATERIAL_ID,
    OP_SET_FACE_SMOOTHING_GROUP,
)

# Which component kind each op indexes, so preflight can range-check indices
# against the right live count.
OP_COMPONENT = {
    OP_MOVE_VERTICES: "vertices",
    OP_SET_VERTICES: "vertices",
    OP_WELD_VERTICES: "vertices",
    OP_ALIGN_VERTICES: "vertices",
    OP_DELETE_VERTICES: "vertices",
    OP_DELETE_EDGES: "edges",
    OP_DELETE_FACES: "faces",
    OP_DETACH_FACES: "faces",
    OP_SET_FACE_MATERIAL_ID: "faces",
    OP_SET_FACE_SMOOTHING_GROUP: "faces",
}

# Ops that remove data. Their presence is why the tool is destructive.
DESTRUCTIVE_OPS = frozenset(
    {OP_DELETE_VERTICES, OP_DELETE_EDGES, OP_DELETE_FACES, OP_DETACH_FACES, OP_WELD_VERTICES}
)

ALIGN_AXES = ("x", "y", "z", "xy", "xz", "yz", "xyz")
ALIGN_MODES = ("mean", "min", "max")
SPACES = ("world", "object")

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

# Bounds on a material ID and a smoothing group. 3ds Max allows 32 smoothing
# groups (a bitmask) and material IDs up to 65535 on an Editable Poly.
MAX_MATERIAL_ID = 65535
MAX_SMOOTHING_GROUP = 32


class _RequestError(ValueError):
    """A malformed request: raised during normalization, before any host call."""


class _EditFailure(Exception):
    """A host write that failed or did not verify; aborts and rolls back."""

    def __init__(self, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data or {}


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


# ── normalization ──────────────────────────────────────────────────────


def _int_field(edit: Dict[str, Any], key: str, *, minimum: int, maximum: int) -> Optional[int]:
    value = edit.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _RequestError("{} must be an integer between {} and {}".format(key, minimum, maximum))
    if not minimum <= value <= maximum:
        raise _RequestError("{} must be between {} and {}".format(key, minimum, maximum))
    return value


def _indices(edit: Dict[str, Any]) -> List[int]:
    value = edit.get("indices")
    if value is None:
        raise _RequestError("indices is required for {}".format(edit["op"]))
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _RequestError("indices must be an array of 1-based component indices")
    if not 1 <= len(value) <= MAX_INDICES_PER_OP:
        raise _RequestError(
            "indices must contain between 1 and {} entries".format(MAX_INDICES_PER_OP)
        )
    for position, index in enumerate(value):
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            raise _RequestError("indices[{}] must be a positive integer".format(position))
    return [int(index) for index in value]


def _normalize_ops(ops: Any) -> List[Dict[str, Any]]:
    """Validate the request shape and return one normalized record per op."""
    if not isinstance(ops, list) or not ops:
        raise _RequestError("ops must be a non-empty array")
    if len(ops) > MAX_OPS:
        raise _RequestError("ops must contain at most {} entries, received {}".format(MAX_OPS, len(ops)))

    normalized: List[Dict[str, Any]] = []
    for position, edit in enumerate(ops):
        if not isinstance(edit, dict):
            raise _RequestError("ops[{}] must be an object".format(position))
        op = str(edit.get("op") or "").strip().lower()
        if op not in OPERATIONS:
            raise _RequestError(
                "ops[{}]: unsupported op {!r}, expected one of {}".format(
                    position, edit.get("op"), ", ".join(OPERATIONS)
                )
            )

        record: Dict[str, Any] = {
            "position": position,
            "op": op,
            "indices": _indices(edit),
            "space": str(edit.get("space") or "world").strip().lower(),
        }
        if record["space"] not in SPACES:
            raise _RequestError("ops[{}]: space must be one of {}".format(position, ", ".join(SPACES)))

        if op in (OP_MOVE_VERTICES, OP_SET_VERTICES):
            if op == OP_MOVE_VERTICES:
                raw = edit.get("offset")
                if raw is None:
                    raise _RequestError("ops[{}]: offset is required for move_vertices".format(position))
                record["offset"] = validated_vectors(
                    [raw], "ops[{}].offset".format(position), min_items=1, max_items=1, item_length=3
                )[0]
            else:
                raw = edit.get("positions")
                if raw is None:
                    raise _RequestError("ops[{}]: positions is required for set_vertices".format(position))
                record["positions"] = validated_vectors(
                    raw,
                    "ops[{}].positions".format(position),
                    min_items=1,
                    max_items=MAX_VERTICES,
                    item_length=3,
                )

        if op == OP_WELD_VERTICES and len(record["indices"]) < 2:
            raise _RequestError("ops[{}]: weld_vertices needs at least two indices".format(position))

        if op == OP_ALIGN_VERTICES:
            axis = str(edit.get("axis") or "xyz").strip().lower()
            if axis not in ALIGN_AXES:
                raise _RequestError("ops[{}]: axis must be one of {}".format(position, ", ".join(ALIGN_AXES)))
            align_mode = str(edit.get("mode") or "mean").strip().lower()
            if align_mode not in ALIGN_MODES:
                raise _RequestError("ops[{}]: mode must be one of {}".format(position, ", ".join(ALIGN_MODES)))
            record["axis"] = axis
            record["mode"] = align_mode

        if op == OP_SET_FACE_MATERIAL_ID:
            record["material_id"] = _int_field(
                edit, "material_id", minimum=1, maximum=MAX_MATERIAL_ID
            )
            if record["material_id"] is None:
                raise _RequestError("ops[{}]: material_id is required".format(position))

        if op == OP_SET_FACE_SMOOTHING_GROUP:
            record["smoothing_group"] = _int_field(
                edit, "smoothing_group", minimum=0, maximum=MAX_SMOOTHING_GROUP
            )
            if record["smoothing_group"] is None:
                raise _RequestError("ops[{}]: smoothing_group is required".format(position))

        if op == OP_DETACH_FACES:
            name = edit.get("name")
            if name is not None:
                name = validated_name(name, "ops[{}].name".format(position))
            record["name"] = name or "DetachedMesh"

        normalized.append(record)
    return normalized


# ── preflight ──────────────────────────────────────────────────────────


# How each op changes the component counts a later op is range-checked
# against. ``None`` means "the host decides, so the count can only be bounded"
# and the entry is the largest number of components that can remain:
# deletions remove at least what was asked for, and may cascade.
_COUNT_EFFECTS = {
    OP_MOVE_VERTICES: {},
    OP_SET_VERTICES: {},
    OP_ALIGN_VERTICES: {},
    OP_WELD_VERTICES: {"vertices": lambda removed: -removed},
    OP_DELETE_VERTICES: {"vertices": lambda removed: -removed, "edges": None, "faces": None},
    OP_DELETE_EDGES: {"edges": lambda removed: -removed, "faces": None},
    # A deleted face takes its edges with it, and which of them survive
    # depends on the neighbours, so the edge count afterwards is unknown rather
    # than merely lower: a later edge op belongs in its own batch.
    OP_DELETE_FACES: {"faces": lambda removed: -removed, "edges": None},
    OP_DETACH_FACES: {"faces": lambda removed: -removed, "edges": None},
    OP_SET_FACE_MATERIAL_ID: {},
    OP_SET_FACE_SMOOTHING_GROUP: {},
}


def _advance_limits(limits: Dict[str, Optional[int]], record: Dict[str, Any]) -> None:
    """Move the tracked counts past one accepted op, in place.

    A count that an op makes unpredictable is set to ``None``, and a later op
    that needs it is then rejected during preflight rather than checked against
    a number that is known to be stale.
    """
    removed = {
        OP_WELD_VERTICES: len(record["indices"]) - 1,
    }.get(record["op"], len(record["indices"]))
    # ``.get``, not a subscript: an op added to OPERATIONS without a count entry
    # must preflight as "no tracked change", not raise a KeyError out of the
    # tool before the hold has even been opened.
    for kind, effect in _COUNT_EFFECTS.get(record["op"], {}).items():
        current = limits.get(kind)
        if effect is None or current is None:
            limits[kind] = None
            continue
        limits[kind] = current + effect(removed)


def _preflight(
    rt: Any, node: Any, ops: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Range-check every op and return ``(plan, errors)``.

    Indices are checked against the counts the node will have *at the point the
    op runs*, not against one snapshot taken before the batch. A batch that
    deletes a face and then re-shades "face 6" of what is now a five-face mesh
    is rejected here instead of failing once the hold is already open.
    """
    counts = component_counts(rt, node)
    limits: Dict[str, Optional[int]] = {
        "vertices": counts.get("vertex_count"),
        "edges": counts.get("edge_count"),
        "faces": counts.get("face_count"),
    }

    plan: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for record in ops:
        kind = OP_COMPONENT[record["op"]]
        limit = limits.get(kind)
        position = record["position"]
        if limit is None:
            if counts.get("errors"):
                reason = "the {} count cannot be read".format(kind)
            else:
                reason = (
                    "an earlier op in this batch deletes {} as a side effect, so the count is not "
                    "known here".format(kind)
                )
            errors.append(
                {
                    "position": position,
                    "op": record["op"],
                    "message": "{}; indices cannot be range-checked, so split the batch".format(reason),
                }
            )
            continue
        out_of_range = [index for index in record["indices"] if index > limit]
        if out_of_range:
            errors.append(
                {
                    "position": position,
                    "op": record["op"],
                    "message": "indices {} are out of range: the node has {} {} at this point in the batch".format(
                        out_of_range[:5], limit, kind
                    ),
                }
            )
            continue
        if record["op"] == OP_SET_VERTICES and len(record["positions"]) != len(record["indices"]):
            errors.append(
                {
                    "position": position,
                    "op": record["op"],
                    "message": "positions has {} entr(ies) but {} ind(ices) were given".format(
                        len(record["positions"]), len(record["indices"])
                    ),
                }
            )
            continue
        _advance_limits(limits, record)
        plan.append(record)
    return plan, errors


# ── apply ──────────────────────────────────────────────────────────────


def _align_value(values: Sequence[float], mode: str) -> float:
    if mode == "min":
        return min(values)
    if mode == "max":
        return max(values)
    return sum(values) / len(values)


def _apply_vertex_position_ops(rt: Any, node: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one ``move_vertices`` / ``set_vertices`` / ``align_vertices`` op."""
    indices = record["indices"]
    before: List[Dict[str, Any]] = []
    for index in indices:
        vertex, error = read_vert(rt, node, index)
        if error:
            raise _EditFailure(error, {"position": record["position"], "index": index})
        before.append(vertex)

    align_targets: Dict[int, float] = {}
    if record["op"] == OP_ALIGN_VERTICES:
        for axis_index in [_AXIS_INDEX[character] for character in record["axis"]]:
            values = [row["object"][axis_index] for row in before]
            align_targets[axis_index] = _align_value(values, record["mode"])

    for slot, index in enumerate(indices):
        if record["op"] == OP_MOVE_VERTICES:
            if record["space"] == "object":
                target = [before[slot]["object"][axis] + record["offset"][axis] for axis in range(3)]
            else:
                world = [before[slot]["world"][axis] + record["offset"][axis] for axis in range(3)]
                target, error = to_object_space(rt, node, world)
                if error:
                    raise _EditFailure(error, {"position": record["position"], "index": index})
                target = point_components(target)
        elif record["op"] == OP_SET_VERTICES:
            if record["space"] == "object":
                target = list(record["positions"][slot])
            else:
                target, error = to_object_space(rt, node, record["positions"][slot])
                if error:
                    raise _EditFailure(error, {"position": record["position"], "index": index})
                target = point_components(target)
        else:
            target = list(before[slot]["object"])
            for axis_index, value in align_targets.items():
                target[axis_index] = value

        point, point_error = make_point(rt, target)
        if point_error:
            raise _EditFailure(point_error, {"position": record["position"], "index": index})
        write_error = set_vert(rt, node, index, point)
        if write_error:
            raise _EditFailure(write_error, {"position": record["position"], "index": index})

    return {
        "position": record["position"],
        "op": record["op"],
        "component": "vertices",
        "indices": list(indices),
        "moved": len(indices),
    }


def _apply(rt: Any, node: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one preflighted op and return its result record."""
    op = record["op"]

    if op in (OP_MOVE_VERTICES, OP_SET_VERTICES, OP_ALIGN_VERTICES):
        return _apply_vertex_position_ops(rt, node, record)

    if op == OP_WELD_VERTICES:
        error = weld_verts(rt, node, record["indices"])
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        return {
            "position": record["position"],
            "op": op,
            "component": "vertices",
            "indices": list(record["indices"]),
            "welded_to": record["indices"][0],
        }

    if op == OP_DELETE_VERTICES:
        before, error = num_verts(rt, node)
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        error = delete_verts(rt, node, record["indices"])
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        after, error = num_verts(rt, node)
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        return {
            "position": record["position"],
            "op": op,
            "component": "vertices",
            "indices": list(record["indices"]),
            "vertex_count": {"before": before, "after": after},
        }

    if op == OP_DELETE_EDGES:
        error = delete_edges(rt, node, record["indices"])
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        return {
            "position": record["position"],
            "op": op,
            "component": "edges",
            "indices": list(record["indices"]),
        }

    if op == OP_DELETE_FACES:
        error = delete_faces(rt, node, record["indices"])
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        return {
            "position": record["position"],
            "op": op,
            "component": "faces",
            "indices": list(record["indices"]),
        }

    if op == OP_DETACH_FACES:
        detached, error = detach_faces_to_node(rt, node, record["indices"], record["name"])
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        payload: Dict[str, Any] = {
            "position": record["position"],
            "op": op,
            "component": "faces",
            "indices": list(record["indices"]),
        }
        if detached is not None and hasattr(detached, "name"):
            payload["detached"] = component_identity(detached)
        else:
            payload["warnings"] = [
                "the detach reported no node, so the new mesh could not be identified; re-read the scene"
            ]
        return payload

    if op == OP_SET_FACE_MATERIAL_ID:
        error = set_face_mat_id(rt, node, record["indices"], record["material_id"])
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        return {
            "position": record["position"],
            "op": op,
            "component": "faces",
            "indices": list(record["indices"]),
            "material_id": record["material_id"],
        }

    if op == OP_SET_FACE_SMOOTHING_GROUP:
        error = set_face_smoothing_group(rt, node, record["indices"], record["smoothing_group"])
        if error:
            raise _EditFailure(error, {"position": record["position"]})
        return {
            "position": record["position"],
            "op": op,
            "component": "faces",
            "indices": list(record["indices"]),
            "smoothing_group": record["smoothing_group"],
        }

    # Explicit rather than a fall-through: an op added to OPERATIONS without an
    # apply branch has to fail as an _EditFailure while the hold is open, not
    # raise a KeyError out of the hold and leave the batch half-applied.
    raise _EditFailure(
        "op {!r} is accepted by the request schema but has no apply branch".format(op),
        {"position": record["position"]},
    )


# ── entry point ────────────────────────────────────────────────────────


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    ops: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
    allow_ungrouped: bool = False,
    label: str = "mesh edit",
) -> Dict[str, Any]:
    """Apply up to 256 component edits as one atomic, reversible batch."""
    try:
        normalized_name = validated_name(node_name, "node_name")
        if normalized_name is None and handle is None:
            raise _RequestError("node_name or handle is required")
        normalized = _normalize_ops(ops)
        validated_label = str(label or "mesh edit").strip() or "mesh edit"
        if len(validated_label) > 255:
            raise _RequestError("label must be at most 255 characters")
    except (_RequestError, ValueError) as exc:
        return _validation_error(str(exc))

    undo_block = {
        "supported": True,
        "granularity": _undo_utils.GRANULARITY_SINGLE_CALL,
        "grouped": False,
    }

    rt = get_runtime()
    node, error = resolve_shape(rt, node_name=normalized_name, handle=handle)
    if error:
        return error
    poly_error_message = ensure_poly(rt, node)
    if poly_error_message:
        return mesh_error(poly_error_message)

    plan, errors = _preflight(rt, node, normalized)
    requested = len(normalized)
    if errors:
        return {
            "success": False,
            "status": "error",
            "message": "{} of {} op(s) failed preflight; nothing was applied".format(
                len(errors), requested
            ),
            "data": {
                "node": component_identity(node),
                "requested": requested,
                "applied": 0,
                "passed_preflight": len(plan),
                "errors": errors,
                "undo": dict(undo_block),
            },
        }

    if dry_run:
        return {
            "success": True,
            "status": "success",
            "message": "Preflight passed for {} op(s); nothing was applied".format(len(plan)),
            "data": {
                "node": component_identity(node),
                "requested": requested,
                "applied": 0,
                "dry_run": True,
                "planned": [
                    {
                        "position": record["position"],
                        "op": record["op"],
                        "component": OP_COMPONENT[record["op"]],
                        "indices": list(record["indices"]),
                    }
                    for record in plan
                ],
                "destructive_ops": sorted(
                    {record["op"] for record in plan if record["op"] in DESTRUCTIVE_OPS}
                ),
            },
        }

    hold: Dict[str, Any] = {"engaged": False, "reason": ""}
    # Captured while the hold is open: a cancelled hold clears ``engaged`` on its
    # way out, so reading it afterwards would report every rollback as an
    # ungrouped batch.
    grouped = False
    applied: List[Dict[str, Any]] = []
    try:
        with _undo_utils.undo_step(rt, validated_label) as hold:
            grouped = bool(hold["engaged"])
            if not grouped and not allow_ungrouped:
                raise _EditFailure(
                    "this 3ds Max host cannot group the batch into one undo step",
                    {"reason": hold["reason"]},
                )
            # Appended one at a time rather than built by a comprehension: when
            # an op fails, the ops that already landed are the partial state the
            # caller has to recover from, so they must be in the result.
            for record in plan:
                applied.append(_apply(rt, node, record))
    except _EditFailure as exc:
        data: Dict[str, Any] = {
            "node": component_identity(node),
            "requested": requested,
            "applied": len(applied),
            "ops": applied,
            "undo": {
                "supported": True,
                "granularity": _undo_utils.GRANULARITY_SINGLE_CALL,
                "grouped": grouped,
            },
        }
        if grouped:
            data["rolled_back"] = True
            data["rollback"] = "cancelled_hold"
        else:
            # No hold means no automatic rollback. Saying so is the point: a
            # clean ``rolled_back`` here would hide a partially edited mesh.
            data["rolled_back"] = False
            data["rollback"] = "unavailable"
            data["rollback_error"] = (
                "the host did not open an undo hold ({}), so the {} op(s) applied before the failure "
                "were not rolled back; call undo_last and re-read the node, repeating while it still "
                "differs".format(hold.get("reason") or "no theHold.Begin/Accept/Cancel", len(applied))
            )
        data.update(exc.data)
        return {"success": False, "status": "error", "message": exc.message, "data": data}

    warnings: List[str] = []
    if not grouped:
        warnings.append(
            "the batch was not grouped into a single undo step ({}); each op may have left its own "
            "undo entry, so undo once and re-read the node, repeating while the scene still differs".format(
                hold.get("reason") or "the host did not open an undo hold"
            )
        )
    elif hold.get("reason"):
        warnings.append("the undo hold did not close cleanly: {}".format(hold["reason"]))
    for record in applied:
        warnings.extend(record.pop("warnings", []))

    counts = component_counts(rt, node)
    data = {
        "node": component_identity(node),
        "requested": requested,
        "applied": len(applied),
        "ops": applied,
        "counts": {key: counts[key] for key in ("vertex_count", "edge_count", "face_count")},
        "undo": {
            "supported": True,
            "granularity": _undo_utils.GRANULARITY_SINGLE_CALL,
            "grouped": grouped,
            "label": validated_label,
            "undo_tool": _undo_utils.UNDO_TOOL,
        },
    }
    if counts.get("errors"):
        warnings.extend("{}: {}".format(key, detail) for key, detail in sorted(counts["errors"].items()))
    if warnings:
        data["warnings"] = warnings
    return {
        "success": True,
        "status": "success",
        "message": "Applied {} component edit(s) in one undo step".format(len(applied)),
        "data": data,
    }
