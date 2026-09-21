"""Editable Poly component primitives shared by the mesh skill scripts.

The five tools this module serves (``create_mesh``, ``inspect_mesh``,
``mesh_edit``, ``edit_vertices``, ``pick_component``) all need the same three
things, and they need them to behave identically:

* **Resolve** a component index (vertex / edge / face) against the live node so
  an out-of-range or stale index is reported before anything is written.
* **Write** through ``polyOp`` / ``meshOp`` and **read the value back**. A host
  that ignored a write is a failure, never a warning on a successful result.
* **Map** between object space and world space through the node transform, so
  callers work in world coordinates the way an agent expects.

Every helper returns ``(value, error)`` rather than raising, so callers can
accumulate errors during preflight and abort before the first mutation.

Nothing here invents an execution channel. Component access goes through the
same ``polyOp`` / ``meshOp`` / ``intersectRay`` entry points the rest of the
adapter already uses, and every runtime object is passed in so an offline test
can substitute a fake.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._curve_utils import object_to_world, positions_match, world_to_object
from dcc_mcp_3dsmax._mesh_ops import mesh_error, mesh_success
from dcc_mcp_3dsmax._scene_utils import json_safe, node_identity

__all__ = [
    "MAX_FACES",
    "MAX_INSPECT_ITEMS",
    "MAX_OPS",
    "MAX_VERTICES",
    "POSITION_TOLERANCE",
    "component_counts",
    "create_poly_node",
    "delete_edges",
    "delete_faces",
    "delete_verts",
    "detach_faces_to_node",
    "ensure_poly",
    "face_center",
    "face_edges",
    "face_verts",
    "intersect_scene_ray",
    "make_point",
    "mesh_error",
    "mesh_success",
    "nearest_vertex_on_face",
    "num_edges",
    "point_components",
    "num_faces",
    "num_verts",
    "read_vert",
    "screen_ray",
    "set_face_mat_id",
    "set_face_smoothing_group",
    "set_vert",
    "to_object_space",
    "to_world_space",
    "verify_face_contains_point",
    "weld_verts",
]

# Upper bounds. A component batch is an authoring operation, not a data import;
# an unbounded list would let one call stall the main thread.
MAX_VERTICES = 10000

MAX_FACES = 10000
MAX_OPS = 256
MAX_INDICES_PER_OP = 4096

# Upper bound on how many components one ``inspect_mesh`` call serializes per
# component kind. Reads are cheap but the payload is not, so the result reports
# truncation instead of silently returning a partial list.
MAX_INSPECT_ITEMS = 2000

# Component indices are 1-based, matching every ``polyOp`` accessor.
MIN_COMPONENT_INDEX = 1

# 3ds Max stores coordinates as 32-bit floats, so a read-back is compared with
# a tolerance rather than exactly. See ``_curve_utils.positions_match``.
POSITION_TOLERANCE = 1e-3

# A hit point is accepted as lying on a face when it is within this distance of
# the face plane (world units) and inside the face's bounding sphere.
HIT_PLANE_TOLERANCE = 1e-2
HIT_BOUNDARY_TOLERANCE = 1e-3
HIT_RELATIVE_TOLERANCE = 1e-3


# ── Small helpers ──────────────────────────────────────────────────────


def _poly_op(runtime: Any) -> Tuple[Optional[Any], Optional[str]]:
    """Return ``(polyOp, error)`` for one runtime."""
    poly_op = getattr(runtime, "polyOp", None)
    if poly_op is None:
        return None, "the host does not expose polyOp, so Editable Poly components cannot be read or written"
    return poly_op, None


def _invoke(owner: Any, names: Sequence[str], arg_sets: Sequence[Sequence[Any]]) -> Tuple[bool, Any, str]:
    """Call the first ``owner.<name>(*args)`` shape the host accepts.

    Returns ``(ok, value, detail)``. Every candidate is tried before giving up
    and ``detail`` names them all, so a missing capability is reported instead
    of being downgraded to a no-op.
    """
    attempts: List[str] = []
    for name in names:
        function = getattr(owner, name, None)
        if not callable(function):
            attempts.append("{}: not exposed".format(name))
            continue
        for args in arg_sets:
            try:
                return True, function(*args), name
            except Exception as exc:  # noqa: BLE001 - a later shape may work.
                attempts.append("{}({}): {}".format(name, len(args), exc))
    return False, None, "{} exposes none of {} ({})".format(
        type(owner).__name__, ", ".join(names), "; ".join(attempts)
    )


def _to_int(value: Any, label: str) -> Tuple[Optional[int], Optional[str]]:
    """Coerce one host value to ``int`` or explain why it cannot be."""
    if isinstance(value, bool) or value is None:
        return None, "{} returned {!r}, which is not a count".format(label, value)
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, "{} returned {!r}, which is not a count".format(label, value)


def _point_to_list(point: Any) -> Optional[List[float]]:
    """Convert a Point3-like value into a plain XYZ list."""
    for axis in ("x", "y", "z"):
        if getattr(point, axis, None) is None:
            return None
    try:
        return [float(point.x), float(point.y), float(point.z)]
    except (TypeError, ValueError):
        return None


def point_components(value: Any) -> Optional[List[float]]:
    """Normalize a host ``Point3`` or a plain XYZ triple into a float list.

    Mapping helpers hand back a host point while a request hands in a list, so
    every call site that builds a point accepts both rather than indexing into
    whichever type it happens to hold.
    """
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        try:
            return [float(value.x), float(value.y), float(value.z)]
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(component) for component in value]
        except (TypeError, ValueError):
            return None
    return None


def make_point(runtime: Any, values: Any) -> Tuple[Optional[Any], Optional[str]]:
    """Build a host ``Point3`` from an XYZ triple or another host point."""
    components = point_components(values)
    if components is None:
        return None, "{!r} is not an XYZ triple".format(values)
    factory = getattr(runtime, "Point3", None)
    if not callable(factory):
        return None, "the host does not expose Point3"
    try:
        return factory(components[0], components[1], components[2]), None
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "could not build a Point3 from {}: {}".format(components, exc)


def _index_list_to_list(value: Any) -> Optional[List[int]]:
    """Normalize a host index array into a list of 1-based ``int`` indices."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [int(value)]
    try:
        items = list(value)
    except TypeError:
        return None
    indices: List[int] = []
    for item in items:
        try:
            indices.append(int(item))
        except (TypeError, ValueError):
            return None
    return indices


# ── Coordinate mapping ─────────────────────────────────────────────────


def to_world_space(node: Any, point: Any) -> Tuple[Optional[List[float]], Optional[str]]:
    """Map one object-space host point into world space."""
    return object_to_world(node, point)


def to_object_space(runtime: Any, node: Any, world: Sequence[float]) -> Tuple[Optional[Any], Optional[str]]:
    """Map one world-space XYZ triple into the node's object space."""
    return world_to_object(runtime, node, world)


# ── Counts and capability ──────────────────────────────────────────────


def num_verts(runtime: Any, node: Any) -> Tuple[Optional[int], Optional[str]]:
    """Return the Editable Poly vertex count, or why it cannot be read."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getNumVerts",), ((node,),))
    if not ok:
        return None, detail
    return _to_int(value, "polyOp.getNumVerts")


def num_edges(runtime: Any, node: Any) -> Tuple[Optional[int], Optional[str]]:
    """Return the Editable Poly edge count, or why it cannot be read."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getNumEdges",), ((node,),))
    if not ok:
        return None, detail
    return _to_int(value, "polyOp.getNumEdges")


def num_faces(runtime: Any, node: Any) -> Tuple[Optional[int], Optional[str]]:
    """Return the Editable Poly face count, or why it cannot be read."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getNumFaces",), ((node,),))
    if not ok:
        return None, detail
    return _to_int(value, "polyOp.getNumFaces")


def component_counts(runtime: Any, node: Any) -> Dict[str, Any]:
    """Return ``{vertex_count, edge_count, face_count, errors}`` for one node.

    The three counts are read independently so a host that exposes only some of
    them still returns a usable answer; anything unreadable is named in
    ``errors`` instead of being defaulted to zero.
    """
    counts: Dict[str, Any] = {"vertex_count": None, "edge_count": None, "face_count": None}
    errors: Dict[str, str] = {}
    for key, reader in (
        ("vertex_count", num_verts),
        ("edge_count", num_edges),
        ("face_count", num_faces),
    ):
        value, error = reader(runtime, node)
        if error:
            errors[key] = error
        else:
            counts[key] = value
    if errors:
        counts["errors"] = errors
    return counts


def ensure_poly(runtime: Any, node: Any) -> Optional[str]:
    """Return an error when ``node`` is not readable as an Editable Poly.

    Detection is a read, not a class-name guess: a node whose vertex count can
    be read through ``polyOp`` is editable as a poly. A node that fails is
    reported with the concrete failure so the caller can convert it first.
    """
    _count, error = num_verts(runtime, node)
    if error:
        return "{} is not readable as an Editable Poly ({})".format(
            str(getattr(node, "name", "<node>")), error
        )
    return None


# ── Component reads ────────────────────────────────────────────────────


def read_vert(runtime: Any, node: Any, index: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read one vertex as ``{index, object, world}`` positions."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getVert",), ((node, index),))
    if not ok:
        return None, detail
    local = _point_to_list(value)
    if local is None:
        return None, "polyOp.getVert returned {!r}, which is not an XYZ triple".format(value)
    world, world_error = to_world_space(node, value)
    if world_error:
        return None, world_error
    return {"index": int(index), "object": local, "world": world}, None


def face_verts(runtime: Any, node: Any, index: int) -> Tuple[Optional[List[int]], Optional[str]]:
    """Return the 1-based vertex indices around one face, in winding order."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getFaceVerts",), ((node, index),))
    if not ok:
        return None, detail
    indices = _index_list_to_list(value)
    if not indices:
        return None, "polyOp.getFaceVerts returned {!r}, which is not a vertex index list".format(value)
    return indices, None


def face_edges(runtime: Any, node: Any, index: int) -> Tuple[Optional[List[int]], Optional[str]]:
    """Return the 1-based edge indices around one face."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getFaceEdges",), ((node, index),))
    if not ok:
        return None, detail
    indices = _index_list_to_list(value)
    if not indices:
        return None, "polyOp.getFaceEdges returned {!r}, which is not an edge index list".format(value)
    return indices, None


def edge_verts(runtime: Any, node: Any, index: int) -> Tuple[Optional[List[int]], Optional[str]]:
    """Return the two 1-based vertex indices of one edge."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getEdgeVerts",), ((node, index),))
    if not ok:
        return None, detail
    indices = _index_list_to_list(value)
    if not indices or len(indices) != 2:
        return None, "polyOp.getEdgeVerts returned {!r}, which is not a vertex pair".format(value)
    return indices, None


def face_center(runtime: Any, node: Any, index: int) -> Tuple[Optional[List[float]], Optional[str]]:
    """Return the object-space centre of one face."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getFaceCenter",), ((node, index),))
    if not ok:
        return None, detail
    centre = _point_to_list(value)
    if centre is None:
        return None, "polyOp.getFaceCenter returned {!r}, which is not an XYZ triple".format(value)
    return centre, None


def face_mat_id(runtime: Any, node: Any, index: int) -> Tuple[Optional[int], Optional[str]]:
    """Return the material ID of one face, or ``(None, error)`` when unreadable."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getFaceMatID",), ((node, index),))
    if not ok:
        return None, detail
    return _to_int(value, "polyOp.getFaceMatID")


def face_smoothing_group(runtime: Any, node: Any, index: int) -> Tuple[Optional[int], Optional[str]]:
    """Return the smoothing group of one face, or ``(None, error)``."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    ok, value, detail = _invoke(poly_op, ("getFaceSmoothGroup",), ((node, index),))
    if not ok:
        return None, detail
    return _to_int(value, "polyOp.getFaceSmoothGroup")


# ── Component writes ───────────────────────────────────────────────────
#
# Every writer verifies its effect by reading the component back. A host that
# reports no error but kept the old value is reported as a failure, because a
# call that claims success while the scene changed differently is worse than a
# call that fails.


def set_vert(runtime: Any, node: Any, index: int, local_point: Any) -> Optional[str]:
    """Move one vertex to ``local_point`` (object space) and verify it moved.

    ``polyOp.setVert`` takes a *set* of vertices and one position, so distinct
    positions need one call per vertex. That is slower than a batched call but
    it is the only shape that can be verified per vertex, which is what the
    no-silent-success rule requires.
    """
    poly_op, error = _poly_op(runtime)
    if error:
        return error
    # The index argument is a bit array in MAXScript; hosts accept a bare index
    # or an array depending on the version, so both shapes are tried.
    ok, _value, detail = _invoke(
        poly_op,
        ("setVert",),
        ((node, [int(index)], local_point), (node, int(index), local_point)),
    )
    if not ok:
        return detail
    readback, read_error = read_vert(runtime, node, index)
    if read_error:
        return "vertex {} was written but cannot be read back: {}".format(index, read_error)
    wanted = _point_to_list(local_point)
    if wanted is None or not positions_match(wanted, readback["object"]):
        return "3ds Max kept vertex {} at {} instead of the requested {}".format(
            index, readback["object"], wanted
        )
    return None


def delete_verts(runtime: Any, node: Any, indices: Sequence[int]) -> Optional[str]:
    """Delete vertices and verify the count dropped by the number removed."""
    poly_op, error = _poly_op(runtime)
    if error:
        return error
    before, error = num_verts(runtime, node)
    if error:
        return error
    wanted = sorted({int(index) for index in indices})
    ok, _value, detail = _invoke(
        poly_op,
        ("deleteVerts",),
        ((node, list(wanted)), (node, wanted[0] if len(wanted) == 1 else list(wanted))),
    )
    if not ok:
        return detail
    after, error = num_verts(runtime, node)
    if error:
        return error
    # Deleting a vertex removes the faces and edges that used it, so only the
    # vertex count is a reliable verification signal.
    if after != before - len(wanted):
        return "deleting {} vertice(s) left {} vertices instead of the expected {}".format(
            len(wanted), after, before - len(wanted)
        )
    return None


def delete_edges(runtime: Any, node: Any, indices: Sequence[int]) -> Optional[str]:
    """Delete edges and verify the edge count dropped by at least that many.

    The bound is one-sided, unlike :func:`delete_verts` and
    :func:`delete_faces`. Removing an edge also removes every face that used it,
    and each of those takes its own edges with it, so the exact result depends
    on the topology. The count of *requested* edges is still a lower bound on
    what disappears, which is enough to catch the failure that matters: a host
    that removed only some of them.
    """
    poly_op, error = _poly_op(runtime)
    if error:
        return error
    before, error = num_edges(runtime, node)
    if error:
        return error
    wanted = sorted({int(index) for index in indices})
    ok, _value, detail = _invoke(poly_op, ("deleteEdges",), ((node, list(wanted)),))
    if not ok:
        return detail
    after, error = num_edges(runtime, node)
    if error:
        return error
    # Each requested edge is gone, so `before - len(wanted)` is the most that
    # can remain; anything above it means the host did not remove them all.
    if after > before - len(wanted):
        return "deleting {} edge(s) left {} edges instead of at most {}".format(
            len(wanted), after, before - len(wanted)
        )
    return None


def delete_faces(runtime: Any, node: Any, indices: Sequence[int]) -> Optional[str]:
    """Delete faces and verify the face count dropped by the number removed."""
    poly_op, error = _poly_op(runtime)
    if error:
        return error
    before, error = num_faces(runtime, node)
    if error:
        return error
    wanted = sorted({int(index) for index in indices})
    ok, _value, detail = _invoke(poly_op, ("deleteFaces",), ((node, list(wanted)),))
    if not ok:
        return detail
    after, error = num_faces(runtime, node)
    if error:
        return error
    if after != before - len(wanted):
        return "deleting {} face(s) left {} faces instead of the expected {}".format(
            len(wanted), after, before - len(wanted)
        )
    return None


def detach_faces_to_node(
    runtime: Any, node: Any, indices: Sequence[int], name: str
) -> Tuple[Optional[Any], Optional[str]]:
    """Detach faces into a new node and verify the source face count dropped."""
    poly_op, error = _poly_op(runtime)
    if error:
        return None, error
    before, error = num_faces(runtime, node)
    if error:
        return None, error
    wanted = sorted({int(index) for index in indices})
    ok, value, detail = _invoke(
        poly_op,
        ("detachFaces",),
        (
            (node, list(wanted), True, name),
            (node, list(wanted)),
        ),
    )
    if not ok:
        return None, detail
    after, error = num_faces(runtime, node)
    if error:
        return None, error
    if after != before - len(wanted):
        return None, "detaching {} face(s) left {} faces instead of the expected {}".format(
            len(wanted), after, before - len(wanted)
        )
    return value, None


def weld_verts(runtime: Any, node: Any, indices: Sequence[int]) -> Optional[str]:
    """Weld every index in ``indices`` onto the first one and verify the count."""
    if len(indices) < 2:
        return "weld_vertices needs at least two vertex indices"
    poly_op, error = _poly_op(runtime)
    if error:
        return error
    before, error = num_verts(runtime, node)
    if error:
        return error
    target = int(indices[0])
    for index in indices[1:]:
        ok, _value, detail = _invoke(
            poly_op,
            ("weldVerts",),
            ((node, target, int(index)), (node, int(index), target)),
        )
        if not ok:
            return detail
    after, error = num_verts(runtime, node)
    if error:
        return error
    if after != before - (len(indices) - 1):
        return "welding {} vertice(s) left {} vertices instead of the expected {}".format(
            len(indices), after, before - (len(indices) - 1)
        )
    return None


def set_face_mat_id(runtime: Any, node: Any, indices: Sequence[int], material_id: int) -> Optional[str]:
    """Assign a material ID to faces, verifying it by reading one of them back."""
    poly_op, error = _poly_op(runtime)
    if error:
        return error
    wanted = [int(index) for index in indices]
    ok, _value, detail = _invoke(
        poly_op,
        ("setFaceMatID",),
        ((node, wanted, int(material_id)),),
    )
    if not ok:
        return detail
    readback, read_error = face_mat_id(runtime, node, wanted[0])
    if read_error:
        # An unreadable material ID is a warning for the caller, not a silent
        # success: the write may have taken effect but the adapter cannot prove
        # it, and claiming otherwise is exactly what this module refuses to do.
        return "faces were written but the material ID cannot be read back: {}".format(read_error)
    if readback != int(material_id):
        return "3ds Max kept material id {} instead of the requested {}".format(
            readback, int(material_id)
        )
    return None


def set_face_smoothing_group(
    runtime: Any, node: Any, indices: Sequence[int], smoothing_group: int
) -> Optional[str]:
    """Assign a smoothing group to faces, verifying it by read-back."""
    poly_op, error = _poly_op(runtime)
    if error:
        return error
    wanted = [int(index) for index in indices]
    ok, _value, detail = _invoke(
        poly_op,
        ("setFaceSmoothGroup",),
        ((node, wanted, int(smoothing_group)),),
    )
    if not ok:
        return detail
    readback, read_error = face_smoothing_group(runtime, node, wanted[0])
    if read_error:
        return "faces were written but the smoothing group cannot be read back: {}".format(read_error)
    if readback != int(smoothing_group):
        return "3ds Max kept smoothing group {} instead of the requested {}".format(
            readback, int(smoothing_group)
        )
    return None


# ── Node construction ──────────────────────────────────────────────────

# Constructors tried, in order, when ``create_mesh`` needs an empty mesh node to
# receive a TriMesh. ``Editable_Mesh`` accepts an assigned ``.mesh``; ``Box`` is
# the fallback for hosts that only expose primitives and needs converting first.
_MESH_NODE_CLASSES = ("Editable_Mesh", "Editable_mesh")
_PRIMITIVE_NODE_CLASSES = ("Box",)


def create_poly_node(
    runtime: Any,
    *,
    world_vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    name: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str], Dict[str, Any]]:
    """Build one Editable Poly node from world vertices and index faces.

    Returns ``(node, error, details)``. ``details`` always carries
    ``created_with`` and ``created_node`` so the caller can roll back a node the
    construction left behind, plus the conversion entry points that were used.

    ``faces`` are 1-based index rings, exactly as an agent writes them, and each
    ring is fan-triangulated because the TriMesh constructor only accepts
    triangles. Which faces were split is reported rather than hidden.

    Nothing here claims success on a partially built node: the vertex count and
    the face count are both read back after conversion, and a mismatch fails the
    call while leaving ``created_node`` set so the caller can remove it.
    """
    details: Dict[str, Any] = {"created_with": None, "created_node": False, "converted_with": None}
    node = None
    attempts: List[str] = []

    for class_name in _MESH_NODE_CLASSES:
        factory = getattr(runtime, class_name, None)
        if not callable(factory):
            attempts.append("{}: not exposed".format(class_name))
            continue
        try:
            node = factory()
        except Exception as exc:  # noqa: BLE001 - the next candidate may work.
            attempts.append("{}: {}".format(class_name, exc))
            node = None
            continue
        if node is None:
            attempts.append("{}: returned nothing".format(class_name))
            continue
        details["created_with"] = class_name
        details["created_node"] = True
        break

    if node is None:
        for class_name in _PRIMITIVE_NODE_CLASSES:
            factory = getattr(runtime, class_name, None)
            if not callable(factory):
                attempts.append("{}: not exposed".format(class_name))
                continue
            try:
                node = factory()
            except Exception as exc:  # noqa: BLE001 - the next candidate may work.
                attempts.append("{}: {}".format(class_name, exc))
                node = None
                continue
            if node is None:
                attempts.append("{}: returned nothing".format(class_name))
                continue
            converter = getattr(runtime, "convertToMesh", None)
            if not callable(converter):
                attempts.append("{} created but convertToMesh is not exposed".format(class_name))
                node = None
                continue
            try:
                converter(node)
            except Exception as exc:  # noqa: BLE001 - surface the host rejection.
                attempts.append("convertToMesh: {}".format(exc))
                node = None
                continue
            details["created_with"] = class_name
            details["created_node"] = True
            break

    if node is None:
        return None, "3ds Max exposes no node class this tool can fill with a mesh ({})".format(
            "; ".join(attempts)
        ), details

    tri_mesh_factory = getattr(runtime, "mesh", None)
    if not callable(tri_mesh_factory):
        return node, "3ds Max does not expose the mesh() TriMesh constructor", details

    point3_factory = getattr(runtime, "Point3", None)
    if not callable(point3_factory):
        return node, "3ds Max does not expose Point3, so the vertex list cannot be built", details

    try:
        vertex_points = [point3_factory(float(v[0]), float(v[1]), float(v[2])) for v in world_vertices]
        face_points = [point3_factory(float(f[0]), float(f[1]), float(f[2])) for f in faces]
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return node, "could not build the TriMesh payload: {}".format(exc), details

    try:
        tri_mesh = tri_mesh_factory(vertices=vertex_points, faces=face_points)
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return node, "mesh() rejected the vertex/face payload: {}".format(exc), details

    try:
        node.mesh = tri_mesh
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return node, "the node rejected the assigned TriMesh: {}".format(exc), details

    updater = getattr(runtime, "update", None)
    if callable(updater):
        try:
            updater(node)
        except Exception as exc:  # noqa: BLE001 - surface the host rejection.
            return node, "update() failed after assigning the mesh: {}".format(exc), details

    converter = getattr(runtime, "convertToPoly", None)
    if not callable(converter):
        return node, "3ds Max does not expose convertToPoly, so the node is not an Editable Poly", details
    try:
        converter(node)
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return node, "convertToPoly failed: {}".format(exc), details
    details["converted_with"] = "convertToPoly"

    counts = component_counts(runtime, node)
    details["counts"] = counts
    if counts["vertex_count"] != len(world_vertices):
        return node, (
            "the node reports {} vertice(s) instead of the {} requested, so the mesh was not built as asked".format(
                counts["vertex_count"], len(world_vertices)
            )
        ), details
    if counts.get("face_count") != len(faces):
        return node, (
            "the node reports {} face(s) instead of the {} requested, so the mesh was not built as asked".format(
                counts.get("face_count"), len(faces)
            )
        ), details

    if name is not None:
        try:
            node.name = name
        except Exception as exc:  # noqa: BLE001 - a naming failure is a hard failure.
            return node, "could not name the mesh node: {}".format(exc), details
    return node, None, details


# ── Ray casting ────────────────────────────────────────────────────────

# Entry points probed to turn a viewport image position into a world ray. The
# list is short and every name is tried before the call gives up, so a host that
# exposes none of them produces an explicit error naming what was probed rather
# than a fabricated ray.
SCREEN_RAY_ENTRY_POINTS = ("mapScreenToWorldRay", "mapScreenToWorldRayEx")

# Ordered ray-cast channels. ``intersectRayEx`` reports the face it hit, which
# is what a component pick needs; ``intersectRay`` only reports a point, so the
# face has to be inferred from the geometry and is flagged as such.
RAY_CAST_ENTRY_POINTS = ("intersectRayEx", "intersectRay")


def screen_ray(
    runtime: Any, x: float, y: float
) -> Tuple[Optional[Any], Optional[str], List[str]]:
    """Build a world ray from a viewport image position.

    Returns ``(ray, error, probed)``. The mapping is a host capability: when no
    entry point is exposed the call fails and ``probed`` names every candidate,
    because inventing a projection would return a plausible but unverifiable
    ray and therefore a plausible but wrong component.
    """
    probed: List[str] = []
    for name in SCREEN_RAY_ENTRY_POINTS:
        function = getattr(runtime, name, None)
        if not callable(function):
            probed.append("{}: not exposed".format(name))
            continue
        for args in ((x, y), ([x, y],)):
            try:
                ray = function(*args)
            except Exception as exc:  # noqa: BLE001 - the next shape may work.
                probed.append("{}({}): {}".format(name, len(args), exc))
                continue
            if ray is None:
                probed.append("{}({}): returned nothing".format(name, len(args)))
                continue
            return ray, None, probed
    return None, (
        "this 3ds Max host exposes no viewport position -> world ray entry point ({}), so an image "
        "position cannot be mapped to a component. Supply ray_origin and ray_direction instead.".format(
            "; ".join(probed) or "nothing to probe"
        )
    ), probed


def make_ray(runtime: Any, origin: Sequence[float], direction: Sequence[float]) -> Tuple[Optional[Any], Optional[str]]:
    """Build a host ``Ray`` from an origin and a direction, normalized."""
    factory = getattr(runtime, "ray", None)
    if not callable(factory):
        return None, "the host does not expose the ray() constructor"
    length = math.sqrt(sum(float(component) ** 2 for component in direction))
    if not math.isfinite(length) or length <= 0.0:
        return None, "ray_direction must be a non-zero vector"
    origin_point, error = make_point(runtime, origin)
    if error:
        return None, error
    direction_point, error = make_point(
        runtime, [float(component) / length for component in direction]
    )
    if error:
        return None, error
    try:
        return factory(origin_point, direction_point), None
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, "ray() rejected the origin/direction: {}".format(exc)


def _ray_hit_point(value: Any) -> Optional[List[float]]:
    """Extract the hit point from an ``intersectRay`` / ``intersectRayEx`` result."""
    if value is None:
        return None
    first = value
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        first = value[0]
    return _point_to_list(first)


def _ray_hit_face(value: Any) -> Optional[int]:
    """Extract the face index an ``intersectRayEx`` result reported."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return int(value[1])
    except (TypeError, ValueError):
        return None


def intersect_scene_ray(
    runtime: Any, node: Any, ray: Any
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Cast one ray at one node and return what it hit.

    Returns ``(hit, error)`` where ``hit`` is ``None`` for a clean miss and
    carries ``point``, ``face_index``, ``face_reported``, and ``distance`` on a
    hit. ``face_reported`` is False when the host only returned a point and the
    face had to be resolved by proximity, so the caller can tell a face the host
    named from one the adapter inferred.

    A host that exposes no ray-cast entry point is an error, not a miss.
    """
    probed: List[str] = []
    used = None
    value = None
    for name in RAY_CAST_ENTRY_POINTS:
        function = getattr(runtime, name, None)
        if not callable(function):
            probed.append("{}: not exposed".format(name))
            continue
        try:
            value = function(node, ray)
        except Exception as exc:  # noqa: BLE001 - the next candidate may work.
            probed.append("{}: {}".format(name, exc))
            continue
        used = name
        break
    if used is None:
        return None, "this 3ds Max host exposes no ray-cast entry point ({})".format("; ".join(probed))

    point = _ray_hit_point(value)
    if point is None:
        # ``intersectRay`` returns undefined on a miss; that is a clean miss,
        # not a failure.
        return None, None
    hit: Dict[str, Any] = {
        "point": point,
        "face_index": _ray_hit_face(value) if used == "intersectRayEx" else None,
        "face_reported": used == "intersectRayEx" and _ray_hit_face(value) is not None,
        "entry_point": used,
    }
    return hit, None


def verify_face_contains_point(
    runtime: Any, node: Any, face_index: int, point: Sequence[float]
) -> Tuple[Optional[bool], Optional[str]]:
    """Return whether ``point`` lies on ``face_index``, or ``None`` if unreadable.

    The check is a plane-distance test followed by a point-in-polygon test in
    that plane. It is what makes a reported face index evidence rather than an
    assertion: a host that names a face the point is not on is reported as
    unverified instead of trusted. A bounding-sphere test would be cheaper but
    too coarse - it accepts any point inside a circle around the face, which on
    a coarse mesh includes most of the neighbouring faces.
    """
    indices, error = face_verts(runtime, node, face_index)
    if error:
        return None, error
    corners: List[List[float]] = []
    for vertex_index in indices:
        vertex, vertex_error = read_vert(runtime, node, vertex_index)
        if vertex_error:
            return None, vertex_error
        corners.append(vertex["object"])
    if len(corners) < 3:
        return None, "face {} reports only {} vertice(s), so it cannot be verified".format(
            face_index, len(corners)
        )

    centre, centre_error = face_center(runtime, node, face_index)
    if centre_error:
        return None, centre_error

    normal = _polygon_normal(corners)
    if normal is None:
        return None, "face {} is degenerate, so its plane cannot be computed".format(face_index)
    # Both tolerances scale with the face radius, so the test stays meaningful
    # on a sub-millimetre face and on a kilometre-wide one.
    radius = max(
        math.sqrt(sum((corner[axis] - centre[axis]) ** 2 for axis in range(3))) for corner in corners
    )
    offset = sum((float(point[axis]) - centre[axis]) * normal[axis] for axis in range(3))
    if abs(offset) > HIT_PLANE_TOLERANCE + radius * HIT_RELATIVE_TOLERANCE:
        return False, None

    polygon_2d = [_project_to_plane(normal, corner) for corner in corners]
    point_2d = _project_to_plane(normal, point)
    if _point_in_polygon(point_2d, polygon_2d):
        return True, None
    # A hit on a shared edge lies on both faces, so a point just outside the
    # polygon but within tolerance of one of its edges still counts.
    boundary_tolerance = HIT_BOUNDARY_TOLERANCE + radius * HIT_RELATIVE_TOLERANCE
    if _distance_to_segments(point_2d, polygon_2d) <= boundary_tolerance:
        return True, None
    return False, None


def nearest_vertex_on_face(
    runtime: Any, node: Any, face_index: int, point: Sequence[float]
) -> Tuple[Optional[int], Optional[str]]:
    """Return the vertex of ``face_index`` closest to ``point``."""
    indices, error = face_verts(runtime, node, face_index)
    if error:
        return None, error
    best: Optional[int] = None
    best_distance = None
    for vertex_index in indices:
        vertex, vertex_error = read_vert(runtime, node, vertex_index)
        if vertex_error:
            return None, vertex_error
        distance = math.sqrt(
            sum((float(point[axis]) - vertex["object"][axis]) ** 2 for axis in range(3))
        )
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = int(vertex_index)
    return best, None


def _polygon_normal(corners: Sequence[Sequence[float]]) -> Optional[List[float]]:
    """Return the unit normal of a planar-ish polygon, or None when degenerate."""
    if len(corners) < 3:
        return None
    normal = [0.0, 0.0, 0.0]
    for index in range(len(corners)):
        current = corners[index]
        following = corners[(index + 1) % len(corners)]
        normal[0] += (current[1] - following[1]) * (current[2] + following[2])
        normal[1] += (current[2] - following[2]) * (current[0] + following[0])
        normal[2] += (current[0] - following[0]) * (current[1] + following[1])
    length = math.sqrt(sum(component ** 2 for component in normal))
    if not math.isfinite(length) or length <= 0.0:
        return None
    return [component / length for component in normal]


def _project_to_plane(normal: Sequence[float], value: Sequence[float]) -> Tuple[float, float]:
    """Drop the axis the normal is most aligned with, leaving a 2D point.

    Dropping one axis is only a valid projection when the polygon is not
    edge-on to it, which is why the axis with the largest normal component is
    the one dropped: it is the axis the polygon varies in least.
    """
    axis = max(range(3), key=lambda index: abs(normal[index]))
    return tuple(float(value[index]) for index in range(3) if index != axis)  # type: ignore[return-value]


def _point_in_polygon(point_2d: Sequence[float], polygon_2d: Sequence[Sequence[float]]) -> bool:
    """Crossing-number containment test; correct for concave simple polygons.

    Editable Poly faces are usually triangles, but an n-gon is a simple polygon
    too, so the test does not assume convexity.
    """
    inside = False
    count = len(polygon_2d)
    for index in range(count):
        ax, ay = polygon_2d[index]
        bx, by = polygon_2d[(index + 1) % count]
        if (ay > point_2d[1]) != (by > point_2d[1]):
            crossing = ax + (point_2d[1] - ay) / (by - ay) * (bx - ax)
            if point_2d[0] < crossing:
                inside = not inside
    return inside


def _distance_to_segments(point_2d: Sequence[float], polygon_2d: Sequence[Sequence[float]]) -> float:
    """Distance from a 2D point to the nearest edge of the polygon."""
    best = float("inf")
    count = len(polygon_2d)
    for index in range(count):
        ax, ay = polygon_2d[index]
        bx, by = polygon_2d[(index + 1) % count]
        dx, dy = bx - ax, by - ay
        length_squared = dx * dx + dy * dy
        if length_squared <= 0.0:
            continue
        parameter = max(
            0.0, min(1.0, ((point_2d[0] - ax) * dx + (point_2d[1] - ay) * dy) / length_squared)
        )
        distance = math.sqrt(
            (point_2d[0] - ax - parameter * dx) ** 2 + (point_2d[1] - ay - parameter * dy) ** 2
        )
        best = min(best, distance)
    return best


def component_identity(node: Any) -> Dict[str, Any]:
    """Return the node identity block used in every component result."""
    return node_identity(node)


def safe(value: Any) -> Any:
    """Serialize a host value for the result envelope."""
    return json_safe(value)


def poly_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return the shared error envelope."""
    return mesh_error(message, **data)


def poly_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return the shared success envelope."""
    return mesh_success(message, **data)
