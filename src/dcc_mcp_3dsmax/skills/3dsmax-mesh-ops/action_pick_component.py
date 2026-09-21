"""Map a viewport image position - or an explicit world ray - onto a component.

A pick is the bridge between "the agent is looking at a rendered viewport" and
"the agent has a component index it can edit". Two stages compose it:

1. **A world ray.** Either supplied directly as ``ray_origin`` /
   ``ray_direction``, or mapped from an image position by the host. The mapping
   is a host capability, not something this adapter can derive: when no entry
   point is exposed the call **fails and names every candidate it probed**,
   because fabricating a projection would return a plausible ray and therefore a
   plausible but wrong component. Supplying the ray explicitly always works.

2. **A ray cast.** ``intersectRayEx`` reports the face it hit, which is exactly
   what a component pick needs; ``intersectRay`` reports only a point, and the
   face is then resolved by proximity and flagged ``face_reported: false`` so the
   caller knows the index was inferred rather than named by the host.

The result is then **verified**: the hit point is checked against the reported
face's plane and bounding radius. A host that names a face the point does not
lie on produces ``face_verified: false`` plus a warning instead of a confident
answer. A clean miss is reported as ``hit: false`` - a miss is information, not
an error, so it is a successful result that says nothing was hit.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._curve_utils import resolve_shape, validated_name, validated_number
from dcc_mcp_3dsmax._poly_utils import (
    component_identity,
    ensure_poly,
    face_center,
    face_edges,
    intersect_scene_ray,
    make_ray,
    mesh_error,
    mesh_success,
    nearest_vertex_on_face,
    num_faces,
    read_vert,
    screen_ray,
    to_object_space,
    verify_face_contains_point,
)
from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes
from dcc_mcp_3dsmax.api import get_runtime, with_max

# Which component the pick resolves to. ``face`` is what the host reports;
# ``vertex`` and ``edge`` are derived from the hit face by proximity.
COMPONENT_KINDS = ("face", "vertex", "edge")

# Upper bound on candidate nodes for an unscoped pick.
MAX_CANDIDATES = 256

# Image-space units accepted for the position.
IMAGE_SPACES = ("pixels", "normalized")


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _validate_triple(value: Any, name: str) -> List[float]:
    """Validate an XYZ triple of finite numbers."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise ValueError("{} must be an array of three finite numbers".format(name))
    components: List[float] = []
    for offset, component in enumerate(value):
        number = validated_number(component, "{}[{}]".format(name, offset))
        components.append(float(number))
    return components


def _candidates(rt: Any, node_name: Optional[str], handle: Optional[int], use_selection: bool):
    """Return ``(nodes, error)`` for the nodes the ray is cast at."""
    if node_name is not None or handle is not None:
        node, error = resolve_shape(rt, node_name=node_name, handle=handle)
        if error:
            return None, error
        return [node], None

    if use_selection:
        try:
            selected = list(rt.selection)
        except Exception as exc:  # noqa: BLE001 - an unreadable selection is a failure.
            return None, mesh_error("the current selection could not be read: {}".format(exc))
        if not selected:
            return None, mesh_error("use_selection is true but the current selection is empty")
        return selected[:MAX_CANDIDATES], None

    nodes = [node for node in iter_scene_nodes(rt) if ensure_poly(rt, node) is None]
    if not nodes:
        return None, mesh_error(
            "the scene contains no Editable Poly node to pick against; pass node_name or handle"
        )
    return nodes[:MAX_CANDIDATES], None


def _nearest_edge(
    rt: Any, node: Any, face_index: int, point: Sequence[float]
) -> Optional[int]:
    """Return the edge of ``face_index`` whose midpoint is closest to ``point``."""
    ring, error = face_edges(rt, node, face_index)
    if error or not ring:
        return None
    from dcc_mcp_3dsmax._poly_utils import edge_verts

    best: Optional[int] = None
    best_distance = None
    for edge_index in ring:
        pair, pair_error = edge_verts(rt, node, edge_index)
        if pair_error or not pair:
            continue
        corners: List[List[float]] = []
        for vertex_index in pair:
            vertex, vertex_error = read_vert(rt, node, vertex_index)
            if vertex_error:
                corners = []
                break
            corners.append(vertex["object"])
        if len(corners) != 2:
            continue
        midpoint = [(corners[0][axis] + corners[1][axis]) / 2.0 for axis in range(3)]
        distance = math.sqrt(sum((float(point[axis]) - midpoint[axis]) ** 2 for axis in range(3)))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = int(edge_index)
    return best


@with_max
def main(
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    use_selection: bool = False,
    image_x: Optional[float] = None,
    image_y: Optional[float] = None,
    image_space: Optional[str] = None,
    ray_origin: Optional[Sequence[float]] = None,
    ray_direction: Optional[Sequence[float]] = None,
    component: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve an image position or a world ray to a face, vertex, or edge."""
    try:
        normalized_name = validated_name(node_name, "node_name")
        normalized_space = str(image_space or "pixels").strip().lower()
        if normalized_space not in IMAGE_SPACES:
            raise ValueError("image_space must be one of {}".format(", ".join(IMAGE_SPACES)))
        normalized_kind = str(component or "face").strip().lower()
        if normalized_kind not in COMPONENT_KINDS:
            raise ValueError("component must be one of {}".format(", ".join(COMPONENT_KINDS)))

        has_image = image_x is not None or image_y is not None
        has_ray = ray_origin is not None or ray_direction is not None
        if has_image and has_ray:
            raise ValueError("pass either image_x/image_y or ray_origin/ray_direction, not both")
        if not has_image and not has_ray:
            raise ValueError("image_x/image_y or ray_origin/ray_direction is required")

        origin: Optional[List[float]] = None
        direction: Optional[List[float]] = None
        # Assigned in the image branch below; the explicit-ray branch never
        # reads them.
        position_x = 0.0
        position_y = 0.0
        if has_ray:
            if ray_origin is None or ray_direction is None:
                raise ValueError("ray_origin and ray_direction are both required")
            origin = _validate_triple(ray_origin, "ray_origin")
            direction = _validate_triple(ray_direction, "ray_direction")
        else:
            if image_x is None or image_y is None:
                raise ValueError("image_x and image_y are both required")
            position_x = validated_number(image_x, "image_x")
            position_y = validated_number(image_y, "image_y")
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()

    if origin is not None:
        ray, ray_error = make_ray(rt, origin, direction)
        if ray_error:
            return mesh_error(ray_error)
        ray_source = "explicit"
        probed: List[str] = []
    else:
        ray, ray_error, probed = screen_ray(rt, float(position_x), float(position_y))
        if ray_error:
            return mesh_error(ray_error, probed=probed)
        ray_source = "host"

    nodes, error = _candidates(rt, normalized_name, handle, use_selection)
    if error:
        return error

    hits: List[Dict[str, Any]] = []
    warnings: List[str] = []
    cast_errors: List[Dict[str, Any]] = []
    for node in nodes:
        hit, cast_error = intersect_scene_ray(rt, node, ray)
        if cast_error:
            cast_errors.append(
                {"node": component_identity(node), "error": cast_error}
            )
            continue
        if hit is None:
            continue
        ray_origin_point = _ray_origin(rt, ray)
        if ray_origin_point is not None:
            hit["distance"] = math.sqrt(
                sum((hit["point"][axis] - ray_origin_point[axis]) ** 2 for axis in range(3))
            )
        hit["node"] = node
        hits.append(hit)

    if not hits:
        payload: Dict[str, Any] = {
            "hit": False,
            "ray_source": ray_source,
            "candidates": len(nodes),
        }
        if probed:
            payload["probed"] = probed
        if cast_errors:
            payload["cast_errors"] = cast_errors
            warnings.append(
                "{} of {} candidate node(s) could not be ray-cast, so the pick only covered part of "
                "the selection".format(len(cast_errors), len(nodes))
            )
        if warnings:
            payload["warnings"] = warnings
        return mesh_success(
            "The ray did not hit any of the {} candidate node(s)".format(len(nodes)), **payload
        )

    hits.sort(key=lambda item: item.get("distance", float("inf")))
    best = hits[0]
    node = best.pop("node")

    face_index = best.get("face_index")
    if face_index is None:
        face_index, resolve_error = _resolve_face_by_proximity(rt, node, best["point"])
        if resolve_error:
            return mesh_error(resolve_error, node=component_identity(node), point=best["point"])
        warnings.append(
            "the host reported only a hit point, so the face was resolved by proximity rather than "
            "named by the ray cast; treat face_index as inferred"
        )
    face_index = int(face_index)

    local_point, map_error = to_object_space(rt, node, best["point"])
    if map_error:
        return mesh_error(map_error, node=component_identity(node))
    local = [float(local_point.x), float(local_point.y), float(local_point.z)]

    verified, verify_error = verify_face_contains_point(rt, node, face_index, local)
    if verify_error:
        # Unverifiable is not the same as wrong: report the face with an
        # explicit flag rather than either failing a good pick or claiming a
        # verified one.
        warnings.append("the hit face could not be verified: {}".format(verify_error))
        face_verified = None
    else:
        face_verified = bool(verified)
        if not verified:
            warnings.append(
                "the host named face {} but the hit point does not lie on it; re-read the node before "
                "trusting this index".format(face_index)
            )

    target: Dict[str, Any] = {
        "kind": normalized_kind,
        "face_index": face_index,
        "point": best["point"],
    }
    if normalized_kind in ("vertex", "edge"):
        vertex_index, vertex_error = nearest_vertex_on_face(rt, node, face_index, local)
        if vertex_error:
            warnings.append("the nearest vertex could not be resolved: {}".format(vertex_error))
        else:
            target["vertex_index"] = vertex_index
        if normalized_kind == "edge":
            edge_index = _nearest_edge(rt, node, face_index, local)
            if edge_index is None:
                warnings.append("the nearest edge could not be resolved on face {}".format(face_index))
            else:
                target["edge_index"] = edge_index

    data: Dict[str, Any] = {
        "hit": True,
        "node": component_identity(node),
        "component": target,
        "ray_source": ray_source,
        "entry_point": best.get("entry_point"),
        "face_reported": bool(best.get("face_reported")),
        "face_verified": face_verified,
        "distance": best.get("distance"),
        "candidates": len(nodes),
        "hits": len(hits),
    }
    if probed:
        data["probed"] = probed
    if cast_errors:
        data["cast_errors"] = cast_errors
        warnings.append(
            "{} of {} candidate node(s) could not be ray-cast".format(len(cast_errors), len(nodes))
        )
    if warnings:
        data["warnings"] = warnings
    return mesh_success(
        "Picked {} {} on {}".format(
            normalized_kind, face_index, str(getattr(node, "name", "<node>"))
        ),
        **data
    )


def _ray_origin(rt: Any, ray: Any) -> Optional[List[float]]:
    """Read the origin of a host ray, or None when it cannot be read."""
    origin = getattr(ray, "pos", None)
    if origin is None:
        origin = getattr(ray, "position", None)
    if origin is None:
        return None
    for axis in ("x", "y", "z"):
        if getattr(origin, axis, None) is None:
            return None
    return [float(origin.x), float(origin.y), float(origin.z)]


def _resolve_face_by_proximity(rt: Any, node: Any, world_point: Sequence[float]):
    """Find the face whose centre is closest to ``world_point``."""
    local_point, map_error = to_object_space(rt, node, world_point)
    if map_error:
        return None, map_error
    local = [float(local_point.x), float(local_point.y), float(local_point.z)]
    total, error = num_faces(rt, node)
    if error:
        return None, error
    best: Optional[int] = None
    best_distance = None
    for face_index in range(1, (total or 0) + 1):
        centre, centre_error = face_center(rt, node, face_index)
        if centre_error:
            continue
        distance = math.sqrt(sum((local[axis] - centre[axis]) ** 2 for axis in range(3)))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = face_index
    if best is None:
        return None, "no face centre could be read, so the hit face cannot be resolved"
    return best, None
