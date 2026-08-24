"""Typed, host-readback-verified transform constraints."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from dcc_mcp_3dsmax._rigging_utils import _construct_runtime_object, _set_optional_attr, rig_error, rig_success
from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_object

CONSTRAINT_CONSTRUCTORS: Dict[str, Tuple[str, ...]] = {
    "position": ("Position_Constraint", "PositionConstraint"),
    "orientation": ("Orientation_Constraint", "OrientationConstraint"),
    "look_at": ("LookAt_Constraint", "LookAtConstraint"),
    "path": ("Path_Constraint", "PathConstraint"),
    "parent": ("Link_Constraint", "LinkConstraint"),
}

TYPED_CONSTRAINTS = {
    "point": "position",
    "orient": "orientation",
    "aim": "look_at",
    "parent": "parent",
}

_MISSING = object()
_PARENT_IDENTITY = {
    "position": (0.0, 0.0, 0.0),
    "rotation": (0.0, 0.0, 0.0, 1.0),
    "scale": (1.0, 1.0, 1.0),
}


def set_constraint_target(
    runtime: Any,
    *,
    constrained_name: Optional[str] = None,
    constrained_handle: Optional[int] = None,
    target_name: Optional[str] = None,
    target_handle: Optional[int] = None,
    constraint_type: str,
    weight: float = 100.0,
    maintain_offset: bool = False,
) -> Dict[str, Any]:
    """Create or update a transform constraint and verify its target."""
    if constraint_type not in CONSTRAINT_CONSTRUCTORS:
        return rig_error("Unsupported constraint_type", constraint_type=constraint_type)
    if (
        not isinstance(weight, (int, float))
        or isinstance(weight, bool)
        or not math.isfinite(float(weight))
        or not 0 <= float(weight) <= 100
    ):
        return rig_error("Constraint weight must be a finite number from 0 through 100")
    if not isinstance(maintain_offset, bool):
        return rig_error("maintain_offset must be a boolean")
    if constraint_type == "parent" and not math.isclose(float(weight), 100.0, abs_tol=1e-6):
        return rig_error("Parent constraints do not support weighted targets")
    constrained_result, constrained = resolve_node_object(
        runtime, node_name=constrained_name, handle=constrained_handle
    )
    if constrained is None:
        return rig_error("Could not resolve constrained node", constrained=constrained_result)
    target_result, target = resolve_node_object(runtime, node_name=target_name, handle=target_handle)
    if target is None:
        return rig_error("Could not resolve constraint target", target=target_result)
    if node_identity(constrained) == node_identity(target):
        return rig_error("Constraint target must be different from constrained node", node=node_identity(constrained))

    constraint, warnings = _construct_runtime_object(runtime, CONSTRAINT_CONSTRUCTORS[constraint_type])
    if constraint is None:
        return rig_error(
            "No supported constraint constructor was available",
            constraint_type=constraint_type,
            warnings=warnings,
        )
    attachment_snapshot = _attachment_snapshot(constrained, constraint_type)
    if _python_attributes(constraint) is not None:
        _set_optional_attr(constraint, "constraint_type", constraint_type, warnings, strict=False)
        _set_optional_attr(constraint, "target", target, warnings, strict=False)
        _set_optional_attr(constraint, "weight", float(weight), warnings, strict=False)
    parent_offset = None
    if constraint_type == "parent":
        _attach_constraint(constrained, constraint_type, constraint, warnings)
        _append_parent_target(runtime, constraint, target, warnings)
        parent_offset = _apply_parent_offset(runtime, constraint, maintain_offset, warnings)
    else:
        _set_optional_attr(constraint, "relative", bool(maintain_offset), warnings, strict=False)
        if _python_attributes(constraint) is not None:
            _set_optional_attr(constraint, "maintain_offset", bool(maintain_offset), warnings, strict=False)
        _append_constraint_target(constraint, target, weight, warnings)
        _attach_constraint(constrained, constraint_type, constraint, warnings)

    if not _constraint_readback_matches(
        constrained,
        constraint,
        target,
        constraint_type=constraint_type,
        weight=weight,
        maintain_offset=maintain_offset,
        parent_offset=parent_offset,
    ):
        _remove_constraint_metadata(constrained, constraint)
        rollback_errors = _restore_attachment(attachment_snapshot, constraint)
        return rig_error(
            "Constraint host readback did not match the requested target",
            constrained=node_identity(constrained),
            target=node_identity(target),
            constraint_type=constraint_type,
            changed_target_count=0,
            rolled_back=not rollback_errors,
            rollback_errors=rollback_errors,
            warnings=warnings,
        )
    return rig_success(
        "Set constraint target",
        constrained=node_identity(constrained),
        target=node_identity(target),
        constraint_type=constraint_type,
        maintain_offset=bool(maintain_offset),
        changed_target_count=1,
        warnings=warnings,
    )


def create_constraint(
    runtime: Any,
    *,
    constrained_name: Optional[str] = None,
    constrained_handle: Optional[int] = None,
    target_name: Optional[str] = None,
    target_handle: Optional[int] = None,
    constraint_type: str,
    weight: float = 100.0,
    maintain_offset: bool = False,
) -> Dict[str, Any]:
    """Create a shared-name point/orient/aim/parent constraint."""
    native_type = TYPED_CONSTRAINTS.get(constraint_type)
    if native_type is None:
        return rig_error("Unsupported typed constraint", constraint_type=constraint_type)
    result = set_constraint_target(
        runtime,
        constrained_name=constrained_name,
        constrained_handle=constrained_handle,
        target_name=target_name,
        target_handle=target_handle,
        constraint_type=native_type,
        weight=weight,
        maintain_offset=maintain_offset,
    )
    if result.get("success"):
        result["data"]["native_constraint_type"] = native_type
        result["data"]["constraint_type"] = constraint_type
    return result


def _append_constraint_target(constraint: Any, target: Any, weight: float, warnings: List[str]) -> None:
    for method_name in ("appendTarget", "addTarget"):
        method = getattr(constraint, method_name, None)
        if callable(method):
            try:
                method(target, weight)
                return
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not call {}: {}".format(method_name, exc))
    targets = getattr(constraint, "targets", None)
    if targets is None:
        try:
            constraint.targets = []
            targets = constraint.targets
        except Exception:  # noqa: BLE001
            return
    try:
        targets.append({"target": target, "weight": float(weight)})
    except Exception as exc:  # noqa: BLE001
        warnings.append("Could not append constraint target: {}".format(exc))


def _append_parent_target(runtime: Any, constraint: Any, target: Any, warnings: List[str]) -> None:
    method = getattr(constraint, "addTarget", None)
    if not callable(method):
        warnings.append("Parent constraint does not expose addTarget")
        return
    frame = int(float(getattr(runtime, "currentTime", 0) or 0))
    try:
        method(target, frame)
    except Exception as exc:  # noqa: BLE001
        warnings.append("Could not append parent constraint target: {}".format(exc))


def _apply_parent_offset(
    runtime: Any, constraint: Any, maintain_offset: bool, warnings: List[str]
) -> Optional[Dict[str, Tuple[float, ...]]]:
    if maintain_offset:
        state = _parent_offset_state(constraint)
        if state is None:
            warnings.append("Parent constraint offset could not be read")
        return state

    link_params = getattr(constraint, "link_params", None)
    if link_params is None:
        warnings.append("Parent constraint does not expose link_params")
        return None
    point3 = getattr(runtime, "Point3", None)
    quat = getattr(runtime, "Quat", None)
    if not callable(point3) or not callable(quat):
        warnings.append("Runtime does not expose Point3 and Quat offset constructors")
        return None
    try:
        link_params.Position = point3(0.0, 0.0, 0.0)
        link_params.Rotation = quat(0.0, 0.0, 0.0, 1.0)
        link_params.Scale = point3(1.0, 1.0, 1.0)
    except Exception as exc:  # noqa: BLE001
        warnings.append("Could not reset parent constraint offset: {}".format(exc))
        return None
    return _PARENT_IDENTITY


def _parent_offset_state(constraint: Any) -> Optional[Dict[str, Tuple[float, ...]]]:
    link_params = getattr(constraint, "link_params", None)
    if link_params is None:
        return None
    position = _numeric_components(getattr(link_params, "Position", None), 3)
    rotation = _numeric_components(getattr(link_params, "Rotation", None), 4)
    scale = _numeric_components(getattr(link_params, "Scale", None), 3)
    if position is None or rotation is None or scale is None:
        return None
    return {"position": position, "rotation": rotation, "scale": scale}


def _numeric_components(value: Any, count: int) -> Optional[Tuple[float, ...]]:
    scale_value = getattr(value, "s", None)
    if scale_value is not None:
        value = scale_value
    component_names = ("x", "y", "z", "w")[:count]
    try:
        values = tuple(float(getattr(value, name)) for name in component_names)
    except (AttributeError, TypeError, ValueError):
        try:
            values = tuple(float(value[index]) for index in range(count))
        except Exception:  # noqa: BLE001 - pymxs value wrappers raise host-specific exceptions.
            return None
    if not all(math.isfinite(item) for item in values):
        return None
    return values


def _parent_offset_matches(constraint: Any, expected: Optional[Dict[str, Tuple[float, ...]]]) -> bool:
    if expected is None:
        return False
    actual = _parent_offset_state(constraint)
    if actual is None:
        return False
    return all(
        len(actual[name]) == len(values)
        and all(math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-6) for left, right in zip(actual[name], values))
        for name, values in expected.items()
    )


def _constraint_readback_matches(
    node: Any,
    constraint: Any,
    target: Any,
    *,
    constraint_type: str,
    weight: float,
    maintain_offset: bool,
    parent_offset: Optional[Dict[str, Tuple[float, ...]]] = None,
) -> bool:
    if constraint_type != "parent" and bool(getattr(constraint, "relative", not maintain_offset)) != bool(
        maintain_offset
    ):
        return False
    target_verified = False
    weight_verified = constraint_type == "parent"
    get_count = getattr(constraint, "getNumTargets", None)
    get_node = getattr(constraint, "getNode", None)
    if callable(get_count) and callable(get_node):
        try:
            count = int(get_count())
            target_verified = count >= 1 and _same_node(get_node(count), target)
            if constraint_type != "parent":
                get_weight = getattr(constraint, "getWeight", None)
                actual_weight = get_weight(count) if callable(get_weight) else getattr(constraint, "weight", None)
                weight_verified = actual_weight is not None and abs(float(actual_weight) - float(weight)) <= 1e-6
        except Exception:  # noqa: BLE001
            return False
    else:
        targets = getattr(constraint, "targets", None)
        if isinstance(targets, list) and targets:
            stored = targets[-1]
            stored_target = stored.get("target") if isinstance(stored, dict) else stored[0]
            stored_weight = stored.get("weight") if isinstance(stored, dict) else stored[1]
            target_verified = _same_node(stored_target, target)
            weight_verified = abs(float(stored_weight) - float(weight)) <= 1e-6
    if not target_verified or not weight_verified:
        return False
    if constraint_type == "parent" and not _parent_offset_matches(constraint, parent_offset):
        return False
    constraints = _constraint_metadata(node)
    if isinstance(constraints, list) and constraint in constraints:
        return True
    channel = "rotation" if constraint_type in {"orientation", "look_at"} else "position"
    if constraint_type == "parent":
        if _same_host_value(getattr(node, "controller", None), constraint):
            return True
        attributes = _python_attributes(node)
        return attributes is not None and _same_host_value(attributes.get("transform_constraint"), constraint)
    slot = getattr(node, channel, None)
    if _same_host_value(getattr(slot, "controller", None), constraint):
        return True
    attributes = _python_attributes(node)
    return attributes is not None and _same_host_value(attributes.get("{}_constraint".format(channel)), constraint)


def _same_node(left: Any, right: Any) -> bool:
    if left is right:
        return True
    try:
        left_handle = int(getattr(left, "handle"))
        right_handle = int(getattr(right, "handle"))
    except (AttributeError, TypeError, ValueError):
        return False
    return left_handle == right_handle


def _same_host_value(left: Any, right: Any) -> bool:
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:  # noqa: BLE001 - pymxs equality can raise host-specific exceptions.
        return False


def _python_attributes(value: Any) -> Optional[Dict[str, Any]]:
    try:
        attributes = vars(value)
    except TypeError:
        return None
    return attributes if isinstance(attributes, dict) else None


def _constraint_metadata(node: Any) -> Optional[List[Any]]:
    attributes = _python_attributes(node)
    if attributes is None:
        return None
    constraints = attributes.get("constraints")
    return constraints if isinstance(constraints, list) else None


def _remove_constraint_metadata(node: Any, constraint: Any) -> None:
    constraints = _constraint_metadata(node)
    if isinstance(constraints, list) and constraint in constraints:
        constraints.remove(constraint)


def _attachment_snapshot(node: Any, constraint_type: str) -> List[Tuple[Any, str, Any]]:
    snapshots = [(node, "controller", getattr(node, "controller", _MISSING))]
    channel = "rotation" if constraint_type in {"orientation", "look_at"} else "position"
    slot = getattr(node, channel, None)
    if slot is not None:
        snapshots.append((slot, "controller", getattr(slot, "controller", _MISSING)))
    attributes = _python_attributes(node)
    if attributes is not None:
        snapshots.append(
            (node, "{}_constraint".format(channel), attributes.get("{}_constraint".format(channel), _MISSING))
        )
        snapshots.append((node, "transform_constraint", attributes.get("transform_constraint", _MISSING)))
    return snapshots


def _restore_attachment(snapshots: List[Tuple[Any, str, Any]], constraint: Any) -> List[str]:
    errors = []
    for owner, attribute, previous in snapshots:
        if not _same_host_value(getattr(owner, attribute, _MISSING), constraint):
            continue
        try:
            if previous is _MISSING:
                delattr(owner, attribute)
            else:
                setattr(owner, attribute, previous)
        except Exception as exc:  # noqa: BLE001
            errors.append("{}: {}".format(attribute, exc))
    return errors


def _attach_constraint(node: Any, constraint_type: str, constraint: Any, warnings: List[str]) -> None:
    attributes = _python_attributes(node)
    constraints = _constraint_metadata(node)
    if attributes is not None and constraints is None:
        constraints = []
        attributes["constraints"] = constraints
    if constraints is not None:
        constraints.append(constraint)
    if constraint_type == "parent":
        try:
            node.controller = constraint
            return
        except Exception:  # noqa: BLE001
            if attributes is not None:
                _set_optional_attr(node, "transform_constraint", constraint, warnings, strict=False)
            return
    channel = "rotation" if constraint_type in {"orientation", "look_at"} else "position"
    slot = getattr(node, channel, None)
    if slot is not None:
        try:
            slot.controller = constraint
            return
        except Exception:  # noqa: BLE001
            pass
    if attributes is not None:
        _set_optional_attr(node, "{}_constraint".format(channel), constraint, warnings, strict=False)
