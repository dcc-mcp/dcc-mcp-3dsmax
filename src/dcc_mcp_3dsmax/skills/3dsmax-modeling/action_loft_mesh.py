"""Build a quad loft from matching cross-section splines, with persisted parameters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._curve_utils import (
    LOFT_PARAM_PROPERTY,
    apply_properties,
    call_first,
    create_scene_object,
    curve_error,
    curve_success,
    delete_node,
    is_node_like,
    iter_scene_shapes,
    load_params,
    read_count,
    resolve_shape,
    store_params,
    validated_bool,
    validated_int,
    validated_name,
)
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max

_ACTIONS = ("create", "update", "read", "list")

_SHAPE_ADDERS = ("addShape", "AddShape")
_PATH_CREATORS = ("createPath", "CreatePath")
_SHAPE_COUNTS = ("numShapes", "NumShapes")
# Probed when a partially applied update has to be taken back off an existing
# loft. The Loft interface does not document a stable removal name, so each
# candidate is tried and the shape count is re-read after every attempt.
_SHAPE_REMOVERS = ("deleteShape", "DeleteShape", "removeShape", "RemoveShape")


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _store_loft_params(
    runtime: Any,
    loft: Any,
    previous: Dict[str, Any],
    sections: Sequence[str],
    surface: Dict[str, Any],
    name: str,
    path_node: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Write the stored record for a loft and report whether the write took.

    The record is the node's previous payload with only the fields this call
    changed merged in, so a path that stops early still leaves the node
    described as it is rather than as it was.
    """
    persisted: Dict[str, Any] = dict(previous)
    persisted.update(
        {
            "name": name,
            "cross_sections": list(sections),
            "surface_params": dict(surface),
        }
    )
    persisted["cross_section_count"] = len(sections)
    if path_node:
        persisted["path_node"] = str(path_node)
    return store_params(runtime, loft, LOFT_PARAM_PROPERTY, persisted)


def _discard_added_sections(
    runtime: Any, loft: Any, expected_count: int
) -> Tuple[bool, Optional[int]]:
    """Take the sections added by this call back off an existing loft.

    Returns ``(restored, observed_count)``. An update mutates a loft the caller
    already owns, so a partial add has to be undone rather than left in place;
    when the host exposes no usable removal call the failure says so instead of
    implying the loft is untouched.
    """
    for name in _SHAPE_REMOVERS:
        for owner, pass_object in ((loft, False), (runtime, True)):
            function = getattr(owner, name, None)
            if not callable(function):
                continue
            for _attempt in range(64):
                count, verified = read_count(runtime, loft, _SHAPE_COUNTS)
                if not verified or count is None or count <= expected_count:
                    return (verified and count == expected_count), count
                try:
                    function(loft, count) if pass_object else function(count)
                except Exception:  # noqa: BLE001 - try the next candidate.
                    break
            count, verified = read_count(runtime, loft, _SHAPE_COUNTS)
            if verified and count == expected_count:
                return True, count
    count, verified = read_count(runtime, loft, _SHAPE_COUNTS)
    return (verified and count == expected_count), count


@with_max
def main(
    action: str = "create",
    cross_sections: Optional[Sequence[str]] = None,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    name: Optional[str] = None,
    path_node: Optional[str] = None,
    shape_steps: Optional[int] = None,
    path_steps: Optional[int] = None,
    optimize_shapes: Optional[bool] = None,
    optimize_path: Optional[bool] = None,
    cap_start: Optional[bool] = None,
    cap_end: Optional[bool] = None,
    smooth_length: Optional[bool] = None,
    smooth_width: Optional[bool] = None,
) -> Dict[str, Any]:
    """Loft matching cross-section splines into one surface.

    Every cross-section has to register on the loft: the shape count is read
    back, and a call that cannot confirm the count fails instead of reporting
    a loft the agent would assume is complete. Surface parameters are applied
    one by one and read back, so a parameter the host ignores fails the call
    on every action path and is returned in ``rejected_surface_params`` rather
    than being reported as applied.
    """
    try:
        normalized_action = str(action or "create").strip().lower()
        if normalized_action not in _ACTIONS:
            raise ValueError("action must be one of {}".format(", ".join(_ACTIONS)))
        normalized_name = validated_name(name)
        normalized_node_name = validated_name(node_name, "node_name")
        sections: List[str] = []
        if cross_sections is not None:
            if isinstance(cross_sections, (str, bytes)) or not isinstance(cross_sections, Sequence):
                raise ValueError("cross_sections must be an array of node names")
            if not 2 <= len(cross_sections) <= 64:
                raise ValueError("cross_sections must contain between 2 and 64 node names")
            for entry in cross_sections:
                if not isinstance(entry, str) or not entry.strip():
                    raise ValueError("cross_sections entries must be non-empty node names")
                sections.append(entry.strip())
        if normalized_action == "create" and not sections:
            raise ValueError("cross_sections is required for create")
        surface: Dict[str, Any] = {}
        if shape_steps is not None:
            surface["shape_steps"] = validated_int(shape_steps, "shape_steps", default=5, minimum=1, maximum=100)
        if path_steps is not None:
            surface["path_steps"] = validated_int(path_steps, "path_steps", default=5, minimum=1, maximum=100)
        for flag_name, raw in (
            ("optimize_shapes", optimize_shapes),
            ("optimize_path", optimize_path),
            ("cap_start", cap_start),
            ("cap_end", cap_end),
            ("smooth_length", smooth_length),
            ("smooth_width", smooth_width),
        ):
            if raw is not None:
                surface[flag_name] = validated_bool(raw, flag_name)
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()

    if normalized_action == "list":
        models = iter_scene_shapes(rt, LOFT_PARAM_PROPERTY)
        return curve_success("Listed {} loft definition(s)".format(len(models)), lofts=models, count=len(models))

    if normalized_action == "read":
        node, error = resolve_shape(rt, node_name=normalized_node_name, handle=handle)
        if error:
            return error
        params = load_params(rt, node, LOFT_PARAM_PROPERTY)
        if params is None:
            return curve_error(
                "the node carries no loft parameters",
                node=node_identity(node),
                property_name=LOFT_PARAM_PROPERTY,
            )
        payload = {"node": node_identity(node)}
        payload.update(params)
        return curve_success("Read loft parameters", **payload)

    loft = None
    created_node = False
    count_before = 0
    try:
        if normalized_action == "update":
            node, error = resolve_shape(rt, node_name=normalized_node_name, handle=handle)
            if error:
                return error
            loft = node
        else:
            loft, _used_class, error = create_scene_object(rt, ("Loft",))
            if error:
                return curve_error(error)
            if not is_node_like(loft):
                return curve_error(
                    "3ds Max did not return a scene node for the Loft constructor, so the loft cannot be placed",
                    constructor_returned=type(loft).__name__,
                )
            created_node = True

        # numShapes is cumulative, so an update needs the pre-state to compare
        # against; a freshly created loft starts from zero.
        count_before = 0
        if sections:
            baseline, baseline_verified = read_count(rt, loft, _SHAPE_COUNTS)
            if not baseline_verified:
                rolled_back = delete_node(rt, loft) if created_node else False
                return curve_error(
                    "the loft shape count cannot be read, so the cross-sections cannot be "
                    "confirmed (tried {})".format(", ".join(_SHAPE_COUNTS)),
                    rolled_back=rolled_back,
                )
            count_before = baseline

        added: List[Dict[str, Any]] = []
        for offset, section_name in enumerate(sections):
            section, error = resolve_shape(rt, node_name=section_name)
            if error:
                return error
            ok, used_method, error = call_first(
                loft,
                _SHAPE_ADDERS,
                ((section,), (section, float(offset))),
                owner_label="the Loft object",
            )
            if not ok:
                if created_node:
                    rolled_back = delete_node(rt, loft)
                    return curve_error(
                        error, cross_section=node_identity(section), rolled_back=rolled_back
                    )
                restored, observed = _discard_added_sections(rt, loft, count_before)
                return curve_error(
                    error,
                    cross_section=node_identity(section),
                    rolled_back=False,
                    restored=restored,
                    observed_shape_count=observed,
                    expected_shape_count=count_before,
                )
            added.append({"node": node_identity(section), "method": used_method})

        # numShapes is cumulative: read it before adding so an update on a loft
        # that already holds sections is compared against the right baseline.
        count = None
        if sections:
            count, verified = read_count(rt, loft, _SHAPE_COUNTS)
        else:
            verified = True
        if not verified:
            if created_node:
                return curve_error(
                    "the loft shape count cannot be read, so the cross-sections cannot be confirmed",
                    requested_cross_sections=len(sections),
                    rolled_back=delete_node(rt, loft),
                )
            restored, observed = _discard_added_sections(rt, loft, count_before)
            return curve_error(
                "the loft shape count cannot be read, so the cross-sections cannot be confirmed",
                requested_cross_sections=len(sections),
                rolled_back=False,
                restored=restored,
                observed_shape_count=observed,
                expected_shape_count=count_before,
            )
        if count is not None and count != count_before + len(sections):
            if created_node:
                return curve_error(
                    "the loft registered {} cross-sections instead of the expected {}".format(
                        count, count_before + len(sections)
                    ),
                    registered_before=count_before,
                    requested_cross_sections=len(sections),
                    registered_shape_count=count,
                    rolled_back=delete_node(rt, loft),
                )
            restored, observed = _discard_added_sections(rt, loft, count_before)
            return curve_error(
                "the loft registered {} cross-sections instead of the expected {}".format(
                    count, count_before + len(sections)
                ),
                registered_before=count_before,
                requested_cross_sections=len(sections),
                registered_shape_count=count,
                rolled_back=False,
                restored=restored,
                observed_shape_count=observed,
                expected_shape_count=count_before,
            )

        path_summary: Dict[str, Any] = {"node": None, "verified": False}
        if path_node:
            path_object, error = resolve_shape(rt, node_name=str(path_node))
            if error:
                return error
            ok, used_method, error = call_first(
                loft, _PATH_CREATORS, ((path_object,),), owner_label="the Loft object"
            )
            if not ok:
                if created_node:
                    return curve_error(error, rolled_back=delete_node(rt, loft))
                restored, observed = _discard_added_sections(rt, loft, count_before)
                return curve_error(
                    error,
                    rolled_back=False,
                    restored=restored,
                    observed_shape_count=observed,
                    expected_shape_count=count_before,
                )
            path_summary = {"node": node_identity(path_object), "method": used_method, "verified": True}

        applied, rejected = apply_properties(loft, surface, owner_label="the Loft object")
        if rejected and created_node:
            return curve_error(
                "the loft did not accept every surface parameter",
                rejected_surface_params=rejected,
                applied_surface_params=applied,
                rolled_back=delete_node(rt, loft),
            )
        if rejected:
            # A rejected surface parameter fails on every action path, so an
            # agent never has to know whether it called create or update. The
            # difference is only in what can be taken back: a create removes
            # its own new node above, while an update leaves the caller's node
            # in place and only takes the sections this call added back off it.
            restored = True
            # No baseline is read when the call supplies no cross-sections, so
            # there is no measured count to report - only the statement that
            # nothing had to be taken back off.
            observed: Optional[int] = None
            if added:
                restored, observed = _discard_added_sections(rt, loft, count_before)
            # The host keeps every parameter it accepted, so the update failing
            # on a later one cannot leave the node described by the previous
            # record: `read` would then answer with values the loft no longer
            # holds. Store what the host actually took before reporting.
            previous = load_params(rt, loft, LOFT_PARAM_PROPERTY) or {}
            merged_failed_surface = dict(previous.get("surface_params") or {})
            merged_failed_surface.update(applied)
            merged_failed_sections = list(previous.get("cross_sections") or [])
            if not restored:
                # The host kept some of the sections this call added, so they
                # belong in the record even though the call failed. Removal
                # takes the current cumulative count, so what is left is the
                # leading part of `added`; `observed` says how much of it
                # survived rather than assuming all of it did.
                retained_count = len(added)
                if observed is not None:
                    retained_count = max(0, min(len(added), observed - count_before))
                for entry in added[:retained_count]:
                    merged_failed_sections.append(entry["node"]["node_name"])
            stored, store_error = _store_loft_params(
                rt,
                loft,
                previous,
                merged_failed_sections,
                merged_failed_surface,
                name=str(getattr(loft, "name", "")),
                path_node=path_node,
            )
            failure_data: Dict[str, Any] = {
                "rejected_surface_params": rejected,
                "applied_surface_params": applied,
                "rolled_back": False,
                "restored": restored,
                "params_stored": stored,
                "store_error": store_error,
            }
            if observed is not None:
                failure_data["observed_shape_count"] = observed
                failure_data["expected_shape_count"] = count_before
            return curve_error(
                "the loft did not accept every surface parameter", **failure_data
            )

        if normalized_name is not None:
            try:
                loft.name = normalized_name
            except Exception as exc:  # noqa: BLE001 - a naming failure is a hard failure.
                if created_node or not added:
                    rolled_back = delete_node(rt, loft) if created_node else False
                    return curve_error(
                        "could not name the loft node: {}".format(exc), rolled_back=rolled_back
                    )
                restored, observed = _discard_added_sections(rt, loft, count_before)
                return curve_error(
                    "could not name the loft node: {}".format(exc),
                    rolled_back=False,
                    restored=restored,
                    observed_shape_count=observed,
                    expected_shape_count=count_before,
                )

        # An update that supplies no cross-sections is a surface-parameter
        # edit; the loft object still holds the previously registered shapes,
        # so the stored record must not be overwritten with an empty list.
        previous = load_params(rt, loft, LOFT_PARAM_PROPERTY) or {}
        # The node holds the union of everything registered on it, so the
        # stored record starts from the previous payload and merges in only the
        # fields this call supplied. Sections are appended in registration
        # order: a repeated cross-section is a real extra shape on the host
        # even though its name is already in the list.
        merged_sections = list(previous.get("cross_sections") or [])
        for entry in added:
            merged_sections.append(entry["node"]["node_name"])
        merged_surface = dict(previous.get("surface_params") or {})
        merged_surface.update(applied)

        stored, store_error = _store_loft_params(
            rt,
            loft,
            previous,
            merged_sections,
            merged_surface,
            name=normalized_name or str(getattr(loft, "name", "")),
            path_node=path_node,
        )
        if not stored:
            if created_node or not added:
                rolled_back = delete_node(rt, loft) if created_node else False
                return curve_error(
                    "the loft parameters could not be stored on the node",
                    node=node_identity(loft),
                    store_error=store_error,
                    rolled_back=rolled_back,
                )
            restored, observed = _discard_added_sections(rt, loft, count_before)
            return curve_error(
                "the loft parameters could not be stored on the node",
                node=node_identity(loft),
                store_error=store_error,
                rolled_back=False,
                restored=restored,
                observed_shape_count=observed,
                expected_shape_count=count_before,
            )
    except Exception as exc:  # noqa: BLE001 - host failures roll the new node back.
        if created_node and loft is not None:
            return curve_error(
                "loft_mesh failed",
                exception_type=type(exc).__name__,
                exception=str(exc),
                rolled_back=delete_node(rt, loft),
            )
        restored = False
        observed = None
        if loft is not None:
            restored, observed = _discard_added_sections(rt, loft, count_before)
        return curve_error(
            "loft_mesh failed",
            exception_type=type(exc).__name__,
            exception=str(exc),
            rolled_back=False,
            restored=restored,
            observed_shape_count=observed,
            expected_shape_count=count_before,
        )

    data: Dict[str, Any] = {
        "node": node_identity(loft),
        "action": normalized_action,
        "cross_sections": added,
        "cross_section_count": len(added),
        "registered_shape_count": count,
        "registered_before": count_before,
        "path": path_summary,
        "applied_surface_params": applied,
        "params_stored": True,
    }
    return curve_success(
        "{} loft: {}".format("Created" if normalized_action == "create" else "Updated", str(loft.name)),
        **data
    )
