"""Typed controller pose capture and readback-verified restore."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._rigging_utils import resolve_rig_targets, rig_error, rig_success
from dcc_mcp_3dsmax._scene_utils import node_identity, point3_to_list, resolve_node_object

POSE_SCHEMA = "dcc-mcp/pose@1"
MAX_POSE_NODES = 512
TRANSFORMS = ("position", "rotation", "scale")


def save_pose(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Capture explicit node transforms as JSON-safe pose data."""
    targets = resolve_rig_targets(
        runtime,
        node_names=node_names,
        handles=handles,
        use_selection=use_selection,
    )
    if not targets.get("success"):
        return targets
    nodes = list(targets["objects"])
    if len(nodes) > MAX_POSE_NODES:
        return rig_error("Pose target count exceeds the supported bound", maximum=MAX_POSE_NODES)
    payload, error = _pose_payload(nodes)
    if error is not None:
        return error
    return rig_success(
        "Captured controller pose",
        pose_data=payload,
    )


def load_pose(runtime: Any, pose_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a complete pose before mutation, then verify each transform."""
    plans, error = _validate_pose(runtime, pose_data)
    if error is not None:
        return error
    snapshots = {}
    for node, _transforms in plans:
        snapshots[id(node)] = _read_transforms(node)[0]

    changed = 0
    try:
        for node, transforms in plans:
            _apply_transforms(runtime, node, transforms)
            actual, read_error = _read_transforms(node)
            if read_error is not None or not _transforms_match(actual, transforms):
                raise RuntimeError("host transform readback did not match {}".format(node.name))
            changed += 1
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        rollback_errors = []
        for node, _transforms in plans:
            try:
                _apply_transforms(runtime, node, snapshots[id(node)])
            except (AttributeError, RuntimeError, TypeError, ValueError) as rollback_exc:
                rollback_errors.append("{}: {}".format(node.name, rollback_exc))
        return rig_error(
            "Could not load and verify the complete controller pose",
            reason=str(exc),
            changed_node_count=changed,
            rolled_back=not rollback_errors,
            rollback_errors=rollback_errors,
        )
    payload, payload_error = _pose_payload([node for node, _value in plans])
    if payload_error is not None:
        return payload_error
    return rig_success(
        "Loaded and verified controller pose",
        changed_node_count=changed,
        pose_data=payload,
    )


def _pose_payload(nodes: Sequence[Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    rows = []
    for node in nodes:
        transforms, error = _read_transforms(node)
        if error is not None:
            return {}, error
        rows.append({"node": node_identity(node), "transforms": transforms})
    return {"schema": POSE_SCHEMA, "nodes": rows}, None


def _validate_pose(
    runtime: Any, payload: Dict[str, Any]
) -> Tuple[List[Tuple[Any, Dict[str, List[float]]]], Optional[Dict[str, Any]]]:
    if not isinstance(payload, dict) or payload.get("schema") != POSE_SCHEMA:
        return [], rig_error("pose_data must use schema {}".format(POSE_SCHEMA))
    rows = payload.get("nodes")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_POSE_NODES:
        return [], rig_error("pose_data nodes must be a bounded non-empty list")
    plans = []
    seen = set()
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("node"), dict):
            return [], rig_error("Each pose node must include a node identity", row_index=row_index)
        identity = row["node"]
        node_name = identity.get("node_name")
        object_id = identity.get("object_id")
        resolved, node = resolve_node_object(runtime, node_name=node_name, handle=object_id)
        if node is None:
            return [], rig_error("Pose target node is missing: {}".format(node_name), node=resolved)
        if id(node) in seen:
            return [], rig_error("Duplicate pose target node", node=node_identity(node))
        seen.add(id(node))
        transforms = row.get("transforms")
        if not isinstance(transforms, dict) or set(transforms) != set(TRANSFORMS):
            return [], rig_error("Pose transforms must contain position, rotation, and scale", row_index=row_index)
        converted = {}
        for property_name in TRANSFORMS:
            value = transforms[property_name]
            if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(_finite(item) for item in value):
                return [], rig_error("Pose transform values must contain three finite numbers", row_index=row_index)
            converted[property_name] = [float(item) for item in value]
        plans.append((node, converted))
    return plans, None


def _read_transforms(node: Any) -> Tuple[Dict[str, List[float]], Optional[Dict[str, Any]]]:
    values = {}
    for property_name in TRANSFORMS:
        value = point3_to_list(getattr(node, property_name, None))
        if value is None:
            value = point3_to_list(getattr(getattr(node, property_name, None), "value", None))
        if value is None:
            return {}, rig_error("Could not read node transform", node=node_identity(node), property=property_name)
        values[property_name] = value
    return values, None


def _apply_transforms(runtime: Any, node: Any, transforms: Dict[str, Sequence[float]]) -> None:
    for property_name in TRANSFORMS:
        constructor_name = "EulerAngles" if property_name == "rotation" else "Point3"
        constructor = getattr(runtime, constructor_name, None)
        value = transforms[property_name]
        converted = constructor(*value) if callable(constructor) else list(value)
        setattr(node, property_name, converted)


def _transforms_match(actual: Dict[str, Sequence[float]], expected: Dict[str, Sequence[float]]) -> bool:
    return all(
        all(math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-6) for left, right in zip(actual[name], expected[name]))
        for name in TRANSFORMS
    )


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
