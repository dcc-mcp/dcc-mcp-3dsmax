"""Helpers for 3ds Max rigging and deformer skill scripts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._mesh_ops import add_modifier
from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_object, resolve_node_objects

DEFORMER_CONSTRUCTORS: Dict[str, Tuple[str, ...]] = {
    "skin": ("Skin", "SkinModifier"),
    "bend": ("Bend",),
    "twist": ("Twist",),
    "ffd_2x2x2": ("FFD_2x2x2", "FFD2x2x2"),
    "path_deform": ("PathDeform", "Path_Deform"),
    "skin_wrap": ("Skin_Wrap", "SkinWrap"),
    "morpher": ("Morpher",),
}

CONSTRAINT_CONSTRUCTORS: Dict[str, Tuple[str, ...]] = {
    "position": ("Position_Constraint", "PositionConstraint"),
    "orientation": ("Orientation_Constraint", "OrientationConstraint"),
    "look_at": ("LookAt_Constraint", "LookAtConstraint"),
    "path": ("Path_Constraint", "PathConstraint"),
}


def rig_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def rig_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def resolve_rig_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
) -> Dict[str, Any]:
    """Resolve explicit rig targets or an explicitly requested selection."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001
            selected = []
        if not selected:
            return rig_error("Current selection is empty", nodes=[], objects=[])
        return {
            "success": True,
            "status": "success",
            "message": "Resolved selected nodes",
            "nodes": [node_identity(node) for node in selected],
            "objects": selected,
        }
    result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
    if not result.get("success"):
        return rig_error(result["message"], errors=result.get("errors", []), objects=[])
    result["status"] = "success"
    return result


def create_helper_node(
    runtime: Any,
    *,
    name: str,
    helper_type: str = "point",
    size: float = 10.0,
    position: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Create a host-native helper node."""
    factories = {
        "point": ("Point",),
        "dummy": ("Dummy",),
        "expose_transform": ("ExposeTransform", "Expose_Transform"),
        "circle": ("Circle",),
    }.get(helper_type)
    if factories is None:
        return rig_error("Unsupported helper_type", helper_type=helper_type)

    node, warnings = _construct_runtime_object(runtime, factories)
    if node is None:
        return rig_error("No supported helper constructor was available", helper_type=helper_type, warnings=warnings)
    _set_name(node, name)
    _set_optional_attr(node, "size", float(size), warnings)
    if position is not None:
        _set_optional_attr(node, "position", _vector(position), warnings)
    _set_optional_attr(node, "helper_type", helper_type, warnings, strict=False)
    _append_scene_object(runtime, node)
    return rig_success("Created helper node", node=node_identity(node), changed_node_count=1, warnings=warnings)


def create_bone_node(
    runtime: Any,
    *,
    name: str,
    start: Sequence[float],
    end: Sequence[float],
    up_axis: Optional[Sequence[float]] = None,
) -> Tuple[Optional[Any], List[str]]:
    """Create one bone-like node and return the raw host node."""
    warnings: List[str] = []
    start_vec = _vector(start)
    end_vec = _vector(end)
    if start_vec == end_vec:
        return None, ["Bone start and end positions must differ"]

    bone = None
    creator = getattr(getattr(runtime, "BoneSys", None), "createBone", None)
    if callable(creator):
        try:
            bone = creator(
                _point3(runtime, start_vec), _point3(runtime, end_vec), _point3(runtime, _vector(up_axis or [0, 0, 1]))
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not create bone through BoneSys: {}".format(exc))

    if bone is None:
        bone, constructor_warnings = _construct_runtime_object(runtime, ("BoneGeometry", "Bone"))
        warnings.extend(constructor_warnings)
    if bone is None:
        return None, warnings or ["No supported bone constructor was available"]

    _set_name(bone, name)
    _set_optional_attr(bone, "start", start_vec, warnings, strict=False)
    _set_optional_attr(bone, "end", end_vec, warnings, strict=False)
    _append_scene_object(runtime, bone)
    return bone, warnings


def create_joint_chain(runtime: Any, *, base_name: str, positions: Sequence[Sequence[float]]) -> Dict[str, Any]:
    """Create a simple parented bone chain from point positions."""
    if len(positions) < 2:
        return rig_error("At least two positions are required to create a joint chain", changed_node_count=0)

    bones = []
    warnings: List[str] = []
    previous = None
    for index in range(len(positions) - 1):
        name = "{}_{:02d}".format(base_name, index + 1)
        bone, bone_warnings = create_bone_node(runtime, name=name, start=positions[index], end=positions[index + 1])
        warnings.extend(bone_warnings)
        if bone is None:
            return rig_error("Could not create joint chain", warnings=warnings, changed_node_count=len(bones))
        if previous is not None:
            try:
                bone.parent = previous
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not parent {} to previous bone: {}".format(name, exc))
        bones.append(bone)
        previous = bone

    return rig_success(
        "Created joint chain",
        bones=[node_identity(bone) for bone in bones],
        changed_node_count=len(bones),
        warnings=warnings,
    )


def create_path_helper(
    runtime: Any,
    *,
    name: str,
    points: Sequence[Sequence[float]],
    closed: bool = False,
) -> Dict[str, Any]:
    """Create a simple path or curve helper with public point metadata."""
    if len(points) < 2:
        return rig_error("At least two path points are required", changed_node_count=0)
    node, warnings = _construct_runtime_object(runtime, ("SplineShape", "Line", "Shape"))
    if node is None:
        return rig_error("No supported path helper constructor was available", warnings=warnings)
    _set_name(node, name)
    _set_optional_attr(node, "path_points", [_vector(point) for point in points], warnings, strict=False)
    _set_optional_attr(node, "closed", bool(closed), warnings, strict=False)
    _append_scene_object(runtime, node)
    return rig_success(
        "Created path helper",
        node=node_identity(node),
        point_count=len(points),
        changed_node_count=1,
        warnings=warnings,
    )


def rig_state_summary(node: Any) -> Dict[str, Any]:
    """Return controller, constraint, deformer, and skinning state for one node."""
    modifiers = _modifier_rows(node)
    deformers = [row for row in modifiers if _is_deformer(row["name"], row["type"])]
    skin = next((row for row in modifiers if _is_skin(row["name"], row["type"])), None)
    return {
        "node": node_identity(node),
        "controllers": _controller_rows(node),
        "constraints": _constraint_rows(node),
        "modifiers": modifiers,
        "deformers": deformers,
        "skinning": {
            "has_skin": skin is not None,
            "skin_modifier": skin,
            "bone_count": _skin_bone_count(node, skin),
        },
    }


def apply_deformer(
    runtime: Any, node: Any, *, deformer_type: str, attributes: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Apply a supported deformer/modifier to one node."""
    constructors = DEFORMER_CONSTRUCTORS.get(deformer_type)
    if constructors is None:
        return rig_error("Unsupported deformer_type", deformer_type=deformer_type)
    modifier, warnings = add_modifier(runtime, node, constructors, **dict(attributes or {}))
    if modifier is None:
        return rig_error("No supported deformer constructor was available", node=node_identity(node), warnings=warnings)
    _set_optional_attr(modifier, "deformer_type", deformer_type, warnings, strict=False)
    return rig_success(
        "Applied deformer modifier",
        node=node_identity(node),
        modifier=_modifier_row(modifier, index=len(_modifier_rows(node))),
        changed_modifier_count=1,
        warnings=warnings,
    )


def remove_deformer(
    node: Any, *, deformer_type: Optional[str] = None, modifier_name: Optional[str] = None
) -> Dict[str, Any]:
    """Remove matching deformer/modifier entries from a node's stack."""
    if not deformer_type and not modifier_name:
        return rig_error(
            "deformer_type or modifier_name is required", node=node_identity(node), changed_modifier_count=0
        )
    try:
        modifiers = list(node.modifiers)
    except Exception:  # noqa: BLE001
        modifiers = []
    matches = [
        modifier
        for modifier in modifiers
        if _matches_modifier(modifier, deformer_type=deformer_type, modifier_name=modifier_name)
    ]
    if not matches:
        return rig_error("No matching deformer modifier found", node=node_identity(node), changed_modifier_count=0)
    for modifier in matches:
        try:
            node.modifiers.remove(modifier)
        except Exception:  # noqa: BLE001
            continue
    return rig_success("Removed deformer modifiers", node=node_identity(node), changed_modifier_count=len(matches))


def set_constraint_target(
    runtime: Any,
    *,
    constrained_name: Optional[str] = None,
    constrained_handle: Optional[int] = None,
    target_name: Optional[str] = None,
    target_handle: Optional[int] = None,
    constraint_type: str,
    weight: float = 100.0,
) -> Dict[str, Any]:
    """Create or update a basic transform constraint target."""
    if constraint_type not in CONSTRAINT_CONSTRUCTORS:
        return rig_error("Unsupported constraint_type", constraint_type=constraint_type)
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
            "No supported constraint constructor was available", constraint_type=constraint_type, warnings=warnings
        )

    _set_optional_attr(constraint, "constraint_type", constraint_type, warnings, strict=False)
    _set_optional_attr(constraint, "target", target, warnings, strict=False)
    _set_optional_attr(constraint, "weight", float(weight), warnings, strict=False)
    _append_constraint_target(constraint, target, weight, warnings)
    _attach_constraint(constrained, constraint_type, constraint, warnings)

    return rig_success(
        "Set constraint target",
        constrained=node_identity(constrained),
        target=node_identity(target),
        constraint_type=constraint_type,
        changed_target_count=1,
        warnings=warnings,
    )


def character_system_availability(runtime: Any) -> Dict[str, Any]:
    """Return availability for optional character-system helpers."""
    systems = {
        "biped": _has_symbol(runtime, "Biped"),
        "cat": _has_symbol(runtime, "CATParent") or _has_symbol(runtime, "CATRig"),
        "character": _has_symbol(runtime, "Character"),
    }
    return rig_success("Checked character system availability", systems=systems, available=any(systems.values()))


def _construct_runtime_object(runtime: Any, factories: Sequence[str]) -> Tuple[Optional[Any], List[str]]:
    warnings = []
    for name in factories:
        factory = getattr(runtime, name, None)
        if not callable(factory):
            continue
        try:
            return factory(), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not create {}: {}".format(name, exc))
    return None, warnings


def _set_name(node: Any, name: str) -> None:
    try:
        node.name = str(name)
    except Exception:  # noqa: BLE001
        pass


def _set_optional_attr(node: Any, attr: str, value: Any, warnings: List[str], *, strict: bool = True) -> None:
    try:
        setattr(node, attr, value)
    except Exception as exc:  # noqa: BLE001
        if strict:
            warnings.append("Could not set {}: {}".format(attr, exc))


def _append_scene_object(runtime: Any, node: Any) -> None:
    try:
        objects = runtime.objects
        if isinstance(objects, list) and node not in objects:
            objects.append(node)
    except Exception:  # noqa: BLE001
        pass


def _point3(runtime: Any, value: Sequence[float]) -> Any:
    factory = getattr(runtime, "Point3", None)
    vector = _vector(value)
    if callable(factory):
        try:
            return factory(vector[0], vector[1], vector[2])
        except Exception:  # noqa: BLE001
            pass
    return vector


def _vector(value: Sequence[float]) -> List[float]:
    if len(value) < 3:
        raise ValueError("Vector values require at least three numbers")
    return [float(value[0]), float(value[1]), float(value[2])]


def _controller_rows(node: Any) -> Dict[str, Optional[str]]:
    rows: Dict[str, Optional[str]] = {}
    for attr in ("position", "rotation", "scale"):
        value = getattr(node, attr, None)
        controller = getattr(value, "controller", None)
        rows[attr] = type(controller).__name__ if controller is not None else None
    return rows


def _modifier_rows(node: Any) -> List[Dict[str, Any]]:
    try:
        raw_modifiers = list(node.modifiers)
    except Exception:  # noqa: BLE001
        raw_modifiers = []
    return [_modifier_row(modifier, index=index) for index, modifier in enumerate(raw_modifiers, start=1)]


def _modifier_row(modifier: Any, *, index: int) -> Dict[str, Any]:
    name = str(getattr(modifier, "name", "") or type(modifier).__name__)
    return {
        "index": index,
        "name": name,
        "type": type(modifier).__name__,
        "enabled": bool(getattr(modifier, "enabled", True)),
        "deformer_type": getattr(modifier, "deformer_type", None),
    }


def _constraint_rows(node: Any) -> List[Dict[str, Any]]:
    rows = []
    for index, constraint in enumerate(list(getattr(node, "constraints", []) or []), start=1):
        target = getattr(constraint, "target", None)
        rows.append(
            {
                "index": index,
                "type": getattr(constraint, "constraint_type", type(constraint).__name__),
                "target": node_identity(target) if target is not None else None,
                "weight": float(getattr(constraint, "weight", 100.0) or 0.0),
            }
        )
    return rows


def _is_deformer(name: str, type_name: str) -> bool:
    text = "{} {}".format(name, type_name).lower()
    return any(
        token.lower().replace("_", "") in text.replace("_", "")
        for values in DEFORMER_CONSTRUCTORS.values()
        for token in values
    )


def _is_skin(name: str, type_name: str) -> bool:
    text = "{} {}".format(name, type_name).lower()
    return "skin" in text


def _skin_bone_count(node: Any, skin_row: Optional[Dict[str, Any]]) -> int:
    if skin_row is None:
        return 0
    for modifier in getattr(node, "modifiers", []) or []:
        if getattr(modifier, "name", "") == skin_row["name"]:
            bones = getattr(modifier, "bones", None)
            if bones is not None:
                try:
                    return len(bones)
                except TypeError:
                    return int(getattr(modifier, "bone_count", 0) or 0)
    return 0


def _matches_modifier(modifier: Any, *, deformer_type: Optional[str], modifier_name: Optional[str]) -> bool:
    name = str(getattr(modifier, "name", "") or type(modifier).__name__)
    if modifier_name and name == modifier_name:
        return True
    if not deformer_type:
        return False
    constructors = DEFORMER_CONSTRUCTORS.get(deformer_type, ())
    text = "{} {}".format(name, type(modifier).__name__).lower().replace("_", "")
    return (
        any(token.lower().replace("_", "") in text for token in constructors)
        or getattr(modifier, "deformer_type", None) == deformer_type
    )


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


def _attach_constraint(node: Any, constraint_type: str, constraint: Any, warnings: List[str]) -> None:
    constraints = getattr(node, "constraints", None)
    if constraints is None:
        try:
            node.constraints = []
            constraints = node.constraints
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not store constraint metadata: {}".format(exc))
            constraints = None
    if constraints is not None:
        try:
            constraints.append(constraint)
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not append constraint metadata: {}".format(exc))

    channel = "rotation" if constraint_type in {"orientation", "look_at"} else "position"
    slot = getattr(node, channel, None)
    if slot is not None:
        try:
            slot.controller = constraint
            return
        except Exception:  # noqa: BLE001
            pass
    _set_optional_attr(node, "{}_constraint".format(channel), constraint, warnings, strict=False)


def _has_symbol(runtime: Any, name: str) -> bool:
    try:
        return getattr(runtime, name) is not None
    except Exception:  # noqa: BLE001
        return False
