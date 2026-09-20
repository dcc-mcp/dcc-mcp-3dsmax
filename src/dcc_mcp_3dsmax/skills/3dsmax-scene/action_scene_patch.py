"""Preflight and apply a batch of mechanical node edits in one undo step.

``scene_patch`` exists for the pattern that no single-property tool covers well:
an agent that has computed N mechanical node edits and wants them applied as one
atomic, reversible operation. Two properties make it safe to call:

**Preflight.** Every edit is resolved and validated against the live scene
*before* the first write. A bad node reference, a missing property, or a value
the property cannot accept fails the whole call and nothing is written, so a
partial application can never be reported as a success.

**Single undo step.** The writes run inside :func:`_undo_utils.undo_step`, the
adapter-side equivalent of the MAXScript ``undo "label" ( ...)`` wrapper. One
call therefore leaves one host undo entry regardless of how many edits it
applied, and a failure part-way through cancels the hold so the host rolls the
whole batch back.

The hold is a capability, not a given. Hosts without ``theHold.Begin``/
``Accept``/``Cancel`` cannot group the batch, so the tool refuses to run unless
``allow_ungrouped=true`` explicitly accepts per-edit undo entries - and even
then the result says so.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dcc_mcp_3dsmax import _undo_utils
from dcc_mcp_3dsmax._scene_utils import (
    build_property_value,
    coerce_vector3,
    node_identity,
    node_visible,
    point3_to_list,
    property_values_match,
    read_property,
    resolve_node_object,
    serialize_property_value,
    set_node_visible,
    vector_equal,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

# Matches the 256-edit ceiling of the batch API this tool closes the gap with.
MAX_EDITS = 256
MAX_NODE_NAME_LENGTH = 256

# Properties the adapter treats as read-only node identity.
RESERVED_PROPERTIES = frozenset({"handle", "inode"})

OP_SET_PROPERTY = "set_property"
OP_RENAME = "rename"
OP_SET_POSITION = "set_position"
OP_SET_VISIBILITY = "set_visibility"
OPERATIONS = (OP_SET_PROPERTY, OP_RENAME, OP_SET_POSITION, OP_SET_VISIBILITY)


class _PatchRequestError(ValueError):
    """A malformed request: raised during normalization, before any host call."""


class _PatchFailure(Exception):
    """A host write that failed or did not verify; aborts and rolls back."""

    def __init__(self, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data or {}


# ── normalization ──────────────────────────────────────────────────────


def _coerce_handle(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _PatchRequestError("handle must be an integer, not a boolean")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise _PatchRequestError("handle must be an integer, received {!r}".format(value))


def _normalize_edits(edits: Any) -> List[Dict[str, Any]]:
    """Validate the request shape and return one normalized record per edit.

    Only the request itself is inspected here - no host call is made - so a
    malformed batch is rejected before the scene is touched.
    """
    if not isinstance(edits, list) or not edits:
        raise _PatchRequestError("edits must be a non-empty array")
    if len(edits) > MAX_EDITS:
        raise _PatchRequestError(
            "edits must contain at most {} entries, received {}".format(MAX_EDITS, len(edits))
        )

    normalized: List[Dict[str, Any]] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise _PatchRequestError("edit {} must be an object".format(index))

        op = str(edit.get("op") or "").strip().lower()
        if op not in OPERATIONS:
            raise _PatchRequestError(
                "edit {}: unsupported op {!r}, expected one of {}".format(
                    index, edit.get("op"), ", ".join(OPERATIONS)
                )
            )

        node_name = edit.get("node_name")
        if node_name is not None and (not isinstance(node_name, str) or not node_name.strip()):
            raise _PatchRequestError("edit {}: node_name must be a non-empty string".format(index))
        handle = _coerce_handle(edit.get("handle"))
        if node_name is None and handle is None:
            raise _PatchRequestError("edit {}: node_name or handle is required".format(index))

        normalized.append(
            {
                "index": index,
                "op": op,
                "node_name": None if node_name is None else str(node_name).strip(),
                "handle": handle,
                "value": edit.get("value"),
                "name": edit.get("name"),
                "position": edit.get("position"),
                "visible": edit.get("visible"),
                "property": edit.get("property"),
            }
        )
    return normalized


def _target_key(edit: Dict[str, Any]) -> Tuple[str, ...]:
    """Return the collision key: two edits on one target with one op."""
    return (str(edit.get("_identity_key")), edit["op"], str(edit.get("property") or ""))


# ── preflight ──────────────────────────────────────────────────────────


def _prepare_set_property(runtime: Any, node: Any, edit: Dict[str, Any]) -> Dict[str, Any]:
    name = edit.get("property")
    if not isinstance(name, str) or not name.strip():
        raise _PatchRequestError("edit {}: property is required".format(edit["index"]))
    name = name.strip()
    if name.startswith("_"):
        raise _PatchRequestError("edit {}: refusing to write private property {}".format(edit["index"], name))
    if name in RESERVED_PROPERTIES:
        raise _PatchRequestError("edit {}: property {} is read-only".format(edit["index"], name))

    try:
        current_value = getattr(node, name)
    except AttributeError as exc:
        raise _PatchRequestError(
            "edit {}: property {} does not exist on this node".format(edit["index"], name)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a host read failure is a preflight failure
        raise _PatchRequestError(
            "edit {}: property {} could not be read before writing ({})".format(edit["index"], name, exc)
        ) from exc
    if callable(current_value):
        raise _PatchRequestError("edit {}: refusing to overwrite method {}".format(edit["index"], name))

    before = read_property(node, name)
    if not before.get("available"):
        raise _PatchRequestError("edit {}: property {} is not readable on this node".format(edit["index"], name))

    try:
        target = build_property_value(runtime, current_value, edit.get("value"), name)
    except ValueError as exc:
        raise _PatchRequestError("edit {}: {}".format(edit["index"], exc)) from exc

    return {
        "index": edit["index"],
        "op": OP_SET_PROPERTY,
        "node": node,
        "identity": node_identity(node),
        "property": name,
        "target": target,
        "before": before.get("value"),
        "expected": serialize_property_value(target),
    }


def _prepare_rename(node: Any, edit: Dict[str, Any]) -> Dict[str, Any]:
    name = edit.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _PatchRequestError("edit {}: name must be a non-empty string".format(edit["index"]))
    name = name.strip()
    if "\x00" in name:
        raise _PatchRequestError("edit {}: name must not contain a null character".format(edit["index"]))
    if len(name) > MAX_NODE_NAME_LENGTH:
        raise _PatchRequestError(
            "edit {}: name must be at most {} characters".format(edit["index"], MAX_NODE_NAME_LENGTH)
        )
    return {
        "index": edit["index"],
        "op": OP_RENAME,
        "node": node,
        "identity": node_identity(node),
        "property": "name",
        "target": name,
        "before": str(getattr(node, "name", "")),
        "expected": name,
    }


def _prepare_set_position(edit: Dict[str, Any]) -> Dict[str, Any]:
    try:
        position = coerce_vector3(edit.get("position"), "position")
    except ValueError as exc:
        raise _PatchRequestError("edit {}: {}".format(edit["index"], exc)) from exc
    if position is None:
        raise _PatchRequestError("edit {}: position is required".format(edit["index"]))
    node = edit["_node"]
    return {
        "index": edit["index"],
        "op": OP_SET_POSITION,
        "node": node,
        "identity": node_identity(node),
        "property": "pos",
        "target": position,
        "before": point3_to_list(getattr(node, "pos", None)),
        "expected": list(position),
    }


def _prepare_set_visibility(node: Any, edit: Dict[str, Any]) -> Dict[str, Any]:
    visible = edit.get("visible")
    if not isinstance(visible, bool):
        raise _PatchRequestError("edit {}: visible must be a boolean".format(edit["index"]))
    return {
        "index": edit["index"],
        "op": OP_SET_VISIBILITY,
        "node": node,
        "identity": node_identity(node),
        "property": "isHidden",
        "target": visible,
        "before": node_visible(node),
        "expected": visible,
    }


def _preflight(
    runtime: Any, normalized: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve and validate every edit; return ``(plan, errors)``.

    Nothing is written. ``errors`` is empty only when every edit resolved to a
    concrete target the host is expected to accept.
    """
    plan: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, ...], int] = {}

    for edit in normalized:
        index = edit["index"]
        try:
            result, node = resolve_node_object(runtime, node_name=edit["node_name"], handle=edit["handle"])
            if node is None:
                raise _PatchRequestError(
                    "edit {}: {}".format(index, result.get("message") or "no matching node found")
                )
            edit["_node"] = node
            edit["_identity_key"] = "{}:{}".format(
                node_identity(node).get("object_id"), node_identity(node).get("node_name")
            )

            key = _target_key(edit)
            if key in seen:
                raise _PatchRequestError(
                    "edit {}: duplicates edit {} (same node, op, and property)".format(index, seen[key])
                )

            if edit["op"] == OP_SET_PROPERTY:
                prepared = _prepare_set_property(runtime, node, edit)
            elif edit["op"] == OP_RENAME:
                prepared = _prepare_rename(node, edit)
            elif edit["op"] == OP_SET_POSITION:
                prepared = _prepare_set_position(edit)
            else:
                prepared = _prepare_set_visibility(node, edit)

            seen[key] = index
            plan.append(prepared)
        except _PatchRequestError as exc:
            errors.append({"index": index, "op": edit["op"], "message": str(exc)})

    return plan, errors


# ── apply ──────────────────────────────────────────────────────────────


def _apply_set_property(runtime: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    node = item["node"]
    name = item["property"]
    try:
        setattr(node, name, item["target"])
    except Exception as exc:  # noqa: BLE001 - a host rejection must abort the batch
        raise _PatchFailure(
            "3ds Max rejected the property value",
            {"index": item["index"], "property": name, "error": "{}: {}".format(type(exc).__name__, exc)},
        ) from exc
    try:
        readback = getattr(node, name)
    except Exception as exc:  # noqa: BLE001
        raise _PatchFailure(
            "Property readback failed after writing",
            {"index": item["index"], "property": name, "error": "{}: {}".format(type(exc).__name__, exc)},
        ) from exc
    if not property_values_match(readback, item["target"]):
        raise _PatchFailure(
            "Property write did not take effect",
            {
                "index": item["index"],
                "property": name,
                "requested": item["expected"],
                "readback": serialize_property_value(readback),
            },
        )
    after = read_property(node, name)
    return {
        "index": item["index"],
        "op": OP_SET_PROPERTY,
        "node": node_identity(node),
        "property": name,
        "before": item["before"],
        "after": after.get("value"),
        "verified": True,
    }


def _apply_rename(item: Dict[str, Any]) -> Dict[str, Any]:
    node = item["node"]
    try:
        node.name = item["target"]
    except Exception as exc:  # noqa: BLE001
        raise _PatchFailure(
            "3ds Max rejected the node rename",
            {"index": item["index"], "target": item["target"], "error": "{}: {}".format(type(exc).__name__, exc)},
        ) from exc
    readback = str(getattr(node, "name", ""))
    if readback != item["target"]:
        raise _PatchFailure(
            "Rename did not take effect",
            {"index": item["index"], "target": item["target"], "readback": readback},
        )
    return {
        "index": item["index"],
        "op": OP_RENAME,
        "node": node_identity(node),
        "property": "name",
        "before": item["before"],
        "after": readback,
        "verified": True,
    }


def _apply_set_position(runtime: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    node = item["node"]
    target = item["target"]
    try:
        node.pos = runtime.Point3(target[0], target[1], target[2])
    except Exception as exc:  # noqa: BLE001
        raise _PatchFailure(
            "3ds Max rejected the position value",
            {"index": item["index"], "requested": list(target), "error": "{}: {}".format(type(exc).__name__, exc)},
        ) from exc
    readback = point3_to_list(getattr(node, "pos", None))
    if readback is None:
        raise _PatchFailure(
            "Position could not be read back after writing", {"index": item["index"], "requested": list(target)}
        )
    if not vector_equal(readback, target):
        raise _PatchFailure(
            "Position write did not take effect",
            {"index": item["index"], "requested": list(target), "readback": readback},
        )
    return {
        "index": item["index"],
        "op": OP_SET_POSITION,
        "node": node_identity(node),
        "property": "pos",
        "before": item["before"],
        "after": readback,
        "verified": True,
    }


def _apply_set_visibility(runtime: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    node = item["node"]
    try:
        set_node_visible(runtime, node, item["target"])
    except Exception as exc:  # noqa: BLE001
        raise _PatchFailure(
            "3ds Max rejected the visibility change",
            {"index": item["index"], "requested": item["target"], "error": "{}: {}".format(type(exc).__name__, exc)},
        ) from exc
    readback = node_visible(node)
    if readback is not item["target"]:
        raise _PatchFailure(
            "Visibility write did not take effect",
            {"index": item["index"], "requested": item["target"], "readback": readback},
        )
    return {
        "index": item["index"],
        "op": OP_SET_VISIBILITY,
        "node": node_identity(node),
        "property": "isHidden",
        "before": item["before"],
        "after": readback,
        "verified": True,
    }


def _apply(runtime: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    if item["op"] == OP_SET_PROPERTY:
        return _apply_set_property(runtime, item)
    if item["op"] == OP_RENAME:
        return _apply_rename(item)
    if item["op"] == OP_SET_POSITION:
        return _apply_set_position(runtime, item)
    return _apply_set_visibility(runtime, item)


# ── entry point ────────────────────────────────────────────────────────


@with_max
def main(
    edits: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
    allow_ungrouped: bool = False,
    label: str = "scene patch",
) -> Dict[str, Any]:
    """Apply up to 256 mechanical node edits as one atomic, reversible batch.

    Every edit is resolved and validated before the first write, so a rejected
    batch leaves the scene untouched. The accepted edits are then written inside
    a single host undo hold: one call leaves one undo entry, and a failure
    part-way through cancels the hold so the host rolls the whole batch back.

    ``dry_run`` runs the preflight and reports the planned change per edit
    without writing. Hosts that cannot open an undo hold refuse the batch unless
    ``allow_ungrouped`` is true, in which case the result says the edits were
    not grouped.
    """
    try:
        normalized = _normalize_edits(edits)
    except _PatchRequestError as exc:
        return {"success": False, "message": str(exc), "data": {"requested": 0, "applied": 0, "errors": []}}

    rt = get_runtime()
    plan, errors = _preflight(rt, normalized)
    requested = len(normalized)

    if errors:
        return {
            "success": False,
            "message": "{} of {} edit(s) failed preflight; nothing was applied".format(len(errors), requested),
            "data": {
                "requested": requested,
                "applied": 0,
                "passed_preflight": len(plan),
                "errors": errors,
                "undo": {
                    "supported": True,
                    "granularity": _undo_utils.GRANULARITY_SINGLE_CALL,
                    "grouped": False,
                },
            },
        }

    if dry_run:
        return {
            "success": True,
            "message": "Preflight passed for {} edit(s); nothing was applied".format(len(plan)),
            "data": {
                "requested": requested,
                "applied": 0,
                "dry_run": True,
                "planned": [
                    {
                        "index": item["index"],
                        "op": item["op"],
                        "node": item["identity"],
                        "property": item["property"],
                        "before": item["before"],
                        "after": item["expected"],
                    }
                    for item in plan
                ],
            },
        }

    hold: Dict[str, Any] = {"engaged": False, "reason": ""}
    # Captured while the hold is open: a cancelled hold clears ``engaged`` on
    # its way out, so reading it afterwards would report every rollback as an
    # ungrouped batch.
    grouped = False
    try:
        with _undo_utils.undo_step(rt, label) as hold:
            grouped = bool(hold["engaged"])
            if not grouped and not allow_ungrouped:
                raise _PatchFailure(
                    "this 3ds Max host cannot group the batch into one undo step",
                    {"reason": hold["reason"], "applied": 0},
                )
            applied = [_apply(rt, item) for item in plan]
    except _PatchFailure as exc:
        data: Dict[str, Any] = {
            "requested": requested,
            "applied": 0,
            "rolled_back": grouped,
            "undo": {
                "supported": True,
                "granularity": _undo_utils.GRANULARITY_SINGLE_CALL,
                "grouped": grouped,
            },
        }
        data.update(exc.data)
        return {"success": False, "message": exc.message, "data": data}

    warnings: List[str] = []
    if not grouped:
        warnings.append(
            "the batch was not grouped into a single undo step ({}); each edit may have left its own "
            "undo entry, so undo once and re-read the nodes, repeating while the scene still differs".format(
                hold["reason"] or "the host did not open an undo hold"
            )
        )
    elif hold["reason"]:
        # The hold was opened but closing it failed, so the batch is applied
        # while its undo coverage is unproven. Say so rather than imply a clean
        # single-step grouping.
        warnings.append("the undo hold did not close cleanly: {}".format(hold["reason"]))

    data = {
        "requested": requested,
        "applied": len(applied),
        "edits": applied,
        "undo": {
            "supported": True,
            "granularity": _undo_utils.GRANULARITY_SINGLE_CALL,
            "grouped": grouped,
            "label": str(label),
            "undo_tool": _undo_utils.UNDO_TOOL,
        },
    }
    if warnings:
        data["warnings"] = warnings
    return {
        "success": True,
        "message": "Applied {} edit(s) in one undo step".format(len(applied)),
        "data": data,
    }
