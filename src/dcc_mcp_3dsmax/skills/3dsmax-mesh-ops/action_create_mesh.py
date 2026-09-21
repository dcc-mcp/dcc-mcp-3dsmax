"""Build an Editable Poly from explicit world-space vertices and ordered faces.

This is the entry point for geometry an agent computed rather than clicked: a
procedural panel, a reconstructed surface, a patch of vertices sampled from
another DCC. Vertices are given as world coordinates, exactly as
``inspect_mesh`` reports them, and faces as 1-based index rings.

Two things make the result trustworthy:

**Every vertex is read back.** The TriMesh is assigned, the node is converted to
an Editable Poly, and then each requested vertex is re-read in world space and
compared against what was asked for. A host that rounded, dropped, or merged
vertices fails the call instead of returning a mesh that looks right.

**A failed build leaves nothing behind.** Construction can fail after the node
exists - an assignment the node refused, a conversion that never ran, a name it
would not accept - so a failure deletes the node it made and reports whether
that deletion was *confirmed* through the handle lookup. A partially built mesh
that silently stays in the scene is worse than a failed call.

n-gons are fan-triangulated because the TriMesh constructor only accepts
triangles; which faces were split is reported in ``triangulated_faces`` rather
than being hidden behind a clean success.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._curve_utils import delete_node, positions_match, validated_name, validated_vectors
from dcc_mcp_3dsmax._poly_utils import (
    MAX_FACES,
    MAX_VERTICES,
    component_counts,
    component_identity,
    create_poly_node,
    ensure_poly,
    mesh_error,
    mesh_success,
    read_vert,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

# A face needs at least three vertices to enclose an area.
MIN_FACE_VERTICES = 3
# Upper bound on vertices in one face. A larger ring is almost always a
# mis-specified face rather than a real polygon.
MAX_FACE_VERTICES = 64


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _validate_faces(
    faces: Any, vertex_count: int
) -> List[List[int]]:
    """Validate 1-based index rings and return them as plain int lists."""
    if isinstance(faces, (str, bytes)) or not isinstance(faces, Sequence):
        raise ValueError("faces must be an array of vertex index arrays")
    if not 1 <= len(faces) <= MAX_FACES:
        raise ValueError("faces must contain between 1 and {} entries".format(MAX_FACES))
    normalized: List[List[int]] = []
    for position, face in enumerate(faces):
        label = "faces[{}]".format(position)
        if isinstance(face, (str, bytes)) or not isinstance(face, Sequence):
            raise ValueError("{} must be an array of 1-based vertex indices".format(label))
        if not MIN_FACE_VERTICES <= len(face) <= MAX_FACE_VERTICES:
            raise ValueError(
                "{} must reference between {} and {} vertices".format(
                    label, MIN_FACE_VERTICES, MAX_FACE_VERTICES
                )
            )
        indices: List[int] = []
        for offset, index in enumerate(face):
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("{}[{}] must be an integer vertex index".format(label, offset))
            if not 1 <= index <= vertex_count:
                raise ValueError(
                    "{}[{}] references vertex {}, which is outside 1..{}".format(
                        label, offset, index, vertex_count
                    )
                )
            indices.append(index)
        if len(set(indices)) != len(indices):
            raise ValueError("{} repeats a vertex index, so it does not describe a simple polygon".format(label))
        normalized.append(indices)
    return normalized


def _fan_triangulate(faces: Sequence[Sequence[int]]) -> tuple:
    """Split every n-gon into triangles around its first vertex.

    Returns ``(triangles, triangulated)`` where ``triangulated`` lists the
    1-based face positions that were split, so the caller can report the change
    instead of pretending the requested topology survived intact.
    """
    triangles: List[List[int]] = []
    triangulated: List[int] = []
    for position, face in enumerate(faces, start=1):
        if len(face) == MIN_FACE_VERTICES:
            triangles.append(list(face))
            continue
        triangulated.append(position)
        anchor = int(face[0])
        for offset in range(1, len(face) - 1):
            triangles.append([anchor, int(face[offset]), int(face[offset + 1])])
    return triangles, triangulated


@with_max
def main(
    vertices: Optional[Sequence[Sequence[float]]] = None,
    faces: Optional[Sequence[Sequence[int]]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create one Editable Poly node from vertices and faces."""
    try:
        if vertices is None:
            raise ValueError("vertices is required")
        world_vertices = validated_vectors(
            vertices, "vertices", min_items=MIN_FACE_VERTICES, max_items=MAX_VERTICES, item_length=3
        )
        if faces is None:
            raise ValueError("faces is required")
        validated_faces = _validate_faces(faces, len(world_vertices))
        normalized_name = validated_name(name, "name")
    except ValueError as exc:
        return _validation_error(str(exc))

    triangles, triangulated = _fan_triangulate(validated_faces)

    rt = get_runtime()
    node, error, details = create_poly_node(
        rt, world_vertices=world_vertices, faces=triangles, name=normalized_name
    )

    if error:
        payload: Dict[str, Any] = {
            "created_with": details.get("created_with"),
            "converted_with": details.get("converted_with"),
        }
        if details.get("created_node"):
            payload["rolled_back"] = delete_node(rt, node)
            payload["node"] = component_identity(node)
        return mesh_error(error, **payload)

    # The node exists, is a poly, and reports the right counts. Confirm the
    # geometry itself: a host that merged or moved a vertex has not built what
    # was asked for, and that is a failure, not a warning.
    poly_error_message = ensure_poly(rt, node)
    if poly_error_message:
        return mesh_error(poly_error_message, rolled_back=delete_node(rt, node))

    mismatches: List[Dict[str, Any]] = []
    for index, requested in enumerate(world_vertices, start=1):
        vertex, vertex_error = read_vert(rt, node, index)
        if vertex_error:
            mismatches.append({"index": index, "error": vertex_error})
            continue
        if not positions_match(requested, vertex["world"]):
            mismatches.append(
                {"index": index, "requested": list(requested), "readback": vertex["world"]}
            )

    if mismatches:
        return mesh_error(
            "the node was built but {} of {} vertice(s) do not match the requested world positions".format(
                len(mismatches), len(world_vertices)
            ),
            node=component_identity(node),
            rolled_back=delete_node(rt, node),
            mismatches=mismatches[:20],
            mismatch_count=len(mismatches),
        )

    counts = component_counts(rt, node)
    data: Dict[str, Any] = {
        "node": component_identity(node),
        "created_with": details.get("created_with"),
        "converted_with": details.get("converted_with"),
        "vertex_count": counts["vertex_count"],
        "edge_count": counts["edge_count"],
        "face_count": counts["face_count"],
        "requested_face_count": len(validated_faces),
        "triangulated_faces": triangulated,
    }
    warnings: List[str] = []
    if triangulated:
        warnings.append(
            "{} of {} face(s) had more than three vertices and were fan-triangulated, so the node "
            "carries {} triangle(s)".format(len(triangulated), len(validated_faces), len(triangles))
        )
    if counts.get("errors"):
        warnings.extend(
            "{}: {}".format(key, detail) for key, detail in sorted(counts["errors"].items())
        )
    if warnings:
        data["warnings"] = warnings
    return mesh_success(
        "Created {} with {} vertice(s) and {} face(s)".format(
            str(getattr(node, "name", "<node>")), counts["vertex_count"], counts["face_count"]
        ),
        **data
    )
