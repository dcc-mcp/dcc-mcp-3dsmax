"""Helpers for 3ds Max mesh operation skill scripts."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import json_safe, node_identity, resolve_node_object, resolve_node_objects


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


def modifier_stack_summary(
    node: Any,
    runtime: Any = None,
    *,
    property_names: Optional[Sequence[str]] = None,
    include_parameters: bool = True,
) -> Dict[str, Any]:
    """Return modifier stack metadata for one node.

    When a runtime is supplied and ``include_parameters`` is true, every stack
    entry also carries a ``parameters`` mapping. Properties that cannot be read
    are reported in ``unreadable`` so callers never mistake a partial read for
    a complete one. When a modifier exposes more properties than
    :data:`MODIFIER_PROPERTY_LIMIT`, the entry sets ``parameters_truncated``.
    """
    raw_modifiers, error = modifier_stack_entries(node)
    if error:
        return {"node": node_identity(node), "modifiers": [], "count": 0, "error": error}
    modifiers = []
    for index, modifier in enumerate(raw_modifiers, start=1):
        entry = {
            "index": index,
            "name": str(getattr(modifier, "name", "") or type(modifier).__name__),
            "type": type(modifier).__name__,
            "enabled": bool(getattr(modifier, "enabled", True)),
        }
        for attribute, key in _MODIFIER_FLAG_KEYS:
            value = getattr(modifier, attribute, None)
            if value is not None:
                entry[key] = bool(value)
        if include_parameters and runtime is not None:
            parameters, unreadable, parameter_error, truncated = modifier_parameters(runtime, modifier, property_names)
            entry["parameters"] = parameters
            if unreadable:
                entry["unreadable"] = unreadable
            if parameter_error:
                entry["parameter_error"] = parameter_error
            if truncated:
                entry["parameters_truncated"] = True
        modifiers.append(entry)
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


# ── Modifier stack CRUD ─────────────────────────────────────────────────
#
# Every write helper below is strict: a value that the target modifier does not
# accept is reported as an error, never as a success. Writes are verified by
# reading the value back before the tool reports success.

# 3ds Max stores parameters as 32-bit floats. ``sys.float_info``-style epsilon
# for binary32 is 2**-23; comparisons use that as the relative bound so a
# round-trip rounding difference is accepted while a genuinely rejected or
# coerced value (which differs by far more) still fails.
_FLOAT32_REL_TOL = 2.0**-23
_FLOAT32_ABS_TOL = 2.0**-23

# Upper bound on how many modifier parameters a single read will serialize.
MODIFIER_PROPERTY_LIMIT = 64

# Probed when the host cannot enumerate a modifier's own property names.
_MODIFIER_DISCOVERY_PROPERTIES = (
    "enabled",
    "enabledInViews",
    "enabledInRender",
    "iterations",
    "renderIterations",
    "useRenderIterations",
    "vertexPercent",
    "VertexPercent",
    "amount",
    "angle",
    "bendAngle",
    "direction",
    "axis",
    "tension",
    "segments",
    "thickness",
    "offset",
    "height",
    "width",
    "length",
    "radius",
    "sides",
    "smooth",
    "strength",
    "multiplier",
    "decay",
)

_MODIFIER_FLAG_KEYS = (("enabledInViews", "enabled_in_views"), ("enabledInRender", "enabled_in_render"))


def _node_label(node: Any) -> str:
    """Return a human-readable label for one node."""
    name = getattr(node, "name", None)
    return str(name) if name else "<node>"


def _modifier_label(modifier: Any, index: Any = None) -> str:
    """Return a human-readable label for one modifier."""
    name = getattr(modifier, "name", None)
    if name:
        return str(name)
    label = type(modifier).__name__
    return "{} #{}".format(label, index) if index is not None else label


def _is_valid_property_name(name: Any) -> bool:
    """Return True when ``name`` is a safe, public modifier property name."""
    if not isinstance(name, str) or not name:
        return False
    if name.startswith("_"):
        return False
    return name.isidentifier()


def _coerce_property_name(value: Any) -> Optional[str]:
    """Normalize a MAXScript ``Name`` or string into a property name."""
    text = str(value).strip()
    if text.startswith("#"):
        text = text[1:].strip()
    return text if _is_valid_property_name(text) else None


def _numbers_equal(requested: float, actual: float) -> bool:
    """Compare two numbers, tolerating 3ds Max's 32-bit float round-trip.

    3ds Max stores parameters as 32-bit floats, so a read-back can differ in
    the low bits (``123456.789`` comes back as ``123456.7890625``). Integers
    round-trip exactly, so they are compared exactly: a relative tolerance on
    a large integer would silently swallow a one-unit difference.
    """
    if requested == actual:
        return True
    if requested.is_integer() and actual.is_integer():
        return False
    return math.isclose(requested, actual, rel_tol=_FLOAT32_REL_TOL, abs_tol=_FLOAT32_ABS_TOL)


def _values_equal(requested: Any, actual: Any) -> bool:
    """Compare a requested value against a read-back value, tolerating coercion."""
    if requested is None or actual is None:
        return requested is None and actual is None
    if isinstance(requested, bool) or isinstance(actual, bool):
        return bool(requested) == bool(actual)
    if isinstance(requested, (int, float)) and isinstance(actual, (int, float)):
        try:
            return _numbers_equal(float(requested), float(actual))
        except (TypeError, ValueError, OverflowError):
            return False
    if isinstance(requested, str) and not isinstance(actual, str):
        try:
            return requested == str(actual)
        except Exception:  # noqa: BLE001
            return False
    if isinstance(requested, Sequence) and not isinstance(requested, (str, bytes)):
        if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes)):
            return False
        if len(requested) != len(actual):
            return False
        return all(_values_equal(item, other) for item, other in zip(requested, actual))
    try:
        return bool(requested == actual)
    except Exception:  # noqa: BLE001
        return False


def modifier_stack_entries(node: Any) -> Tuple[List[Any], Optional[str]]:
    """Return ``(modifiers, error)`` for one node."""
    try:
        raw = getattr(node, "modifiers", None)
        modifiers = [] if raw is None else list(raw)
    except Exception as exc:  # noqa: BLE001
        return [], "Could not read the modifier stack on {}: {}".format(_node_label(node), exc)
    return modifiers, None


def find_modifier(
    node: Any, *, modifier_name: Optional[str] = None, modifier_index: Any = None
) -> Tuple[Optional[int], Optional[Any], Optional[str]]:
    """Return ``(index, modifier, error)`` for one stack entry.

    ``index`` is 1-based, matching how agents read ``get_modifier_stack`` output.
    """
    if modifier_name is not None and modifier_index is not None:
        return None, None, "Provide either modifier_name or modifier_index, not both"
    if modifier_name is None and modifier_index is None:
        return None, None, "modifier_name or modifier_index is required"

    modifiers, error = modifier_stack_entries(node)
    if error:
        return None, None, error

    if modifier_index is not None:
        try:
            wanted = int(modifier_index)
        except (TypeError, ValueError):
            return None, None, "modifier_index must be an integer, got {!r}".format(modifier_index)
        if wanted < 1 or wanted > len(modifiers):
            return (
                None,
                None,
                "Modifier index {} is out of range: {} has {} modifier(s)".format(
                    wanted, _node_label(node), len(modifiers)
                ),
            )
        return wanted, modifiers[wanted - 1], None

    wanted_name = str(modifier_name)
    matches = [
        (index, modifier)
        for index, modifier in enumerate(modifiers, start=1)
        if str(getattr(modifier, "name", "") or "").lower() == wanted_name.lower()
    ]
    if not matches:
        available = ", ".join(str(getattr(modifier, "name", "") or type(modifier).__name__) for modifier in modifiers)
        return (
            None,
            None,
            "No modifier named '{}' on {} (available: {})".format(wanted_name, _node_label(node), available or "none"),
        )
    if len(matches) > 1:
        return (
            None,
            None,
            "Modifier name '{}' is ambiguous on {}: {} entries match, use modifier_index instead".format(
                wanted_name, _node_label(node), len(matches)
            ),
        )
    index, modifier = matches[0]
    return index, modifier, None


def _read_one_property(runtime: Any, modifier: Any, name: str) -> Tuple[bool, Any]:
    """Return ``(ok, value)`` for one modifier property."""
    getter = getattr(runtime, "getProperty", None)
    if callable(getter):
        try:
            return True, getter(modifier, name)
        except Exception:  # noqa: BLE001
            pass
    try:
        return True, getattr(modifier, name)
    except Exception:  # noqa: BLE001
        return False, None


def modifier_property_names(runtime: Any, modifier: Any) -> Tuple[List[str], bool]:
    """Discover the readable property names exposed by one modifier.

    Returns ``(names, truncated)``; ``truncated`` reports whether the result was
    cut at :data:`MODIFIER_PROPERTY_LIMIT`.
    """
    names: List[str] = []
    prop_names = getattr(runtime, "getPropNames", None)
    if callable(prop_names):
        try:
            for item in prop_names(modifier):
                candidate = _coerce_property_name(item)
                if candidate and candidate not in names:
                    names.append(candidate)
        except Exception:  # noqa: BLE001
            names = []
    if not names:
        for candidate in _MODIFIER_DISCOVERY_PROPERTIES:
            if candidate not in names and hasattr(modifier, candidate):
                names.append(candidate)
    if not names:
        for candidate in dir(modifier):
            if candidate in names or not _is_valid_property_name(candidate):
                continue
            try:
                value = getattr(modifier, candidate)
            except Exception:  # noqa: BLE001
                continue
            if not callable(value):
                names.append(candidate)
    return names[:MODIFIER_PROPERTY_LIMIT], len(names) > MODIFIER_PROPERTY_LIMIT


def modifier_parameters(
    runtime: Any, modifier: Any, property_names: Optional[Sequence[str]] = None
) -> Tuple[Dict[str, Any], List[str], Optional[str], bool]:
    """Return ``(parameters, unreadable, error, truncated)`` for one modifier."""
    if property_names:
        names: List[str] = []
        for item in property_names:
            candidate = _coerce_property_name(item)
            if candidate is None:
                return {}, [], "'{}' is not a valid modifier property name".format(item), False
            if candidate not in names:
                names.append(candidate)
        names, truncated = names[:MODIFIER_PROPERTY_LIMIT], len(names) > MODIFIER_PROPERTY_LIMIT
    else:
        names, truncated = modifier_property_names(runtime, modifier)

    parameters: Dict[str, Any] = {}
    unreadable: List[str] = []
    for name in names:
        ok, value = _read_one_property(runtime, modifier, name)
        if ok:
            parameters[name] = json_safe(value)
        else:
            unreadable.append(name)
    return parameters, unreadable, None, truncated


def set_modifier_property(runtime: Any, modifier: Any, name: Any, value: Any) -> Tuple[Any, Optional[str]]:
    """Set one modifier property, verify it by read-back, and return ``(applied, error)``."""
    candidate = _coerce_property_name(name)
    if candidate is None:
        return None, "'{}' is not a valid modifier property name".format(name)
    try:
        setattr(modifier, candidate, value)
    except Exception as exc:  # noqa: BLE001
        return None, "Modifier '{}' rejected property '{}': {}".format(_modifier_label(modifier), candidate, exc)
    ok, actual = _read_one_property(runtime, modifier, candidate)
    if not ok:
        return (
            None,
            "Modifier '{}' accepted property '{}' but the value could not be read back".format(
                _modifier_label(modifier), candidate
            ),
        )
    if not _values_equal(value, actual):
        return (
            None,
            "Modifier '{}' rejected property '{}': requested {!r} but it reports {!r}".format(
                _modifier_label(modifier), candidate, value, actual
            ),
        )
    return json_safe(actual), None


def apply_modifier_properties(
    runtime: Any, modifier: Any, properties: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Apply a mapping of modifier properties strictly. Return ``(applied, error)``."""
    if not properties:
        return {}, None
    if not isinstance(properties, dict):
        return {}, "properties must be an object mapping property names to values"
    applied: Dict[str, Any] = {}
    for key in properties:
        candidate = _coerce_property_name(key)
        if candidate is None:
            return applied, "'{}' is not a valid modifier property name".format(key)
        value, error = set_modifier_property(runtime, modifier, candidate, properties[key])
        if error:
            return applied, error
        applied[candidate] = value
    return applied, None


def create_modifier(runtime: Any, modifier_class: Any) -> Tuple[Any, Optional[str]]:
    """Instantiate a modifier class by name. Return ``(modifier, error)``."""
    candidate = str(modifier_class or "").strip()
    if not candidate:
        return None, "modifier_class is required"
    if not candidate.replace("_", "").isalnum():
        return None, "'{}' is not a valid 3ds Max modifier class name".format(modifier_class)
    factory = getattr(runtime, candidate, None)
    if not callable(factory):
        return None, "3ds Max does not expose a modifier class named '{}'".format(candidate)
    try:
        modifier = factory()
    except Exception as exc:  # noqa: BLE001
        return None, "Could not create modifier '{}': {}".format(candidate, exc)
    if modifier is None:
        return None, "Modifier constructor '{}' returned nothing".format(candidate)
    return modifier, None


def attach_modifier(runtime: Any, node: Any, modifier: Any) -> Tuple[Optional[int], Optional[str]]:
    """Attach an instantiated modifier to a node and verify the stack grew.

    Returns ``(index, error)`` where ``index`` is the 1-based stack position.

    3ds Max numbers the modifier stack from the top, and ``addModifier`` without
    ``before:`` inserts at the top - so the new modifier lands at index 1, not
    at the bottom of the stack.
    """
    before, error = modifier_stack_entries(node)
    if error:
        return None, error
    add = getattr(runtime, "addModifier", None)
    if callable(add):
        try:
            add(node, modifier)
        except Exception as exc:  # noqa: BLE001
            return None, "Could not attach modifier to {}: {}".format(_node_label(node), exc)
        inserted_at_top = True
    else:
        try:
            node.modifiers.append(modifier)
        except Exception as exc:  # noqa: BLE001
            return None, "Could not attach modifier to {}: {}".format(_node_label(node), exc)
        inserted_at_top = False

    after, error = modifier_stack_entries(node)
    if error:
        return None, error

    # Identity first: it is the only check that is correct regardless of where
    # the host inserted the modifier. pymxs hands back a fresh wrapper per
    # access, so fall through to positional reasoning when it does not match.
    for index, existing in enumerate(after, start=1):
        if existing is modifier:
            return index, None

    if len(after) != len(before) + 1:
        return (
            None,
            "Modifier was not attached to {}: the stack still has {} entr(ies), expected {}".format(
                _node_label(node), len(after), len(before) + 1
            ),
        )
    return 1 if inserted_at_top else len(after), None


def remove_modifier(runtime: Any, node: Any, index: int) -> Optional[str]:
    """Remove one modifier by 1-based index and verify the stack shrank.

    Returns an error message, or ``None`` on verified success.
    """
    before, error = modifier_stack_entries(node)
    if error:
        return error
    if index < 1 or index > len(before):
        return "Modifier index {} is out of range: {} has {} modifier(s)".format(index, _node_label(node), len(before))
    target = before[index - 1]
    label = _modifier_label(target, index)

    deleter = getattr(runtime, "deleteModifier", None)
    if callable(deleter):
        for args in ((node, target), (node, index)):
            try:
                deleter(*args)
            except Exception:  # noqa: BLE001
                continue
            after, read_error = modifier_stack_entries(node)
            if read_error:
                return read_error
            if len(after) < len(before):
                return None
        return (
            "Could not remove modifier '{}' from {}: the host reported no error "
            "but the stack still has {} modifier(s)".format(label, _node_label(node), len(before))
        )

    try:
        node.modifiers.remove(target)
    except Exception as exc:  # noqa: BLE001
        return "Could not remove modifier '{}' from {}: {}".format(label, _node_label(node), exc)
    after, read_error = modifier_stack_entries(node)
    if read_error:
        return read_error
    if len(after) >= len(before):
        return "Could not remove modifier '{}' from {}: it is still on the stack".format(label, _node_label(node))
    return None


def set_modifier_state(
    runtime: Any,
    modifier: Any,
    *,
    enabled: Optional[bool] = None,
    enabled_in_views: Optional[bool] = None,
    enabled_in_render: Optional[bool] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Toggle modifier enable state with separate viewport and render granularity."""
    if enabled is None and enabled_in_views is None and enabled_in_render is None:
        return {}, "At least one of enabled, enabled_in_views, or enabled_in_render is required"

    applied: Dict[str, Any] = {}
    if enabled is not None:
        value, error = set_modifier_property(runtime, modifier, "enabled", bool(enabled))
        if error:
            return applied, error
        applied["enabled"] = value

    for attribute, requested in (("enabledInViews", enabled_in_views), ("enabledInRender", enabled_in_render)):
        if requested is None:
            continue
        if not hasattr(modifier, attribute):
            return (
                applied,
                "Modifier '{}' does not expose '{}', so viewport and render state cannot be "
                "controlled separately; use the `enabled` argument instead".format(
                    _modifier_label(modifier), attribute
                ),
            )
        value, error = set_modifier_property(runtime, modifier, attribute, bool(requested))
        if error:
            return applied, error
        applied[attribute] = value
    return applied, None


def collapse_modifier_stack(runtime: Any, node: Any) -> Dict[str, Any]:
    """Collapse a node's whole modifier stack and verify it actually shrank."""
    before, error = modifier_stack_entries(node)
    if error:
        return {"before": 0, "after": 0, "error": error, "warning": None}
    if not before:
        return {
            "before": 0,
            "after": 0,
            "error": None,
            "warning": "{} has no modifiers to collapse".format(_node_label(node)),
        }

    max_ops = getattr(runtime, "maxOps", None)
    candidates = []
    node_collapser = getattr(max_ops, "collapseNode", None) if max_ops is not None else None
    if callable(node_collapser):
        candidates.append(("maxOps.collapseNode", lambda: node_collapser(node, False)))
        candidates.append(("maxOps.collapseNode", lambda: node_collapser(node)))
    stack_collapser = getattr(runtime, "collapseStack", None)
    if callable(stack_collapser):
        candidates.append(("collapseStack", lambda: stack_collapser(node)))

    if not candidates:
        return {
            "before": len(before),
            "after": len(before),
            "error": (
                "Could not collapse the modifier stack of {}: this host exposes neither "
                "maxOps.collapseNode nor collapseStack".format(_node_label(node))
            ),
            "warning": None,
        }

    attempts: List[str] = []
    for name, call in candidates:
        try:
            call()
        except Exception as exc:  # noqa: BLE001
            attempts.append("{}: {}".format(name, exc))
            continue
        after, read_error = modifier_stack_entries(node)
        if read_error:
            return {"before": len(before), "after": len(before), "error": read_error, "warning": None}
        if len(after) >= len(before):
            return {
                "before": len(before),
                "after": len(after),
                "error": (
                    "Collapse ran on {} but the modifier stack still has {} entr(ies), was {}".format(
                        _node_label(node), len(after), len(before)
                    )
                ),
                "warning": None,
            }
        return {"before": len(before), "after": len(after), "error": None, "warning": None}

    return {
        "before": len(before),
        "after": len(before),
        "error": "Could not collapse the modifier stack of {} ({})".format(_node_label(node), "; ".join(attempts)),
        "warning": None,
    }


def make_modifier_unique(runtime: Any, node: Any, modifier: Any) -> Dict[str, Any]:
    """Break modifier instancing so one node's copy can be edited independently.

    3ds Max exposes no return value that confirms a modifier became unique, so a
    successful call is reported with an explicit ``warning`` instead of a bare
    success. A host without any ``makeUnique`` entry point is reported as an error.
    """
    label = _modifier_label(modifier)
    candidates: List[Tuple[str, Any]] = []
    make_unique = getattr(runtime, "makeUnique", None)
    if callable(make_unique):
        candidates.append(("makeUnique(node, modifier)", lambda: make_unique(node, modifier)))
        candidates.append(("makeUnique(modifier)", lambda: make_unique(modifier)))
    max_ops = getattr(runtime, "maxOps", None)
    ops_unique = getattr(max_ops, "makeUnique", None) if max_ops is not None else None
    if callable(ops_unique):
        candidates.append(("maxOps.makeUnique(node, modifier)", lambda: ops_unique(node, modifier)))
        candidates.append(("maxOps.makeUnique(modifier)", lambda: ops_unique(modifier)))

    if not candidates:
        return {
            "error": ("This host exposes no makeUnique entry point, so modifier '{}' was left instanced".format(label)),
            "entry_point": None,
            "warning": None,
        }

    failures: List[str] = []
    for name, call in candidates:
        try:
            call()
        except Exception as exc:  # noqa: BLE001
            failures.append("{}: {}".format(name, exc))
            continue
        return {
            "error": None,
            "entry_point": name,
            "warning": (
                "Ran {} on modifier '{}'. 3ds Max returns no value here, so uniqueness cannot be "
                "confirmed programmatically; re-read the modifier stack to verify.".format(name, label)
            ),
        }
    return {
        "error": "Could not make modifier '{}' unique ({})".format(label, "; ".join(failures)),
        "entry_point": None,
        "warning": None,
    }


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
