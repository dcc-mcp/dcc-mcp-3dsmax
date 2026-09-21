"""Component-level read of an Editable Poly: vertices, edges, and faces.

``inspect_mesh`` is the counterpart every other component tool needs. It hands
out the 1-based ``polyOp`` indices that ``mesh_edit``, ``edit_vertices``, and
``pick_component`` accept, together with both the object-space and the
world-space position of every vertex, so an agent can compute a target in world
coordinates and still write it back through the same index.

Reads are strict in one direction only: a component index that is out of range
fails the call, while a *descriptive* field the host does not expose (a face's
edge ring, a material ID) is reported as ``None`` plus a warning. Reporting a
missing descriptive field as a zero would be the silent-success failure mode
this adapter refuses to ship.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._curve_utils import resolve_shape, validated_int, validated_name
from dcc_mcp_3dsmax._poly_utils import (
    MAX_INSPECT_ITEMS,
    component_counts,
    component_identity,
    edge_verts,
    ensure_poly,
    face_center,
    face_edges,
    face_mat_id,
    face_smoothing_group,
    face_verts,
    mesh_error,
    mesh_success,
    num_edges,
    num_faces,
    num_verts,
    read_vert,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

COMPONENT_KINDS = ("vertices", "edges", "faces")


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _normalize_include(value: Any) -> List[str]:
    """Return the component kinds to serialize, defaulting to all of them."""
    if value is None:
        return list(COMPONENT_KINDS)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("include must be an array of {}".format(", ".join(COMPONENT_KINDS)))
    for kind in value:
        if not isinstance(kind, str) or kind.strip().lower() not in COMPONENT_KINDS:
            raise ValueError("include entries must be one of {}".format(", ".join(COMPONENT_KINDS)))
    normalized: List[str] = []
    for kind in value:
        wanted = kind.strip().lower()
        if wanted not in normalized:
            normalized.append(wanted)
    return normalized or list(COMPONENT_KINDS)


def _read_vertices(runtime: Any, node: Any, start: int, limit: int) -> Dict[str, Any]:
    """Serialize a vertex range, reporting any index that cannot be read."""
    total, error = num_verts(runtime, node)
    if error:
        return {"error": error, "items": [], "total": None, "truncated": False}
    last = min(total, start + limit - 1) if total else 0
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index in range(start, last + 1):
        vertex, vertex_error = read_vert(runtime, node, index)
        if vertex_error:
            errors.append(vertex_error)
            continue
        items.append(vertex)
    return {
        "items": items,
        "total": total,
        "truncated": bool(total and last < total),
        "errors": errors,
    }


def _read_edges(runtime: Any, node: Any, start: int, limit: int) -> Dict[str, Any]:
    """Serialize an edge range as the vertex pair each edge spans."""
    total, error = num_edges(runtime, node)
    if error:
        return {"error": error, "items": [], "total": None, "truncated": False}
    last = min(total, start + limit - 1) if total else 0
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index in range(start, last + 1):
        indices, edge_error = edge_verts(runtime, node, index)
        if edge_error:
            errors.append(edge_error)
            continue
        items.append({"index": index, "vertices": indices})
    return {
        "items": items,
        "total": total,
        "truncated": bool(total and last < total),
        "errors": errors,
    }


def _read_faces(runtime: Any, node: Any, start: int, limit: int) -> Dict[str, Any]:
    """Serialize a face range with its vertex ring, centre, and shading data."""
    total, error = num_faces(runtime, node)
    if error:
        return {"error": error, "items": [], "total": None, "truncated": False}
    last = min(total, start + limit - 1) if total else 0
    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index in range(start, last + 1):
        indices, face_error = face_verts(runtime, node, index)
        if face_error:
            errors.append(face_error)
            continue
        centre, centre_error = face_center(runtime, node, index)
        material_id, material_error = face_mat_id(runtime, node, index)
        smoothing_group, smoothing_error = face_smoothing_group(runtime, node, index)
        ring, ring_error = face_edges(runtime, node, index)
        entry: Dict[str, Any] = {
            "index": index,
            "vertices": indices,
            "vertex_count": len(indices),
            "center": centre,
            "material_id": material_id,
            "smoothing_group": smoothing_group,
            "edges": ring,
        }
        # Descriptive additions only: an unreadable material ID does not make
        # the face itself unreadable, so it is named rather than fatal.
        for label, detail in (
            ("center_error", centre_error),
            ("material_id_error", material_error),
            ("smoothing_group_error", smoothing_error),
            ("edges_error", ring_error),
        ):
            if detail:
                entry[label] = detail
        items.append(entry)
    return {
        "items": items,
        "total": total,
        "truncated": bool(total and last < total),
        "errors": errors,
    }


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    include: Optional[Sequence[str]] = None,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Read the vertices, edges, and faces of one Editable Poly node."""
    try:
        normalized_name = validated_name(node_name, "node_name")
        normalized_include = _normalize_include(include)
        start = validated_int(offset, "offset", default=1, minimum=1, maximum=MAX_INSPECT_ITEMS * 100)
        page_size = validated_int(limit, "limit", default=MAX_INSPECT_ITEMS, minimum=1, maximum=MAX_INSPECT_ITEMS)
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()
    node, error = resolve_shape(rt, node_name=normalized_name, handle=handle)
    if error:
        return error

    poly_error_message = ensure_poly(rt, node)
    if poly_error_message:
        return mesh_error(poly_error_message)

    counts = component_counts(rt, node)
    data: Dict[str, Any] = {
        "node": component_identity(node),
        "vertex_count": counts["vertex_count"],
        "edge_count": counts["edge_count"],
        "face_count": counts["face_count"],
    }
    if counts.get("errors"):
        data["count_errors"] = counts["errors"]

    warnings: List[str] = []
    readers = {
        "vertices": _read_vertices,
        "edges": _read_edges,
        "faces": _read_faces,
    }
    for kind in normalized_include:
        result = readers[kind](rt, node, start, page_size)
        data[kind] = result["items"]
        data["{}_truncated".format(kind)] = result["truncated"]
        if result.get("error"):
            data["{}_error".format(kind)] = result["error"]
            warnings.append("{} could not be read: {}".format(kind, result["error"]))
        for detail in result.get("errors") or []:
            warnings.append("{}: {}".format(kind, detail))
    if warnings:
        data["warnings"] = warnings
    return mesh_success(
        "Inspected {} ({} vertice(s), {} edge(s), {} face(s))".format(
            str(getattr(node, "name", "<node>")),
            data["vertex_count"],
            data["edge_count"],
            data["face_count"],
        ),
        **data
    )
