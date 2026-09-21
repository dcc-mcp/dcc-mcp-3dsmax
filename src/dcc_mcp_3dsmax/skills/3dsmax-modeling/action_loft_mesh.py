"""Build a quad loft from matching cross-section splines, with persisted parameters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

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


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


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
    one by one and read back, so a parameter the host ignores is returned in
    ``rejected_surface_params`` rather than being reported as applied.
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
                rolled_back = delete_node(rt, loft) if created_node else False
                return curve_error(
                    error,
                    cross_section=node_identity(section),
                    rolled_back=rolled_back,
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
            rolled_back = delete_node(rt, loft) if created_node else False
            return curve_error(
                "the loft shape count cannot be read, so the cross-sections cannot be confirmed",
                requested_cross_sections=len(sections),
                rolled_back=rolled_back,
            )
        if count is not None and count != count_before + len(sections):
            rolled_back = delete_node(rt, loft) if created_node else False
            return curve_error(
                "the loft registered {} cross-sections instead of the expected {}".format(
                    count, count_before + len(sections)
                ),
                registered_before=count_before,
                requested_cross_sections=len(sections),
                registered_shape_count=count,
                rolled_back=rolled_back,
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
                rolled_back = delete_node(rt, loft) if created_node else False
                return curve_error(error, rolled_back=rolled_back)
            path_summary = {"node": node_identity(path_object), "method": used_method, "verified": True}

        applied, rejected = apply_properties(loft, surface, owner_label="the Loft object")
        if rejected and created_node:
            return curve_error(
                "the loft did not accept every surface parameter",
                rejected_surface_params=rejected,
                applied_surface_params=applied,
                rolled_back=delete_node(rt, loft),
            )

        if normalized_name is not None:
            try:
                loft.name = normalized_name
            except Exception as exc:  # noqa: BLE001 - a naming failure is a hard failure.
                return curve_error(
                    "could not name the loft node: {}".format(exc),
                    rolled_back=delete_node(rt, loft) if created_node else False,
                )

        # An update that supplies no cross-sections is a surface-parameter
        # edit; the loft object still holds the previously registered shapes,
        # so the stored record must not be overwritten with an empty list.
        previous = load_params(rt, loft, LOFT_PARAM_PROPERTY) or {}
        merged_sections = list(previous.get("cross_sections") or [])
        for entry in added:
            name = entry["node"]["node_name"]
            if name not in merged_sections:
                merged_sections.append(name)
        persisted: Dict[str, Any] = {
            "name": normalized_name or str(getattr(loft, "name", "")),
            # The node holds the union of every registered section, so the
            # stored record has to match it rather than list only this call.
            "cross_sections": merged_sections,
            "surface_params": applied,
        }
        persisted["cross_section_count"] = len(merged_sections)
        if path_node:
            persisted["path_node"] = str(path_node)

        stored, store_error = store_params(rt, loft, LOFT_PARAM_PROPERTY, persisted)
        if not stored:
            return curve_error(
                "the loft parameters could not be stored on the node",
                node=node_identity(loft),
                store_error=store_error,
                rolled_back=delete_node(rt, loft) if created_node else False,
            )
    except Exception as exc:  # noqa: BLE001 - host failures roll the new node back.
        rolled_back = delete_node(rt, loft) if (created_node and loft is not None) else False
        return curve_error(
            "loft_mesh failed",
            exception_type=type(exc).__name__,
            exception=str(exc),
            rolled_back=rolled_back,
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
    if rejected:
        data["rejected_surface_params"] = rejected
        data["warnings"] = [
            "{} surface parameter(s) were not accepted; the node was kept".format(len(rejected))
        ]
    return curve_success(
        "{} loft: {}".format("Created" if normalized_action == "create" else "Updated", str(loft.name)),
        **data
    )
