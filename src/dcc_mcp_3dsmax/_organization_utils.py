"""Helpers for 3ds Max named selection sets and group operations.

Every write in this module is verified by reading the host back. A pymxs wrapper
accepts unknown attributes and unrelated calls without persisting anything, so
an operation is only reported applied when the scene state matches what was
asked for. A write the host took but that cannot be read back is reported as
``unverified`` with a warning, never as an applied change.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from dcc_mcp_3dsmax._scene_utils import (
    ATTR_APPLIED,
    ATTR_REJECTED,
    ATTR_UNVERIFIED,
    iter_scene_nodes,
    node_identity,
    resolve_node_objects,
)

# ── Response envelopes ─────────────────────────────────────────────────


def organization_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def organization_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def _row(target: str, status: str, message: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {"target": target, "status": status, "message": message}
    row.update(extra)
    return row


def summarize_organization_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Collapse per-target write results into applied / errors / warnings."""
    warnings = [row["message"] for row in results if row["status"] == ATTR_UNVERIFIED and row.get("message")]
    return {
        "applied": [row["target"] for row in results if row["status"] == ATTR_APPLIED],
        "applied_count": sum(1 for row in results if row["status"] == ATTR_APPLIED),
        "unverified": [row["target"] for row in results if row["status"] == ATTR_UNVERIFIED],
        "errors": [dict(row) for row in results if row["status"] == ATTR_REJECTED],
        "warnings": warnings,
        "results": [dict(row) for row in results],
    }


def _finish(message: str, results: Sequence[Dict[str, Any]], **data: Any) -> Dict[str, Any]:
    """Build a response that fails as soon as one target was rejected."""
    summary = summarize_organization_results(results)
    payload = dict(data)
    payload.update(summary)
    if summary["errors"]:
        return organization_error(message, **payload)
    return organization_success(message, **payload)


# ── Target resolution ──────────────────────────────────────────────────


def resolve_organization_targets(
    runtime: Any,
    *,
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    use_selection: bool = False,
    require_targets: bool = True,
) -> Dict[str, Any]:
    """Resolve nodes by name, by handle, or from the current selection."""
    if use_selection:
        try:
            selected = list(runtime.selection)
        except Exception:  # noqa: BLE001 - an unreadable selection is reported, never assumed.
            selected = []
        if not selected:
            return organization_error("Current selection is empty", errors=[], objects=[])
        return {"success": True, "status": "success", "message": "Resolved selected nodes", "objects": selected}
    if node_names or handles:
        result = resolve_node_objects(runtime, node_names=node_names, handles=handles)
        if not result.get("success"):
            return organization_error(result["message"], errors=result.get("errors", []), objects=[])
        return {"success": True, "status": "success", "message": "Resolved nodes", "objects": result["objects"]}
    if require_targets:
        return organization_error("node_names, handles, or use_selection=true is required", errors=[], objects=[])
    return {
        "success": True,
        "status": "success",
        "message": "Resolved scene nodes",
        "objects": iter_scene_nodes(runtime),
    }


def resolve_single_target(
    runtime: Any,
    *,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    label: str = "target",
) -> Dict[str, Any]:
    """Resolve exactly one node by name or handle."""
    if not node_name and handle is None:
        return organization_error("{} node_name or handle is required".format(label), errors=[], objects=[])
    result = resolve_organization_targets(
        runtime, node_names=[node_name] if node_name else None, handles=[handle] if handle is not None else None
    )
    if not result.get("success"):
        return result
    objects = result["objects"]
    if len(objects) != 1:
        return organization_error(
            "{} resolved to {} nodes, expected exactly one".format(label, len(objects)),
            errors=result.get("errors", []),
            objects=[],
        )
    return {"success": True, "status": "success", "message": "Resolved node", "objects": objects}


# ── Node comparison ────────────────────────────────────────────────────


def node_handle(node: Any) -> Optional[int]:
    """Return a node handle as ``int``, or ``None`` when it has none."""
    handle = getattr(node, "handle", None)
    if handle is None:
        return None
    try:
        return int(handle)
    except (TypeError, ValueError):
        return None


def same_node(candidate: Any, node: Any) -> bool:
    """Compare two node wrappers by identity, handle, or name."""
    if candidate is None or node is None:
        return False
    if candidate is node:
        return True
    expected = node_handle(node)
    actual = node_handle(candidate)
    if expected is not None and actual is not None:
        return expected == actual
    expected_name = str(getattr(node, "name", "") or "")
    actual_name = str(getattr(candidate, "name", "") or "")
    return bool(expected_name) and expected_name == actual_name


def handle_set(nodes: Sequence[Any]) -> Set[Any]:
    """Return a comparable identity set for a collection of nodes."""
    return set((node_handle(node), str(getattr(node, "name", "") or "")) for node in nodes)


# ── Named selection sets ───────────────────────────────────────────────


def _selection_set_container(runtime: Any) -> Any:
    return getattr(runtime, "selectionSets", None)


def _as_node_list(value: Any) -> List[Any]:
    """Normalize one selection set entry into a list of nodes."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


def _selection_set_names(runtime: Any, container: Any) -> Optional[List[str]]:
    """Read the named selection set names, or ``None`` when unreadable."""
    keys = getattr(container, "keys", None)
    if callable(keys):
        try:
            return [str(key) for key in keys()]
        except Exception:  # noqa: BLE001 - fall through to the MAXScript accessors.
            pass
    count_fn = getattr(runtime, "getNamedSelSetCount", None)
    name_fn = getattr(runtime, "getNamedSelSetName", None)
    if callable(count_fn) and callable(name_fn):
        try:
            count = int(count_fn())
        except (TypeError, ValueError):
            count = 0
        names: List[str] = []
        for index in range(1, count + 1):
            try:
                names.append(str(name_fn(index)))
            except Exception:  # noqa: BLE001 - an unreadable set name is skipped and reported.
                continue
        return names
    count = getattr(container, "count", None)
    if isinstance(count, int):
        names = []
        for index in range(count):
            try:
                item = container[index]
            except Exception:  # noqa: BLE001
                continue
            name = getattr(item, "name", None)
            names.append(str(name) if name else str(index))
        return names
    return None


def _selection_set_nodes(container: Any, name: str) -> Optional[List[Any]]:
    """Read one named selection set, or ``None`` when it cannot be read."""
    try:
        return _as_node_list(container[name])
    except Exception:  # noqa: BLE001 - reported as unverified by the caller.
        return None


def _read_selection_sets(runtime: Any) -> Tuple[Optional[List[Tuple[str, List[Any]]]], Optional[str]]:
    """Return ``(entries, error)`` for every named selection set."""
    container = _selection_set_container(runtime)
    if container is None:
        return None, "This host does not expose named selection sets"
    names = _selection_set_names(runtime, container)
    if names is None:
        return None, "Could not read the named selection set list from this host"
    entries: List[Tuple[str, List[Any]]] = []
    for name in names:
        nodes = _selection_set_nodes(container, name)
        entries.append((name, nodes if nodes is not None else []))
    return entries, None


def list_selection_sets(runtime: Any, *, include_nodes: bool = False) -> Dict[str, Any]:
    """List named selection sets and optionally their member nodes."""
    entries, error = _read_selection_sets(runtime)
    if error:
        return organization_error(error, selection_sets=[], count=0)
    assert entries is not None
    payload = []
    for name, nodes in entries:
        entry: Dict[str, Any] = {"name": name, "node_count": len(nodes)}
        if include_nodes:
            entry["nodes"] = [node_identity(node) for node in nodes]
        payload.append(entry)
    return organization_success("Listed named selection sets", selection_sets=payload, count=len(payload))


def _write_selection_set(runtime: Any, container: Any, name: str, nodes: Sequence[Any]) -> Optional[str]:
    """Store nodes under a set name. Returns an error message on failure."""
    try:
        container[name] = list(nodes)
        return None
    except Exception:  # noqa: BLE001 - fall through to the manager APIs.
        pass
    for method_name in ("addSet", "createSet", "newSet"):
        method = getattr(container, method_name, None)
        if callable(method):
            try:
                method(name, list(nodes))
                return None
            except Exception as exc:  # noqa: BLE001
                return "Could not create selection set through {}: {}".format(method_name, exc)
    return "No supported named selection set creation API was available"


def _verify_selection_set(runtime: Any, name: str, nodes: Sequence[Any]) -> Dict[str, Any]:
    """Confirm the host kept a named selection set."""
    entries, error = _read_selection_sets(runtime)
    if error:
        return {"status": ATTR_UNVERIFIED, "message": error}
    assert entries is not None
    for existing_name, existing_nodes in entries:
        if existing_name != name:
            continue
        if handle_set(existing_nodes) == handle_set(nodes):
            return {
                "status": ATTR_APPLIED,
                "message": None,
                "nodes": [node_identity(node) for node in existing_nodes],
            }
        return {
            "status": ATTR_REJECTED,
            "message": "Selection set {} holds {} node(s) after the write, {} were requested".format(
                name, len(existing_nodes), len(nodes)
            ),
            "nodes": [node_identity(node) for node in existing_nodes],
        }
    return {"status": ATTR_REJECTED, "message": "Selection set {} was not created".format(name)}


def create_selection_set(
    runtime: Any,
    *,
    name: str,
    nodes: Sequence[Any],
    replace_existing: bool = False,
) -> Dict[str, Any]:
    """Create a named selection set holding ``nodes``."""
    if not nodes:
        return organization_error("No nodes were resolved for selection set {}".format(name), errors=[])
    container = _selection_set_container(runtime)
    if container is None:
        return organization_error("This host does not expose named selection sets", errors=[])
    entries, error = _read_selection_sets(runtime)
    if error:
        return organization_error(error, errors=[])
    assert entries is not None
    existing = dict(entries)
    if name in existing and not replace_existing:
        return organization_error(
            "Named selection set {} already exists; pass replace_existing=true to overwrite it".format(name),
            errors=[],
            selection_set_name=name,
            existing_node_count=len(existing[name]),
        )
    failure = _write_selection_set(runtime, container, name, nodes)
    if failure:
        return organization_error(failure, errors=[], selection_set_name=name)
    verification = _verify_selection_set(runtime, name, nodes)
    data = {
        "selection_set_name": name,
        "requested": [node_identity(node) for node in nodes],
        "verified": verification["status"] == ATTR_APPLIED,
    }
    if "nodes" in verification:
        data["nodes"] = verification["nodes"]
    if verification["status"] == ATTR_REJECTED:
        return organization_error(
            "Could not confirm that selection set {} was written".format(name),
            errors=[{"target": name, "status": ATTR_REJECTED, "message": verification["message"]}],
            **data,
        )
    warnings = [verification["message"]] if verification["message"] else []
    return organization_success("Created named selection set {}".format(name), warnings=warnings, errors=[], **data)


def replace_selection_set(runtime: Any, *, name: str, nodes: Sequence[Any]) -> Dict[str, Any]:
    """Replace the contents of an existing named selection set."""
    if not nodes:
        return organization_error("No nodes were resolved for selection set {}".format(name), errors=[])
    container = _selection_set_container(runtime)
    if container is None:
        return organization_error("This host does not expose named selection sets", errors=[])
    entries, error = _read_selection_sets(runtime)
    if error:
        return organization_error(error, errors=[])
    assert entries is not None
    if name not in dict(entries):
        return organization_error("Named selection set {} was not found".format(name), errors=[])
    failure = _write_selection_set(runtime, container, name, nodes)
    if failure:
        return organization_error(failure, errors=[], selection_set_name=name)
    verification = _verify_selection_set(runtime, name, nodes)
    data = {
        "selection_set_name": name,
        "requested": [node_identity(node) for node in nodes],
        "verified": verification["status"] == ATTR_APPLIED,
    }
    if "nodes" in verification:
        data["nodes"] = verification["nodes"]
    if verification["status"] == ATTR_REJECTED:
        return organization_error(
            "Could not confirm that selection set {} was replaced".format(name),
            errors=[{"target": name, "status": ATTR_REJECTED, "message": verification["message"]}],
            **data,
        )
    warnings = [verification["message"]] if verification["message"] else []
    return organization_success("Replaced named selection set {}".format(name), warnings=warnings, errors=[], **data)


def _delete_selection_set(runtime: Any, container: Any, name: str) -> Optional[str]:
    """Delete one named selection set. Returns an error message on failure."""
    try:
        del container[name]
        return None
    except Exception:  # noqa: BLE001 - fall through to the MAXScript deletion APIs.
        pass
    names = _selection_set_names(runtime, container) or []
    if name in names:
        remover = getattr(runtime, "deleteItem", None)
        if callable(remover):
            try:
                remover(container, names.index(name) + 1)
                return None
            except Exception:  # noqa: BLE001 - fall through to the manager methods.
                pass
    for method_name in ("deleteSet", "removeSet"):
        method = getattr(container, method_name, None)
        if callable(method):
            try:
                method(name)
                return None
            except Exception as exc:  # noqa: BLE001
                return "Could not delete selection set through {}: {}".format(method_name, exc)
    return "No supported named selection set deletion API was available"


def delete_selection_set(runtime: Any, *, name: str) -> Dict[str, Any]:
    """Delete one named selection set by name."""
    container = _selection_set_container(runtime)
    if container is None:
        return organization_error("This host does not expose named selection sets", errors=[])
    entries, error = _read_selection_sets(runtime)
    if error:
        return organization_error(error, errors=[])
    assert entries is not None
    if name not in dict(entries):
        return organization_error("Named selection set {} was not found".format(name), errors=[], changed_count=0)
    failure = _delete_selection_set(runtime, container, name)
    if failure:
        return organization_error(failure, errors=[], selection_set_name=name)
    entries, error = _read_selection_sets(runtime)
    if error:
        return organization_success(
            "Deleted named selection set {}".format(name),
            errors=[],
            warnings=[error],
            selection_set_name=name,
            changed_count=1,
            verified=False,
        )
    assert entries is not None
    if name in dict(entries):
        return organization_error(
            "Selection set {} still exists after the delete".format(name),
            errors=[{"target": name, "status": ATTR_REJECTED, "message": "The host kept the selection set"}],
            selection_set_name=name,
            changed_count=0,
            verified=False,
        )
    return organization_success(
        "Deleted named selection set {}".format(name),
        errors=[],
        warnings=[],
        selection_set_name=name,
        changed_count=1,
        verified=True,
    )


def select_selection_set(runtime: Any, *, name: str, add: bool = False) -> Dict[str, Any]:
    """Select the nodes held by a named selection set."""
    container = _selection_set_container(runtime)
    if container is None:
        return organization_error("This host does not expose named selection sets", errors=[])
    entries, error = _read_selection_sets(runtime)
    if error:
        return organization_error(error, errors=[])
    assert entries is not None
    existing = dict(entries)
    if name not in existing:
        return organization_error("Named selection set {} was not found".format(name), errors=[])
    nodes = existing[name]
    if not nodes:
        return organization_error("Named selection set {} is empty".format(name), errors=[])
    try:
        previous = list(runtime.selection)
    except Exception:  # noqa: BLE001 - an unreadable selection cannot be verified.
        previous = None
    applier = getattr(runtime, "selectMore", None) if add else getattr(runtime, "select", None)
    if not callable(applier):
        return organization_error(
            "This host does not expose the {} selection call".format("selectMore" if add else "select"), errors=[]
        )
    try:
        if add:
            for node in nodes:
                applier(node)
        else:
            applier(nodes)
    except Exception as exc:  # noqa: BLE001 - an explicit host rejection is a failure.
        return organization_error("Could not select selection set {}: {}".format(name, exc), errors=[])
    expected = list(previous) + list(nodes) if add and previous is not None else list(nodes)
    try:
        current = list(runtime.selection)
    except Exception:  # noqa: BLE001 - reported as unverified rather than assumed.
        return organization_success(
            "Selected named selection set {}".format(name),
            errors=[],
            warnings=["Could not read back the selection to verify it"],
            selection_set_name=name,
            requested=[node_identity(node) for node in nodes],
            verified=False,
        )
    missing = handle_set(expected) - handle_set(current)
    if missing:
        return organization_error(
            "Selection set {} did not fully select: {} expected node(s) are not selected".format(name, len(missing)),
            errors=[
                {
                    "target": name,
                    "status": ATTR_REJECTED,
                    "message": "The host selected {} node(s), {} were requested".format(len(current), len(expected)),
                }
            ],
            selection_set_name=name,
            requested=[node_identity(node) for node in nodes],
            verified=False,
        )
    return organization_success(
        "Selected named selection set {}".format(name),
        errors=[],
        warnings=[],
        selection_set_name=name,
        nodes=[node_identity(node) for node in current],
        selected_count=len(current),
        verified=True,
    )


# ── Groups ─────────────────────────────────────────────────────────────


def _child_nodes(node: Any) -> List[Any]:
    children = getattr(node, "children", None)
    if children is None:
        return []
    try:
        return list(children)
    except TypeError:
        return []


def _parent_of(node: Any) -> Any:
    """Read a node's parent, or ``None`` when the value cannot be read."""
    readable, parent = read_parent(node)
    return parent if readable else None


def read_parent(node: Any) -> Tuple[bool, Any]:
    """Return ``(readable, parent)`` for a node.

    A host that does not expose ``parent`` at all cannot be verified, which is
    reported differently from a host that reads back the wrong parent.
    """
    try:
        return True, getattr(node, "parent")
    except AttributeError:
        return False, None
    except Exception:  # noqa: BLE001 - an unreadable parent is reported as unverified.
        return False, None


def ungroup_nodes(runtime: Any, *, nodes: Sequence[Any]) -> Dict[str, Any]:
    """Dissolve group heads, keeping their member nodes."""
    if not nodes:
        return organization_error("No group nodes were resolved", errors=[])
    ungrouper = getattr(runtime, "ungroup", None)
    if not callable(ungrouper):
        return organization_error("This host does not expose the ungroup call", errors=[])
    results = []
    for node in nodes:
        name = str(getattr(node, "name", "") or "")
        members = _child_nodes(node)
        member_names = [str(getattr(member, "name", "") or "") for member in members]
        try:
            ungrouper(node)
        except Exception as exc:  # noqa: BLE001 - an explicit host rejection is a failure.
            results.append(_row(name, ATTR_REJECTED, "Could not ungroup {}: {}".format(name, exc)))
            continue
        detached = [member for member in members if not same_node(_parent_of(member), node)]
        still_grouped = [member for member in members if same_node(_parent_of(member), node)]
        if members and not still_grouped:
            results.append(
                _row(
                    name,
                    ATTR_APPLIED,
                    members=member_names,
                    released_member_count=len(detached),
                )
            )
            continue
        try:
            in_scene = node in list(runtime.objects)
        except Exception:  # noqa: BLE001 - cannot determine, so report unverified.
            results.append(
                _row(name, ATTR_UNVERIFIED, "Could not read back the scene to verify the ungroup", members=member_names)
            )
            continue
        if in_scene:
            results.append(
                _row(
                    name,
                    ATTR_REJECTED,
                    "{} is still a group after the ungroup".format(name),
                    members=member_names,
                    remaining_member_count=len(still_grouped),
                )
            )
            continue
        results.append(_row(name, ATTR_APPLIED, members=member_names, released_member_count=len(members)))
    return _finish("Ungrouped nodes", results)


def set_group_open(runtime: Any, *, node: Any, open: bool) -> Dict[str, Any]:
    """Open or close one group head."""
    name = str(getattr(node, "name", "") or "")
    opener = getattr(runtime, "setGroupOpen", None)
    if not callable(opener):
        return organization_error("This host does not expose the setGroupOpen call", errors=[])
    try:
        opener(node, bool(open))
    except Exception as exc:  # noqa: BLE001 - an explicit host rejection is a failure.
        return organization_error("Could not {} group {}: {}".format("open" if open else "close", name, exc), errors=[])
    verifier = getattr(runtime, "isOpenGroupHead", None)
    readback = None
    if callable(verifier):
        try:
            readback = verifier(node)
        except Exception:  # noqa: BLE001 - fall through to the property readback.
            readback = None
    if readback is None:
        for attribute in ("groupOpen", "isOpen"):
            value = getattr(node, attribute, None)
            if isinstance(value, bool):
                readback = value
                break
    if readback is None:
        return organization_success(
            "{} group {}".format("Opened" if open else "Closed", name),
            errors=[],
            warnings=["Could not read back the group open state to verify it"],
            group=node_identity(node),
            open=bool(open),
            verified=False,
        )
    if bool(readback) != bool(open):
        return organization_error(
            "Group {} read back open={!r} after requesting open={!r}".format(name, readback, bool(open)),
            errors=[{"target": name, "status": ATTR_REJECTED, "message": "The host kept a different open state"}],
            group=node_identity(node),
            open=bool(readback),
            verified=False,
        )
    return organization_success(
        "{} group {}".format("Opened" if open else "Closed", name),
        errors=[],
        warnings=[],
        group=node_identity(node),
        open=bool(open),
        verified=True,
    )


def attach_to_group(runtime: Any, *, nodes: Sequence[Any], group: Any) -> Dict[str, Any]:
    """Attach nodes to an existing group head."""
    if not nodes:
        return organization_error("No nodes were resolved to attach", errors=[])
    group_name = str(getattr(group, "name", "") or "")
    attacher = getattr(runtime, "attachToGroup", None)
    if not callable(attacher):
        return organization_error("This host does not expose the attachToGroup call", errors=[])
    results = []
    for node in nodes:
        name = str(getattr(node, "name", "") or "")
        readable, parent = read_parent(node)
        if readable and same_node(parent, group):
            results.append(_row(name, ATTR_APPLIED, group=group_name, already_attached=True))
            continue
        errors = []
        for call in (
            lambda: attacher(node, group),
            lambda: attacher(group, node),
        ):
            try:
                call()
            except Exception as exc:  # noqa: BLE001 - try the other argument order.
                errors.append(str(exc))
                continue
            readable, parent = read_parent(node)
            if readable and same_node(parent, group):
                errors = []
                break
        if errors:
            results.append(
                _row(
                    name,
                    ATTR_REJECTED,
                    "Could not attach {} to {}: {}".format(name, group_name, errors[-1]),
                    group=group_name,
                )
            )
            continue
        readable, parent = read_parent(node)
        if not readable:
            results.append(
                _row(
                    name,
                    ATTR_UNVERIFIED,
                    "Could not read back the parent of {} to verify the attach".format(name),
                    group=group_name,
                )
            )
            continue
        if not same_node(parent, group):
            results.append(
                _row(
                    name,
                    ATTR_REJECTED,
                    "{} does not report {} as its parent after the attach".format(name, group_name),
                    group=group_name,
                    actual_parent=str(getattr(parent, "name", "") or "") if parent is not None else None,
                )
            )
            continue
        results.append(_row(name, ATTR_APPLIED, group=group_name))
    return _finish("Attached nodes to group {}".format(group_name), results, group=node_identity(group))


def detach_from_group(runtime: Any, *, nodes: Sequence[Any]) -> Dict[str, Any]:
    """Detach nodes from the group they currently belong to."""
    if not nodes:
        return organization_error("No nodes were resolved to detach", errors=[])
    detacher = getattr(runtime, "detachFromGroup", None)
    if not callable(detacher):
        return organization_error("This host does not expose the detachFromGroup call", errors=[])
    results = []
    for node in nodes:
        name = str(getattr(node, "name", "") or "")
        readable, previous_parent = read_parent(node)
        previous_group = str(getattr(previous_parent, "name", "") or "") if previous_parent is not None else None
        if not readable:
            try:
                detacher(node)
            except Exception as exc:  # noqa: BLE001 - an explicit host rejection is a failure.
                results.append(_row(name, ATTR_REJECTED, "Could not detach {}: {}".format(name, exc)))
                continue
            results.append(
                _row(
                    name,
                    ATTR_UNVERIFIED,
                    "Could not read back the parent of {} to verify the detach".format(name),
                )
            )
            continue
        if previous_parent is None:
            results.append(_row(name, ATTR_APPLIED, previous_group=None, already_detached=True))
            continue
        try:
            detacher(node)
        except Exception as exc:  # noqa: BLE001 - an explicit host rejection is a failure.
            results.append(_row(name, ATTR_REJECTED, "Could not detach {}: {}".format(name, exc)))
            continue
        readable, current_parent = read_parent(node)
        if not readable:
            results.append(
                _row(
                    name,
                    ATTR_UNVERIFIED,
                    "Could not read back the parent of {} to verify the detach".format(name),
                    previous_group=previous_group,
                )
            )
            continue
        if same_node(current_parent, previous_parent):
            results.append(
                _row(
                    name,
                    ATTR_REJECTED,
                    "{} still reports {} as its parent after the detach".format(name, previous_group),
                    previous_group=previous_group,
                )
            )
            continue
        results.append(_row(name, ATTR_APPLIED, previous_group=previous_group))
    return _finish("Detached nodes from their groups", results)
