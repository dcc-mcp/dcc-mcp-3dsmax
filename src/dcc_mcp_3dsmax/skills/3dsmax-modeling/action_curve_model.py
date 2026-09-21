"""Named curve models: parametric profiles, sweeps, and scene-stored parameters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._curve_utils import (
    CURVE_MODEL_PROPERTY,
    PROFILE_KINDS,
    call_first,
    circle_points,
    clear_params,
    create_scene_object,
    create_spline_shape,
    curve_error,
    curve_success,
    detach_modifier,
    iter_scene_shapes,
    load_params,
    read_property_first,
    rectangle_points,
    resolve_shape,
    rounded_rect_points,
    set_property_first,
    store_params,
    update_shape,
    validated_bool,
    validated_int,
    validated_knot_type,
    validated_name,
    validated_number,
    validated_vectors,
    verify_world_points,
)
from dcc_mcp_3dsmax._curve_utils import (
    delete_node as remove_scene_node,
)
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max

_ACTIONS = ("create", "update", "read", "list", "delete")
_OPERATIONS = ("profile", "sweep")


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _profile_points(profile: str, params: Dict[str, Any]) -> tuple:
    """Return ``(points, closed, warning)`` for one profile kind."""
    if profile == "polyline":
        points = validated_vectors(params.get("points"), "points")
        return points, bool(params.get("closed", False)), None
    if profile == "rectangle":
        width = validated_number(params.get("width"), "width", default=100.0, minimum=0.0, exclusive_minimum=True)
        height = validated_number(params.get("height"), "height", default=100.0, minimum=0.0, exclusive_minimum=True)
        return rectangle_points(width, height), True, None
    if profile == "circle":
        radius = validated_number(params.get("radius"), "radius", default=50.0, minimum=0.0, exclusive_minimum=True)
        segments = validated_int(params.get("segments"), "segments", default=16, minimum=3, maximum=256)
        return circle_points(radius, segments), True, None
    width = validated_number(params.get("width"), "width", default=100.0, minimum=0.0, exclusive_minimum=True)
    height = validated_number(params.get("height"), "height", default=100.0, minimum=0.0, exclusive_minimum=True)
    corner_radius = validated_number(params.get("corner_radius"), "corner_radius", default=10.0, minimum=0.0)
    corner_segments = validated_int(params.get("corner_segments"), "corner_segments", default=4, minimum=1, maximum=32)
    points, note = rounded_rect_points(width, height, corner_radius, corner_segments)
    return points, True, note


def _attach_sweep(runtime: Any, path_node: Any, profile_node: Any) -> tuple:
    """Add a Sweep modifier driven by ``profile_node`` to ``path_node``.

    Returns ``(modifier, index, error)``. Any property or method the host does
    not accept fails the call and removes the half-built modifier, so a sweep
    can never be reported as configured when the host ignored part of it.
    """
    value, _used_class, error = create_scene_object(runtime, ("Sweep",))
    if error:
        return None, None, error
    add = getattr(runtime, "addModifier", None)
    if not callable(add):
        return None, None, "3ds Max does not expose addModifier"
    try:
        add(path_node, value)
        index = 1
    except Exception as exc:  # noqa: BLE001 - surface the host rejection.
        return None, None, "could not attach the Sweep modifier: {}".format(exc)

    used_property, error = set_property_first(
        value, ("sectionType", "SectionType"), 1, owner_label="the Sweep modifier"
    )
    if error:
        detach_modifier(runtime, path_node, index)
        return None, None, error

    ok, _used_method, error = call_first(
        value,
        ("AddShape", "addShape"),
        ((profile_node,),),
        owner_label="the Sweep modifier",
    )
    if not ok:
        detach_modifier(runtime, path_node, index)
        return None, None, error

    error = update_shape(runtime, path_node)
    if error:
        detach_modifier(runtime, path_node, index)
        return None, None, error

    registered = _section_registered(value, profile_node)
    if registered is not True:
        detach_modifier(runtime, path_node, index)
        return (
            None,
            None,
            "the Sweep modifier does not report the profile as a section, so the sweep cannot be confirmed",
        )

    return value, index, None


def _section_registered(modifier: Any, profile_node: Any) -> Optional[bool]:
    """Return True/False when the section is verifiable, ``None`` when it is not."""
    found, value = read_property_first(modifier, ("Shapes", "shapes", "NumShapes", "numShapes"))
    if not found:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) >= 1.0
    try:
        entries = list(value)
    except TypeError:
        return None
    wanted = getattr(profile_node, "handle", None)
    for entry in entries:
        if getattr(entry, "handle", None) == wanted:
            return True
    return False if entries else None


@with_max
def main(
    action: str = "list",
    name: Optional[str] = None,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    profile: str = "rounded_rect",
    operation: str = "profile",
    path_node: Optional[str] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
    corner_radius: Optional[float] = None,
    corner_segments: Optional[int] = None,
    radius: Optional[float] = None,
    segments: Optional[int] = None,
    points: Optional[Sequence[Sequence[float]]] = None,
    closed: Optional[bool] = None,
    knot_type: str = "corner",
    curve_type: str = "line",
    delete_node: bool = False,
) -> Dict[str, Any]:
    """Create, read, update, list, or delete a named curve model.

    A model is a profile spline plus the parameters that produced it. The
    parameters are stored on the node, so a later call can rebuild or inspect
    the profile without the caller repeating them.
    """
    try:
        normalized_action = str(action or "list").strip().lower()
        if normalized_action not in _ACTIONS:
            raise ValueError("action must be one of {}".format(", ".join(_ACTIONS)))
        normalized_name = validated_name(name)
        normalized_node_name = validated_name(node_name, "node_name")
        normalized_profile = str(profile or "rounded_rect").strip().lower()
        if normalized_profile not in PROFILE_KINDS:
            raise ValueError("profile must be one of {}".format(", ".join(PROFILE_KINDS)))
        normalized_operation = str(operation or "profile").strip().lower()
        if normalized_operation not in _OPERATIONS:
            raise ValueError("operation must be one of {}".format(", ".join(_OPERATIONS)))
        normalized_knot_type = validated_knot_type(knot_type)
        normalized_curve_type = str(curve_type or "line").strip().lower()
        if normalized_curve_type not in ("line", "curve"):
            raise ValueError("curve_type must be one of line, curve")
        normalized_delete_node = validated_bool(delete_node, "delete_node")
        if normalized_action in ("create", "update") and normalized_name is None:
            raise ValueError("name is required for {}".format(normalized_action))
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()

    if normalized_action == "list":
        models = iter_scene_shapes(rt, CURVE_MODEL_PROPERTY)
        return curve_success(
            "Listed {} curve model(s)".format(len(models)),
            models=models,
            count=len(models),
        )

    if normalized_action == "read":
        node, error = resolve_shape(rt, node_name=normalized_node_name, handle=handle)
        if error:
            return error
        params = load_params(rt, node, CURVE_MODEL_PROPERTY)
        if params is None:
            return curve_error(
                "the node carries no curve model parameters",
                node=node_identity(node),
                property_name=CURVE_MODEL_PROPERTY,
            )
        payload = {"node": node_identity(node)}
        payload.update(params)
        return curve_success("Read curve model", **payload)

    if normalized_action == "delete":
        node, error = resolve_shape(rt, node_name=normalized_node_name, handle=handle)
        if error:
            return error
        params = load_params(rt, node, CURVE_MODEL_PROPERTY) or {}
        entry = {"node": node_identity(node), "name": params.get("name")}
        if not normalized_delete_node:
            removed = clear_params(rt, node, CURVE_MODEL_PROPERTY)
            entry.update({"removed": removed, "delete_node": False})
            if not removed:
                return curve_error(
                    "the curve model parameters could not be cleared from the node", **entry
                )
            return curve_success(
                "Cleared the curve model parameters; the spline node was kept",
                **entry
            )
        removed_node = remove_scene_node(rt, node)
        entry.update({"removed": removed_node, "delete_node": True})
        if not removed_node:
            return curve_error("the curve model node could not be deleted", **entry)
        return curve_success("Deleted the curve model node", **entry)

    # create / update
    raw_params: Dict[str, Any] = {
        "profile": normalized_profile,
        "operation": normalized_operation,
        "width": width,
        "height": height,
        "corner_radius": corner_radius,
        "corner_segments": corner_segments,
        "radius": radius,
        "segments": segments,
        "points": points,
        "closed": closed,
        "knot_type": normalized_knot_type,
        "curve_type": normalized_curve_type,
        "path_node": path_node,
    }
    try:
        world_points, closed_flag, warning = _profile_points(normalized_profile, raw_params)
    except ValueError as exc:
        return _validation_error(str(exc))
    if closed is not None:
        closed_flag = bool(closed)

    node = None
    created_node = False
    target_spline = 1
    try:
        if normalized_action == "update":
            node, error = resolve_shape(rt, node_name=normalized_node_name, handle=handle)
            if error:
                return error
            from dcc_mcp_3dsmax._curve_utils import spline_count

            count, count_error = spline_count(rt, node)
            if count_error:
                return curve_error(count_error)
            if count < 1:
                return curve_error("the target shape has no spline to update")
            # deleteSpline is the only destructive step in an update, so the
            # replacement is built and verified first and the old spline is
            # dropped last. A failure anywhere before that leaves the shape
            # exactly as it was, and the rebuilt spline still lands at index
            # `count` afterwards, because deleteSpline shifts the higher
            # indices down by one.
            from dcc_mcp_3dsmax._curve_utils import append_knots, world_to_object

            delete_spline = getattr(rt, "deleteSpline", None)
            add_new_spline = getattr(rt, "addNewSpline", None)
            if not callable(delete_spline) or not callable(add_new_spline):
                return curve_error("3ds Max does not expose deleteSpline/addNewSpline")

            # Every point is mapped into object space before any mutation, so
            # a mapping failure cannot leave a half-replaced shape behind.
            local_points = []
            for point in world_points:
                local, map_error = world_to_object(rt, node, point)
                if map_error:
                    return curve_error(map_error)
                local_points.append(local)

            add_new_spline(node)
            target_spline = count + 1

            def _abort_replacement(message: str, **details: Any) -> Dict[str, Any]:
                """Drop the half-built spline and fail with the original intact."""
                try:
                    delete_spline(node, target_spline)
                    discarded = True
                except Exception:  # noqa: BLE001 - the discard result is reported.
                    discarded = False
                details.setdefault("node", node_identity(node))
                details["discarded_replacement"] = discarded
                return curve_error(message, **details)

            for local in local_points:
                knot_error = append_knots(
                    rt, node, target_spline, [local], normalized_knot_type, curve_type=normalized_curve_type
                )
                if knot_error:
                    return _abort_replacement(knot_error)
            if closed_flag:
                closer = getattr(rt, "closeSpline", None)
                if not callable(closer):
                    return _abort_replacement("3ds Max does not expose closeSpline")
                closer(node, target_spline)
            update_error = update_shape(rt, node)
            if update_error:
                return _abort_replacement(update_error)

            stale, verify_error = verify_world_points(
                rt, node, target_spline, world_points, expected_closed=closed_flag
            )
            if verify_error:
                return _abort_replacement(verify_error)
            if stale:
                return _abort_replacement(
                    "the regenerated profile did not pass world-space readback", mismatches=stale
                )

            # Verified, so the old spline can be dropped and the index re-checked.
            delete_spline(node, 1)
            target_spline = count
            final, final_error = verify_world_points(
                rt, node, target_spline, world_points, expected_closed=closed_flag
            )
            if final_error:
                return curve_error(final_error)
            if final:
                return curve_error(
                    "the regenerated profile did not pass world-space readback after the replacement",
                    node=node_identity(node),
                    mismatches=final,
                )
        else:
            node, error = create_spline_shape(
                rt,
                world_points=world_points,
                name=None,
                closed=closed_flag,
                knot_type=normalized_knot_type,
                curve_type=normalized_curve_type,
            )
            created_node = node is not None
            if error:
                if created_node:
                    remove_scene_node(rt, node)
                return curve_error(error)
    except Exception as exc:  # noqa: BLE001 - host failures roll the created node back.
        if created_node and node is not None:
            remove_scene_node(rt, node)
        return curve_error(
            "curve_model failed while building the profile",
            exception_type=type(exc).__name__,
            exception=str(exc),
        )

    mismatches: List[Dict[str, Any]] = []
    error = None
    if normalized_action == "create":
        mismatches, error = verify_world_points(
            rt, node, target_spline, world_points, expected_closed=closed_flag
        )
    if error:
        if created_node:
            remove_scene_node(rt, node)
        return curve_error(error)
    if mismatches:
        rolled_back = remove_scene_node(rt, node) if created_node else False
        return curve_error(
            "the curve model profile did not pass world-space readback",
            node=node_identity(node),
            mismatches=mismatches,
            rolled_back=rolled_back,
        )

    if normalized_name is not None:
        try:
            node.name = normalized_name
        except Exception as exc:  # noqa: BLE001 - a naming failure is a hard failure.
            if created_node:
                remove_scene_node(rt, node)
            return curve_error("could not name the curve model node: {}".format(exc))

    sweep_summary: Dict[str, Any] = {"attached": False}
    if normalized_operation == "sweep":
        if not path_node:
            if created_node:
                remove_scene_node(rt, node)
            return curve_error("path_node is required when operation is sweep")
        path_object, error = resolve_shape(rt, node_name=str(path_node))
        if error:
            if created_node:
                remove_scene_node(rt, node)
            return error
        modifier, index, error = _attach_sweep(rt, path_object, node)
        if error:
            if created_node:
                remove_scene_node(rt, node)
            return curve_error(error)
        sweep_summary = {
            "attached": True,
            "path_node": node_identity(path_object),
            "modifier_index": index,
            "modifier_name": str(getattr(modifier, "name", "") or "Sweep"),
        }

    persisted = {
        "name": normalized_name,
        "profile": normalized_profile,
        "operation": normalized_operation,
        "closed": closed_flag,
        "knot_type": normalized_knot_type,
        "curve_type": normalized_curve_type,
        "point_count": len(world_points),
    }
    if normalized_profile == "polyline":
        persisted["points"] = world_points
    else:
        for key in ("width", "height", "corner_radius", "corner_segments", "radius", "segments"):
            if raw_params.get(key) is not None:
                persisted[key] = raw_params[key]
    if path_node:
        persisted["path_node"] = str(path_node)

    stored, store_error = store_params(rt, node, CURVE_MODEL_PROPERTY, persisted)
    if not stored:
        if created_node:
            remove_scene_node(rt, node)
        return curve_error(
            "the curve model parameters could not be stored on the node",
            node=node_identity(node),
            store_error=store_error,
        )

    data: Dict[str, Any] = {
        "node": node_identity(node),
        "action": normalized_action,
        "profile": normalized_profile,
        "operation": normalized_operation,
        "closed": closed_flag,
        "point_count": len(world_points),
        "points": world_points,
        "params_stored": True,
        "sweep": sweep_summary,
    }
    if warning:
        data["warnings"] = [warning]
    return curve_success(
        "{} curve model: {}".format("Created" if normalized_action == "create" else "Updated", str(node.name)),
        **data
    )
