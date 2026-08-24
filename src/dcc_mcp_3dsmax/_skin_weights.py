"""Bounded, readback-verified access to the native 3ds Max Skin modifier."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._rigging_utils import rig_error, rig_success
from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_object

SKIN_WEIGHTS_SCHEMA = "dcc-mcp/skin-weights@1"
MAX_VERTICES = 100_000
MAX_INFLUENCES = 256


def get_skin_weights(
    runtime: Any,
    *,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    vertex_indices: Optional[Sequence[int]] = None,
    modifier_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Read native Skin weights for explicit vertices."""
    resolved, node = resolve_node_object(runtime, node_name=node_name, handle=handle)
    if node is None:
        return rig_error("Could not resolve skinned node", node=resolved)
    binding, error = _skin_binding(runtime, node, modifier_name=modifier_name)
    if error is not None:
        return error
    skin, skin_ops, vertex_count = binding
    indices, error = _vertex_indices(vertex_indices, vertex_count=vertex_count)
    if error is not None:
        return error
    try:
        rows = [_read_vertex(skin_ops, skin, vertex) for vertex in indices]
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return rig_error("Could not read native Skin weights", reason=str(exc))
    return rig_success(
        "Read native Skin weights",
        weights=_weights_payload(node, skin, vertex_count, rows),
    )


def set_skin_weights(
    runtime: Any,
    *,
    vertices: Sequence[Dict[str, Any]],
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    modifier_name: Optional[str] = None,
    normalize: bool = True,
) -> Dict[str, Any]:
    """Replace explicit vertex influences and roll back if readback fails."""
    resolved, node = resolve_node_object(runtime, node_name=node_name, handle=handle)
    if node is None:
        return rig_error("Could not resolve skinned node", node=resolved)
    binding, error = _skin_binding(runtime, node, modifier_name=modifier_name)
    if error is not None:
        return error
    skin, skin_ops, vertex_count = binding
    plans, error = _validate_vertices(
        skin_ops,
        skin,
        vertices,
        vertex_count=vertex_count,
        normalize=normalize,
    )
    if error is not None:
        return error

    snapshots = {vertex: _read_vertex(skin_ops, skin, vertex) for vertex, _ids, _weights in plans}
    changed = 0
    try:
        for vertex, bone_ids, weights in plans:
            _replace_vertex(skin_ops, skin, vertex, bone_ids, weights, normalize=normalize)
            actual = _read_vertex(skin_ops, skin, vertex)
            if not _weights_match(actual, bone_ids, weights):
                raise RuntimeError("native Skin readback did not match vertex {}".format(vertex))
            changed += 1
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        rollback_errors = _restore_vertices(skin_ops, skin, snapshots)
        return rig_error(
            "Could not set and verify the complete Skin weight batch",
            reason=str(exc),
            changed_vertex_count=changed,
            rolled_back=not rollback_errors,
            rollback_errors=rollback_errors,
        )

    rows = [_read_vertex(skin_ops, skin, vertex) for vertex, _ids, _weights in plans]
    return rig_success(
        "Set and verified native Skin weights",
        changed_vertex_count=changed,
        weights=_weights_payload(node, skin, vertex_count, rows),
    )


def copy_weights(
    runtime: Any,
    *,
    source_name: Optional[str] = None,
    source_handle: Optional[int] = None,
    target_name: Optional[str] = None,
    target_handle: Optional[int] = None,
    vertex_indices: Optional[Sequence[int]] = None,
    source_modifier_name: Optional[str] = None,
    target_modifier_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Copy exact-topology weights while mapping target bones by name."""
    source_result, source = resolve_node_object(runtime, node_name=source_name, handle=source_handle)
    if source is None:
        return rig_error("Could not resolve source skinned node", node=source_result)
    target_result, target = resolve_node_object(runtime, node_name=target_name, handle=target_handle)
    if target is None:
        return rig_error("Could not resolve target skinned node", node=target_result)
    if node_identity(source) == node_identity(target):
        return rig_error("Source and target skinned nodes must be different")

    source_identity = node_identity(source)
    source_weights = get_skin_weights(
        runtime,
        node_name=source_identity["node_name"] if source_identity["object_id"] is None else None,
        handle=source_identity["object_id"],
        vertex_indices=vertex_indices,
        modifier_name=source_modifier_name,
    )
    if not source_weights.get("success"):
        return source_weights
    target_binding, error = _skin_binding(runtime, target, modifier_name=target_modifier_name)
    if error is not None:
        return error
    target_skin, target_ops, target_vertex_count = target_binding
    source_payload = source_weights["data"]["weights"]
    if vertex_indices is None and source_payload["vertex_count"] != target_vertex_count:
        return rig_error(
            "Source and target Skin topology counts differ",
            source_vertex_count=source_payload["vertex_count"],
            target_vertex_count=target_vertex_count,
        )
    bone_ids, error = _bone_ids_by_name(target_ops, target_skin)
    if error is not None:
        return error
    rows = []
    for source_row in source_payload["vertices"]:
        if source_row["vertex"] > target_vertex_count:
            return rig_error("Source vertex is outside the target Skin vertex range")
        influences = []
        for influence in source_row["influences"]:
            bone_name = influence["bone_name"]
            if bone_name not in bone_ids:
                return rig_error("Target Skin is missing source bone: {}".format(bone_name))
            influences.append({"bone_id": bone_ids[bone_name], "weight": influence["weight"]})
        rows.append({"vertex": source_row["vertex"], "influences": influences})
    target_identity = node_identity(target)
    result = set_skin_weights(
        runtime,
        node_name=target_identity["node_name"] if target_identity["object_id"] is None else None,
        handle=target_identity["object_id"],
        modifier_name=target_modifier_name,
        vertices=rows,
    )
    if result.get("success"):
        result["message"] = "Copied and verified native Skin weights"
        result["data"]["source"] = source_identity
    return result


def _skin_binding(
    runtime: Any, node: Any, *, modifier_name: Optional[str]
) -> Tuple[Optional[Tuple[Any, Any, int]], Optional[Dict[str, Any]]]:
    skin_ops = getattr(runtime, "skinOps", None)
    if skin_ops is None:
        return None, rig_error("3ds Max skinOps is unavailable")
    try:
        modifiers = list(node.modifiers)
    except Exception:  # noqa: BLE001
        modifiers = []
    matches = []
    for modifier in modifiers:
        name = str(getattr(modifier, "name", "") or type(modifier).__name__)
        type_name = type(modifier).__name__
        if modifier_name is not None:
            if name == modifier_name:
                matches.append(modifier)
        else:
            class_of = getattr(runtime, "classOf", None)
            try:
                native_type = str(class_of(modifier)) if callable(class_of) else ""
            except Exception:  # noqa: BLE001
                native_type = ""
            normalized_types = {value.lower().replace("_", "").replace(" ", "") for value in (type_name, native_type)}
            if "skin" in normalized_types or (
                name.lower() == "skin" and not any("skinwrap" in value for value in normalized_types)
            ):
                matches.append(modifier)
    if not matches:
        return None, rig_error("No matching Skin modifier was found", modifier_name=modifier_name)
    if len(matches) > 1:
        return None, rig_error("Multiple Skin modifiers matched; modifier_name is required")
    skin = matches[0]
    get_count = getattr(skin_ops, "GetNumberVertices", None)
    if not callable(get_count):
        return None, rig_error("skinOps.GetNumberVertices is unavailable")
    try:
        vertex_count = int(get_count(skin))
    except (RuntimeError, TypeError, ValueError) as exc:
        return None, rig_error("Could not inspect Skin vertex count", reason=str(exc))
    if vertex_count < 1 or vertex_count > MAX_VERTICES:
        return None, rig_error("Skin vertex count is outside the supported bound", vertex_count=vertex_count)
    return (skin, skin_ops, vertex_count), None


def _vertex_indices(
    values: Optional[Sequence[int]], *, vertex_count: int
) -> Tuple[List[int], Optional[Dict[str, Any]]]:
    if values is None:
        return list(range(1, vertex_count + 1)), None
    if not isinstance(values, (list, tuple)) or not values:
        return [], rig_error("vertex_indices must contain at least one vertex")
    if len(values) > MAX_VERTICES:
        return [], rig_error("Too many vertex indices", maximum=MAX_VERTICES)
    indices = []
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= vertex_count:
            return [], rig_error("Vertex index is outside the Skin vertex range", vertex=value)
        if value in indices:
            return [], rig_error("Duplicate vertex index", vertex=value)
        indices.append(value)
    return indices, None


def _validate_vertices(
    skin_ops: Any,
    skin: Any,
    vertices: Sequence[Dict[str, Any]],
    *,
    vertex_count: int,
    normalize: bool,
) -> Tuple[List[Tuple[int, List[int], List[float]]], Optional[Dict[str, Any]]]:
    if not isinstance(vertices, (list, tuple)) or not vertices:
        return [], rig_error("vertices must contain at least one weight row")
    if len(vertices) > MAX_VERTICES:
        return [], rig_error("Too many Skin weight rows", maximum=MAX_VERTICES)
    get_bones = getattr(skin_ops, "GetNumberBones", None)
    if not callable(get_bones):
        return [], rig_error("skinOps.GetNumberBones is unavailable")
    try:
        bone_count = int(get_bones(skin))
    except (RuntimeError, TypeError, ValueError) as exc:
        return [], rig_error("Could not inspect Skin bone count", reason=str(exc))
    plans = []
    seen_vertices = set()
    for row_index, row in enumerate(vertices):
        if not isinstance(row, dict):
            return [], rig_error("Each Skin weight row must be an object", row_index=row_index)
        vertex = row.get("vertex")
        if not isinstance(vertex, int) or isinstance(vertex, bool) or not 1 <= vertex <= vertex_count:
            return [], rig_error("Skin weight vertex is outside the valid range", row_index=row_index)
        if vertex in seen_vertices:
            return [], rig_error("Duplicate Skin weight vertex", vertex=vertex)
        seen_vertices.add(vertex)
        influences = row.get("influences")
        if not isinstance(influences, list) or not influences or len(influences) > MAX_INFLUENCES:
            return [], rig_error("Skin influences must be a bounded non-empty list", vertex=vertex)
        bone_ids = []
        weights = []
        for influence in influences:
            if not isinstance(influence, dict):
                return [], rig_error("Each Skin influence must be an object", vertex=vertex)
            bone_id = influence.get("bone_id")
            weight = influence.get("weight")
            if not isinstance(bone_id, int) or isinstance(bone_id, bool) or not 1 <= bone_id <= bone_count:
                return [], rig_error("Skin bone_id is outside the valid range", vertex=vertex, bone_id=bone_id)
            if bone_id in bone_ids:
                return [], rig_error("Duplicate Skin bone_id", vertex=vertex, bone_id=bone_id)
            if not _finite_number(weight) or float(weight) < 0:
                return [], rig_error("Skin weight must be a finite non-negative number", vertex=vertex)
            bone_ids.append(bone_id)
            weights.append(float(weight))
        total = sum(weights)
        if total <= 0:
            return [], rig_error("Skin influence weights must have a positive sum", vertex=vertex)
        if normalize:
            weights = [weight / total for weight in weights]
        elif not math.isclose(total, 1.0, rel_tol=1e-6, abs_tol=1e-6):
            return [], rig_error("Skin influence weights must sum to one when normalize is false", vertex=vertex)
        plans.append((vertex, bone_ids, weights))
    return plans, None


def _read_vertex(skin_ops: Any, skin: Any, vertex: int) -> Dict[str, Any]:
    influence_count = int(skin_ops.GetVertexWeightCount(skin, vertex))
    if influence_count < 0 or influence_count > MAX_INFLUENCES:
        raise ValueError("Skin influence count is outside the supported bound")
    influences = []
    for influence_index in range(1, influence_count + 1):
        bone_id = int(skin_ops.GetVertexWeightBoneID(skin, vertex, influence_index))
        weight = float(skin_ops.GetVertexWeight(skin, vertex, influence_index))
        bone_name = str(skin_ops.GetBoneName(skin, bone_id, 0))
        influences.append({"bone_id": bone_id, "bone_name": bone_name, "weight": weight})
    total = sum(row["weight"] for row in influences)
    unnormalized = _is_unnormalized(skin_ops, skin, vertex, total)
    return {
        "vertex": vertex,
        "influences": influences,
        "total_weight": total,
        "normalized": not unnormalized,
    }


def _bone_ids_by_name(skin_ops: Any, skin: Any) -> Tuple[Dict[str, int], Optional[Dict[str, Any]]]:
    get_count = getattr(skin_ops, "GetNumberBones", None)
    get_name = getattr(skin_ops, "GetBoneName", None)
    if not callable(get_count) or not callable(get_name):
        return {}, rig_error("Native Skin bone-name inspection is unavailable")
    try:
        count = int(get_count(skin))
        rows = [(str(get_name(skin, bone_id, 0)), bone_id) for bone_id in range(1, count + 1)]
    except (RuntimeError, TypeError, ValueError) as exc:
        return {}, rig_error("Could not inspect target Skin bones", reason=str(exc))
    names = [name for name, _bone_id in rows]
    if len(set(names)) != len(names):
        return {}, rig_error("Target Skin bone names are ambiguous")
    return {name: bone_id for name, bone_id in rows}, None


def _is_unnormalized(skin_ops: Any, skin: Any, vertex: int, total: float) -> bool:
    method = getattr(skin_ops, "isUnNormalizeVertex", None)
    if callable(method):
        return bool(method(skin, vertex)) or not math.isclose(total, 1.0, rel_tol=1e-6, abs_tol=1e-6)
    return not math.isclose(total, 1.0, rel_tol=1e-6, abs_tol=1e-6)


def _replace_vertex(
    skin_ops: Any,
    skin: Any,
    vertex: int,
    bone_ids: Sequence[int],
    weights: Sequence[float],
    *,
    normalize: bool,
) -> None:
    replace = getattr(skin_ops, "ReplaceVertexWeights", None)
    if not callable(replace):
        raise AttributeError("skinOps.ReplaceVertexWeights is unavailable")
    replace(skin, vertex, list(bone_ids), list(weights))
    set_unnormalized = getattr(skin_ops, "unNormalizeVertex", None)
    if callable(set_unnormalized):
        set_unnormalized(skin, vertex, not normalize)


def _restore_vertices(skin_ops: Any, skin: Any, snapshots: Dict[int, Dict[str, Any]]) -> List[str]:
    errors = []
    for vertex, row in snapshots.items():
        try:
            _replace_vertex(
                skin_ops,
                skin,
                vertex,
                [item["bone_id"] for item in row["influences"]],
                [item["weight"] for item in row["influences"]],
                normalize=row["normalized"],
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            errors.append("vertex {}: {}".format(vertex, exc))
    return errors


def _weights_match(actual: Dict[str, Any], bone_ids: Sequence[int], weights: Sequence[float]) -> bool:
    rows = actual["influences"]
    actual_weights = {row["bone_id"]: row["weight"] for row in rows}
    expected_weights = dict(zip(bone_ids, weights))
    if set(actual_weights) != set(expected_weights):
        return False
    return all(
        math.isclose(actual_weights[bone_id], expected, rel_tol=1e-6, abs_tol=1e-6)
        for bone_id, expected in expected_weights.items()
    )


def _weights_payload(node: Any, skin: Any, vertex_count: int, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema": SKIN_WEIGHTS_SCHEMA,
        "node": node_identity(node),
        "modifier_name": str(getattr(skin, "name", "Skin")),
        "vertex_count": vertex_count,
        "sampled_vertex_count": len(rows),
        "vertices": rows,
        "unnormalized_vertex_count": sum(not row["normalized"] for row in rows),
    }


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
