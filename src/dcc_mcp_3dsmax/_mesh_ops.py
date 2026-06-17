"""Helpers for 3ds Max mesh operation skill scripts."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_object, resolve_node_objects


def mesh_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def mesh_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def resolve_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Resolve explicit node targets or an explicitly requested selection."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001
            selected = []
        if not selected:
            return mesh_error("Current selection is empty", nodes=[], objects=[])
        return {
            "success": True,
            "status": "success",
            "message": "Resolved selected nodes",
            "nodes": [node_identity(node) for node in selected],
            "objects": selected,
        }
    result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
    if not result.get("success"):
        return mesh_error(result["message"], errors=result.get("errors", []), objects=[])
    result["status"] = "success"
    return result


def topology_summary(runtime: Any, node: Any) -> Dict[str, Any]:
    """Return best-effort mesh topology counts for one node."""
    return {
        "node": node_identity(node),
        "vertex_count": _count(runtime, node, "verts", ("vertex_count", "numVerts", "verts")),
        "edge_count": _count(runtime, node, "edges", ("edge_count", "numEdges", "edges")),
        "face_count": _count(runtime, node, "faces", ("face_count", "numFaces", "faces")),
    }


def selected_topology_summary(runtime: Any) -> Dict[str, Any]:
    """Return topology summaries for the current selection."""
    targets = resolve_targets(runtime, use_selection=True)
    if not targets.get("success"):
        return targets
    rows = [topology_summary(runtime, node) for node in targets["objects"]]
    return mesh_success("Summarized selected mesh topology", nodes=rows, count=len(rows))


def smoothing_group_summary(runtime: Any, node: Any, face_indices: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """Return smoothing group counts for one mesh node."""
    faces = list(face_indices or range(1, (_face_count(runtime, node) or 0) + 1))
    groups: Dict[str, int] = {}
    for face_index in faces:
        value = _get_smoothing_group(runtime, node, int(face_index))
        key = str(value if value is not None else 0)
        groups[key] = groups.get(key, 0) + 1
    return {
        "node": node_identity(node),
        "face_count": len(faces),
        "groups": groups,
    }


def modifier_stack_summary(node: Any) -> Dict[str, Any]:
    """Return modifier stack metadata for one node."""
    modifiers = []
    try:
        raw_modifiers = list(node.modifiers)
    except Exception:  # noqa: BLE001
        raw_modifiers = []
    for index, modifier in enumerate(raw_modifiers, start=1):
        modifiers.append(
            {
                "index": index,
                "name": str(getattr(modifier, "name", "") or type(modifier).__name__),
                "type": type(modifier).__name__,
                "enabled": bool(getattr(modifier, "enabled", True)),
            }
        )
    return {"node": node_identity(node), "modifiers": modifiers, "count": len(modifiers)}


def changed_summary(runtime: Any, nodes: Iterable[Any]) -> List[Dict[str, Any]]:
    """Return identity plus topology for changed nodes."""
    return [topology_summary(runtime, node) for node in nodes]


def add_modifier(
    runtime: Any, node: Any, constructor_names: Sequence[str], **attrs: Any
) -> Tuple[Optional[Any], List[str]]:
    """Create and attach the first available modifier constructor."""
    warnings = []
    modifier = None
    for name in constructor_names:
        factory = getattr(runtime, name, None)
        if callable(factory):
            try:
                modifier = factory()
                break
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not create modifier {}: {}".format(name, exc))
    if modifier is None:
        return None, warnings or ["No supported modifier constructor was available"]
    for key, value in attrs.items():
        try:
            setattr(modifier, key, value)
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not set modifier attribute {}: {}".format(key, exc))
    add = getattr(runtime, "addModifier", None)
    if callable(add):
        add(node, modifier)
    else:
        try:
            node.modifiers.append(modifier)
        except Exception as exc:  # noqa: BLE001
            return modifier, warnings + ["Could not attach modifier: {}".format(exc)]
    return modifier, warnings


def triangulate_node(runtime: Any, node: Any) -> List[str]:
    """Triangulate a mesh using a reversible modifier when available."""
    modifier, warnings = add_modifier(runtime, node, ("Turn_to_Mesh", "TurnToMesh"))
    if modifier is not None:
        return warnings
    convert = getattr(runtime, "convertToMesh", None)
    if callable(convert):
        convert(node)
        return warnings
    return warnings + ["No triangulation operation was available"]


def cleanup_node(runtime: Any, node: Any, weld_threshold: Optional[float] = None) -> List[str]:
    """Run best-effort mesh cleanup operations."""
    cleanup = getattr(runtime, "cleanupMesh", None)
    if callable(cleanup):
        cleanup(node, weld_threshold)
        return []
    modifier, warnings = add_modifier(runtime, node, ("STL_Check", "STLCheck"))
    if modifier is not None:
        return warnings
    return warnings + ["No mesh cleanup operation was available"]


def attach_sources(runtime: Any, target: Any, sources: Sequence[Any]) -> List[str]:
    """Attach source meshes into a target mesh."""
    warnings = []
    attach = getattr(runtime, "attach", None)
    poly_op = getattr(runtime, "polyOp", None)
    poly_attach = getattr(poly_op, "attach", None) if poly_op is not None else None
    for source in sources:
        try:
            if callable(attach):
                attach(target, source)
            elif callable(poly_attach):
                poly_attach(target, source)
            else:
                raise RuntimeError("No attach operation was available")
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not attach {}: {}".format(getattr(source, "name", "<node>"), exc))
    return warnings


def detach_faces(
    runtime: Any,
    node: Any,
    *,
    face_indices: Optional[Sequence[int]] = None,
    use_current_face_selection: bool = False,
    detach_name: str = "DetachedMesh",
) -> Tuple[Optional[Any], List[str]]:
    """Detach faces into a new node."""
    faces = _face_indices(node, face_indices, use_current_face_selection)
    if not faces:
        return None, ["face_indices or use_current_face_selection=true is required"]
    poly_op = getattr(runtime, "polyOp", None)
    detach = getattr(poly_op, "detachFaces", None) if poly_op is not None else None
    if callable(detach):
        try:
            return detach(node, faces, asNode=True, name=detach_name), []
        except TypeError:
            return detach(node, faces), []
        except Exception as exc:  # noqa: BLE001
            return None, ["Could not detach faces: {}".format(exc)]
    runtime_detach = getattr(runtime, "detachFaces", None)
    if callable(runtime_detach):
        try:
            return runtime_detach(node, faces, detach_name), []
        except Exception as exc:  # noqa: BLE001
            return None, ["Could not detach faces: {}".format(exc)]
    return None, ["No detach faces operation was available"]


def apply_subdivision(runtime: Any, node: Any, iterations: int, render_iterations: Optional[int]) -> List[str]:
    """Apply a subdivision modifier."""
    attrs = {"iterations": int(iterations)}
    if render_iterations is not None:
        attrs["renderIterations"] = int(render_iterations)
    modifier, warnings = add_modifier(runtime, node, ("TurboSmooth", "MeshSmooth"), **attrs)
    if modifier is None:
        return warnings + ["No subdivision modifier was available"]
    return warnings


def create_proxy(
    runtime: Any, node: Any, *, reduction_percent: float, name_suffix: str
) -> Tuple[Optional[Any], List[str]]:
    """Duplicate a node and apply a ProOptimizer-style modifier."""
    copy = getattr(runtime, "copy", None)
    if not callable(copy):
        return None, ["No copy operation was available"]
    try:
        proxy = copy(node)
        proxy.name = "{}{}".format(getattr(node, "name", "mesh"), name_suffix)
    except Exception as exc:  # noqa: BLE001
        return None, ["Could not duplicate proxy mesh: {}".format(exc)]
    modifier, warnings = add_modifier(runtime, proxy, ("ProOptimizer",), VertexPercent=float(reduction_percent))
    if modifier is None:
        warnings.append("Proxy was duplicated without a reduction modifier")
    return proxy, warnings


def set_explicit_normals(runtime: Any, node: Any, normal: Sequence[float]) -> List[str]:
    """Set explicit normal data using host helpers or an Edit Normals modifier."""
    vector = [float(normal[0]), float(normal[1]), float(normal[2])]
    meshop = getattr(runtime, "meshop", None)
    setter = getattr(meshop, "setNormal", None) if meshop is not None else None
    if callable(setter):
        try:
            setter(node, vector)
            return []
        except Exception as exc:  # noqa: BLE001
            return ["Could not set normals through meshop: {}".format(exc)]
    modifier, warnings = add_modifier(runtime, node, ("Edit_Normals", "EditNormals"), explicitNormal=vector)
    try:
        node.explicit_normal = vector
    except Exception:  # noqa: BLE001
        pass
    if modifier is None:
        warnings.append("Stored explicit normal marker on the node only")
    return warnings


def clear_explicit_normals(runtime: Any, node: Any) -> List[str]:
    """Clear explicit normal data where host helpers are available."""
    meshop = getattr(runtime, "meshop", None)
    clearer = getattr(meshop, "clearExplicitNormals", None) if meshop is not None else None
    if callable(clearer):
        try:
            clearer(node)
            return []
        except Exception as exc:  # noqa: BLE001
            return ["Could not clear normals through meshop: {}".format(exc)]
    try:
        node.explicit_normal = None
    except Exception as exc:  # noqa: BLE001
        return ["Could not clear explicit normal marker: {}".format(exc)]
    return []


def assign_smoothing_group(
    runtime: Any,
    node: Any,
    *,
    smoothing_group: int,
    face_indices: Optional[Sequence[int]] = None,
) -> List[str]:
    """Assign a smoothing group to explicit faces or every face."""
    faces = list(face_indices or range(1, (_face_count(runtime, node) or 0) + 1))
    if not faces:
        return ["No faces were available for smoothing group assignment"]
    poly_op = getattr(runtime, "polyOp", None)
    setter = getattr(poly_op, "setFaceSmoothGroup", None) if poly_op is not None else None
    warnings = []
    if callable(setter):
        for face_index in faces:
            try:
                setter(node, int(face_index), int(smoothing_group))
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not set smoothing group on face {}: {}".format(face_index, exc))
        return warnings
    groups = getattr(node, "smoothing_groups", None)
    if groups is None:
        groups = {}
        try:
            setattr(node, "smoothing_groups", groups)
        except Exception as exc:  # noqa: BLE001
            return ["Could not store smoothing group data: {}".format(exc)]
    for face_index in faces:
        groups[int(face_index)] = int(smoothing_group)
    return warnings


def resolve_one(
    runtime: Any, *, node_name: Optional[str] = None, handle: Optional[int] = None
) -> Tuple[Dict[str, Any], Any]:
    """Resolve one target node and normalize the error envelope."""
    result, node = resolve_node_object(runtime, node_name=node_name, handle=handle)
    if node is None:
        return mesh_error(result.get("message", "Node could not be resolved"), resolution=result), None
    return result, node


def _count(runtime: Any, node: Any, kind: str, attrs: Sequence[str]) -> int:
    poly_op = getattr(runtime, "polyOp", None)
    method_name = {"verts": "getNumVerts", "edges": "getNumEdges", "faces": "getNumFaces"}[kind]
    method = getattr(poly_op, method_name, None) if poly_op is not None else None
    if callable(method):
        try:
            return int(method(node))
        except Exception:  # noqa: BLE001
            pass
    for attr in attrs:
        value = getattr(node, attr, None)
        if isinstance(value, int):
            return value
        try:
            return len(value)
        except Exception:  # noqa: BLE001
            continue
    return 0


def _face_count(runtime: Any, node: Any) -> int:
    return _count(runtime, node, "faces", ("face_count", "numFaces", "faces"))


def _get_smoothing_group(runtime: Any, node: Any, face_index: int) -> Optional[int]:
    groups = getattr(node, "smoothing_groups", None)
    if isinstance(groups, dict) and face_index in groups:
        return int(groups[face_index])
    poly_op = getattr(runtime, "polyOp", None)
    getter = getattr(poly_op, "getFaceSmoothGroup", None) if poly_op is not None else None
    if callable(getter):
        try:
            return int(getter(node, face_index))
        except Exception:  # noqa: BLE001
            return None
    return None


def _face_indices(node: Any, face_indices: Optional[Sequence[int]], use_current_face_selection: bool) -> List[int]:
    if face_indices:
        return [int(index) for index in face_indices]
    if use_current_face_selection:
        try:
            return [int(index) for index in getattr(node, "selected_faces")]
        except Exception:  # noqa: BLE001
            return []
    return []
