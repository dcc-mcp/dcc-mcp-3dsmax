"""Read-side scene query helpers: hierarchy, instances, dependencies, queries.

Everything here is plain Python over a ``pymxs``-like runtime object, so the
hierarchy walk, the instance grouping, the dependency walk, and the unified
query all run - and are testable - without a 3ds Max host.

The rule these helpers share is the one the rest of the adapter keeps breaking:
**a question the host cannot answer is reported, never answered with an empty
success.** A helper either returns the data it was asked for, or raises
:class:`SceneQueryError` with a reason the action layer puts in the tool
response. A query that could only be answered partially comes back with a
``warnings`` list naming exactly what it could not resolve, so an agent never
reads "no instances" when the truth is "instance query unavailable here".
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Import local modules
from dcc_mcp_3dsmax._scene_utils import (
    is_camera_node,
    node_identity,
    node_visible,
    property_values_match,
    serialize_property_value,
)

# ── limits ─────────────────────────────────────────────────────────────

DEFAULT_QUERY_NODES = 200
MAX_QUERY_NODES = 2000
DEFAULT_HIERARCHY_NODES = 500
MAX_HIERARCHY_NODES = 2000
MAX_HIERARCHY_DEPTH = 32

QUERY_MODES = ("overview", "filter", "class", "property", "selection", "delta")
CLASS_MATCH_MODES = ("exact", "contains")

# The ``refs`` functions this adapter knows how to call, mapped to the result
# section they fill.
DEPENDENCY_SECTIONS = (
    ("direct_dependents", "dependents"),
    ("dependent_nodes", "dependentnodes"),
    ("depends_on", "dependsOn"),
)


class SceneQueryError(ValueError):
    """A request this helper refuses, or a question this host cannot answer."""


# ── small coercions ────────────────────────────────────────────────────


def _as_int(value: Any) -> Optional[int]:
    """Coerce a handle-like value to ``int``; booleans are not integers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _name_key(node: Any) -> Tuple[str, str]:
    name = _text(getattr(node, "name", ""))
    return (name.lower(), name)


def node_key(node: Any) -> Tuple[str, Any]:
    """Identify a node for graph matching: its handle, or its wrapper as a fallback.

    ``pymxs`` can hand out more than one wrapper for the same node, and
    ``resolve_node_object`` can fall back to ``getNodeByName``, which may return
    a wrapper the scene enumeration never produced. Matching on the handle makes
    parent links, cycle detection, and target lookups survive that; identity is
    only used when a host gives no handle at all.
    """
    handle = _as_int(getattr(node, "handle", None))
    return ("handle", handle) if handle is not None else ("wrapper", id(node))


def match_scene_node(nodes: Sequence[Any], target: Any) -> Optional[Any]:
    """Return the enumerated node ``target`` refers to, or ``None``.

    Callers must refuse rather than answer when this returns ``None``: a target
    wrapper the scene enumeration does not contain has no parent link and no
    children the walk can see, so answering anyway produces a result that looks
    complete and is not.
    """
    if target is None:
        return None
    wanted = node_key(target)
    for node in nodes:
        if node_key(node) == wanted:
            return node
    return None


def _clamp_limit(value: Any, default: int, maximum: int, field: str = "limit") -> int:
    """Validate a result-size limit instead of silently clamping a bad one."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneQueryError("{} must be an integer".format(field))
    if value < 1:
        raise SceneQueryError("{} must be at least 1".format(field))
    return min(value, maximum)


def scene_nodes(runtime: Any) -> List[Any]:
    """Return the scene nodes, failing loudly when they cannot be enumerated.

    ``_scene_utils.iter_scene_nodes`` answers an enumeration failure with an
    empty list, which turns "the host refused" into "the scene is empty". Every
    query in this module needs the difference to be visible, so it raises.
    """
    try:
        objects = getattr(runtime, "objects", None)
    except Exception as exc:  # noqa: BLE001
        raise SceneQueryError(
            "could not read the scene nodes: {}: {}".format(type(exc).__name__, exc)
        ) from exc
    if objects is None:
        raise SceneQueryError("this 3ds Max host exposes no objects collection, so the scene cannot be enumerated")
    try:
        return list(objects)
    except Exception as exc:  # noqa: BLE001
        raise SceneQueryError("could not enumerate scene nodes: {}: {}".format(type(exc).__name__, exc)) from exc


def scene_selection(runtime: Any) -> Tuple[List[Any], Optional[str]]:
    """Return the current selection and, when it cannot be read, why."""
    selection = getattr(runtime, "selection", None)
    if selection is None:
        return [], "this 3ds Max host exposes no selection collection"
    try:
        return list(selection), None
    except Exception as exc:  # noqa: BLE001
        return [], "could not read the selection: {}: {}".format(type(exc).__name__, exc)


# ── node description ───────────────────────────────────────────────────


def node_payload(node: Any, runtime: Any = None) -> Dict[str, Any]:
    """Identity payload shared by every node-returning query."""
    identity = node_identity(node)
    payload = {
        "node_name": identity["node_name"],
        "object_id": identity["object_id"],
        "class_name": identity["class_name"],
        "parent": identity["parent"],
        "visible": identity["visible"],
    }
    if runtime is not None:
        payload["is_camera"] = is_camera_node(node, runtime=runtime)
    return payload


def class_tokens(node: Any, runtime: Any = None) -> List[str]:
    """Lowercase class names a node can be matched on.

    The first token is the one ``node_identity`` reports, so callers can treat
    it as the canonical class. The rest are enrichment: a host whose
    ``classOf`` throws still answers class queries from the base object.
    """
    tokens: List[str] = []

    def add(value: Any) -> None:
        text = _text(value).strip().lower()
        if text and text not in tokens:
            tokens.append(text)

    add(node_identity(node)["class_name"])
    add(type(node).__name__)
    add(getattr(node, "className", None))
    if runtime is not None:
        for method_name in ("classOf", "superClassOf"):
            method = getattr(runtime, method_name, None)
            if callable(method):
                try:
                    add(method(node))
                except Exception:  # noqa: BLE001
                    # Enrichment only: the canonical token above already
                    # answers the query, so a host-specific failure here must
                    # not fail the whole call.
                    continue
    return tokens


def class_matches(node: Any, class_name: str, match_mode: str = "exact", runtime: Any = None) -> bool:
    """Match a node against a class name, case-insensitively."""
    wanted = class_name.strip().lower()
    if not wanted:
        return False
    if match_mode == "contains":
        return any(wanted in token for token in class_tokens(node, runtime))
    return wanted in class_tokens(node, runtime)


def _iter_refs(value: Any) -> List[Any]:
    """Normalize a ``refs`` result, which may be one object or a collection."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def _describe_reference(value: Any) -> Dict[str, Any]:
    """Describe a dependency target without assuming it is a scene node."""
    base = getattr(value, "baseObject", None)
    class_name = getattr(base, "__class__", None) if base is not None else None
    return {
        "node_name": _text(getattr(value, "name", "")),
        "object_id": _as_int(getattr(value, "handle", None)),
        "class_name": getattr(class_name, "__name__", None) or type(value).__name__,
    }


# ── property reads ─────────────────────────────────────────────────────


def validate_property_name(name: Any) -> str:
    """Validate a property name before it is read from a node."""
    if not isinstance(name, str) or not name.strip():
        raise SceneQueryError("property is required for this query mode")
    text = name.strip()
    if text.startswith("_"):
        raise SceneQueryError("refusing to read the private property {}".format(text))
    return text


def read_node_property(node: Any, name: str) -> Tuple[bool, Any, str]:
    """Read one property and say explicitly when the read did not happen.

    Returns ``(available, value, reason)``. A node the host refused to answer
    for is never reported as a node that simply lacks the property.
    """
    try:
        value = getattr(node, name)
    except Exception as exc:  # noqa: BLE001
        return False, None, "{}: {}".format(type(exc).__name__, exc)
    if callable(value):
        return False, None, "{} is a method, not a property".format(name)
    return True, value, ""


# ── hierarchy ──────────────────────────────────────────────────────────


class _HierarchyWalker(object):
    """Depth-first walk over parent-linked nodes.

    The walk is explicit about the three ways it can be incomplete: the node
    limit, the depth cap, and a cycle in the parent links. Each one is counted
    or named rather than dropped.
    """

    def __init__(
        self,
        children: Dict[int, List[Any]],
        *,
        limit: int,
        depth_cap: int,
        include_hidden: bool,
        runtime: Any = None,
    ) -> None:
        # ``children`` is keyed by ``id(node)``: the graph is walked by object
        # identity, so nodes without a handle still take part in it.
        self.children = children
        self.limit = limit
        self.depth_cap = depth_cap
        self.include_hidden = include_hidden
        self.runtime = runtime
        self.emitted = 0
        self.truncated = False
        self.max_depth = 0
        self.hidden_skipped = 0
        self.limit_hit = False
        self.depth_hit = False
        self.cycles: List[Dict[str, Any]] = []

    def walk(self, node: Any, depth: int = 0, path: Optional[frozenset] = None) -> Optional[Dict[str, Any]]:
        if self.emitted >= self.limit:
            self.truncated = True
            self.limit_hit = True
            return None

        visible = node_visible(node)
        if not self.include_hidden and not visible:
            # A hidden node takes its subtree with it: 3ds Max hides children
            # with their parent, so re-parenting them here would invent links.
            self.hidden_skipped += 1
            return None

        identity = node_identity(node)
        handle = identity["object_id"]
        payload: Dict[str, Any] = {
            "node_name": identity["node_name"],
            "object_id": handle,
            "class_name": identity["class_name"],
            "visible": visible,
            "is_camera": is_camera_node(node, runtime=self.runtime),
            "depth": depth,
        }
        self.emitted += 1
        self.max_depth = max(self.max_depth, depth)

        child_nodes = self.children.get(node_key(node), [])
        if depth >= self.depth_cap:
            payload["children"] = []
            payload["child_count"] = 0
            if child_nodes:
                payload["unlisted_children"] = len(child_nodes)
                self.truncated = True
                self.depth_hit = True
            return payload

        # ``seen`` is the path from the tree root down to this node, inclusive:
        # a child already on it closes a cycle and is not followed again.
        seen = (path if path is not None else frozenset()) | {node_key(node)}
        children: List[Dict[str, Any]] = []
        children_truncated = False
        for child in child_nodes:
            child_key = node_key(child)
            if child_key in seen:
                self.cycles.append(
                    {
                        "node_name": _text(getattr(child, "name", "")),
                        "parent_name": payload["node_name"],
                        "object_id": _as_int(getattr(child, "handle", None)),
                    }
                )
                self.truncated = True
                continue
            branch = self.walk(child, depth + 1, seen | {child_key})
            if branch is None:
                children_truncated = True
            else:
                children.append(branch)

        payload["children"] = children
        payload["child_count"] = len(children)
        if children_truncated:
            payload["children_truncated"] = True
        return payload


def build_hierarchy(
    nodes: Sequence[Any],
    *,
    runtime: Any = None,
    root: Any = None,
    max_depth: Any = None,
    include_hidden: bool = True,
    limit: Any = None,
) -> Dict[str, Any]:
    """Build a recursive subtree from parent links.

    ``root`` is a node object; when it is omitted every parentless node starts a
    tree. Nodes whose parent is set but absent from ``nodes`` - group heads are
    the usual case - are reported as roots and named in ``warnings`` rather than
    being dropped from the result.
    """
    safe_limit = _clamp_limit(limit, DEFAULT_HIERARCHY_NODES, MAX_HIERARCHY_NODES)
    if max_depth is None:
        depth_cap = MAX_HIERARCHY_DEPTH
    else:
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise SceneQueryError("max_depth must be an integer")
        if max_depth < 0:
            raise SceneQueryError("max_depth must be zero or greater")
        depth_cap = min(max_depth, MAX_HIERARCHY_DEPTH)

    nodes = list(nodes)
    known = {node_key(node) for node in nodes}

    children: Dict[Any, List[Any]] = {}
    roots: List[Any] = []
    parent_outside_scene: List[Dict[str, Any]] = []
    for node in nodes:
        parent = getattr(node, "parent", None)
        if parent is None or node_key(parent) not in known:
            roots.append(node)
            if parent is not None:
                parent_outside_scene.append(
                    {
                        "node_name": node_identity(node)["node_name"],
                        "object_id": _as_int(getattr(node, "handle", None)),
                        "parent_name": _text(getattr(parent, "name", "")) or None,
                    }
                )
            continue
        children.setdefault(node_key(parent), []).append(node)

    for key in children:
        children[key].sort(key=_name_key)

    if root is not None:
        roots = [root]
        parent_outside_scene = [
            entry for entry in parent_outside_scene if entry["object_id"] == _as_int(getattr(root, "handle", None))
        ]
    else:
        roots.sort(key=_name_key)

    # A parent-link cycle has no parentless member, so every node in it is
    # unreachable from the roots and would vanish from the tree. Recover them
    # as roots instead: the tree is then complete and the cycle is named.
    #
    # Only meaningful when walking the whole scene. Under an explicit root the
    # walk is already bounded by that root, and everything outside its subtree
    # is unreachable by design, not stranded.
    stranded: List[Any] = []
    if root is None:
        reachable = set()
        pending = [node_key(node) for node in roots]
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            pending.extend(node_key(child) for child in children.get(key, []))
        stranded = [node for node in nodes if node_key(node) not in reachable]
        stranded.sort(key=_name_key)

    # One representative per cycle is enough to show the whole cycle: seeding a
    # root from every member would print the same subtree once per member.
    stranded_keys = {node_key(node) for node in stranded}
    grouped = set()
    cycle_roots: List[Any] = []
    for node in stranded:
        if node_key(node) in grouped:
            continue
        component = set()
        stack = [node]
        while stack:
            current = stack.pop()
            key = node_key(current)
            if key in component:
                continue
            component.add(key)
            stack.extend(child for child in children.get(key, []) if node_key(child) in stranded_keys)
            parent = getattr(current, "parent", None)
            if parent is not None and node_key(parent) in stranded_keys:
                stack.append(parent)
        grouped |= component
        cycle_roots.append(node)

    walker = _HierarchyWalker(
        children,
        limit=safe_limit,
        depth_cap=depth_cap,
        include_hidden=bool(include_hidden),
        runtime=runtime,
    )
    tree = [branch for branch in (walker.walk(node) for node in list(roots) + cycle_roots) if branch is not None]

    warnings: List[str] = []
    if stranded:
        warnings.append(
            "{} node(s) sit in a parent-link cycle and are reported under {} root(s) that break it: {}".format(
                len(stranded),
                len(cycle_roots),
                ", ".join(_text(getattr(node, "name", "")) for node in stranded),
            )
        )
    # Each cause gets its own line: one sentence naming three possibilities
    # leaves the caller unable to tell which knob to turn.
    if walker.limit_hit:
        warnings.append(
            "the tree was cut short by the node limit ({}); raise limit to see the rest".format(safe_limit)
        )
    if walker.depth_hit:
        warnings.append(
            "the tree was cut short by the depth cap ({}); raise max_depth to see deeper levels".format(depth_cap)
        )
    if walker.hidden_skipped:
        warnings.append(
            "{} hidden node(s) and their subtrees were skipped because include_hidden is false".format(
                walker.hidden_skipped
            )
        )
    if walker.cycles:
        warnings.append(
            "{} parent link(s) form a cycle and were not followed: {}".format(
                len(walker.cycles), ", ".join(entry["node_name"] for entry in walker.cycles)
            )
        )
    if parent_outside_scene:
        warnings.append(
            "{} node(s) have a parent that is not in the scene node list and were reported as roots: {}".format(
                len(parent_outside_scene), ", ".join(entry["node_name"] for entry in parent_outside_scene)
            )
        )

    return {
        "strategy": "parent_links",
        "tree": tree,
        "root_count": len(tree),
        "node_count": walker.emitted,
        "max_depth_reached": walker.max_depth,
        "depth_cap": depth_cap,
        "truncated": walker.truncated,
        "parent_outside_scene": parent_outside_scene,
        "cycles": walker.cycles,
        "cycle_roots": [
            {
                "node_name": _text(getattr(node, "name", "")),
                "object_id": _as_int(getattr(node, "handle", None)),
            }
            for node in cycle_roots
        ],
        "cycle_members": [
            {
                "node_name": _text(getattr(node, "name", "")),
                "object_id": _as_int(getattr(node, "handle", None)),
            }
            for node in stranded
        ],
        "warnings": warnings,
    }


# ── instances ──────────────────────────────────────────────────────────


def _instance_mgr_keys(nodes: Sequence[Any], runtime: Any) -> Optional[Tuple[List[Any], List[Optional[str]]]]:
    """Key every node by its ``InstanceMgr.GetInstances`` result."""
    manager = getattr(runtime, "InstanceMgr", None) if runtime is not None else None
    if manager is None:
        return None
    getter = getattr(manager, "GetInstances", None)
    if not callable(getter):
        return None

    keys: List[Any] = []
    reasons: List[Optional[str]] = []
    for node in nodes:
        try:
            result = getter(node)
        except Exception as exc:  # noqa: BLE001
            keys.append(None)
            reasons.append("InstanceMgr.GetInstances failed: {}: {}".format(type(exc).__name__, exc))
            continue
        handles = sorted(
            handle
            for handle in (_as_int(getattr(item, "handle", None)) for item in _iter_refs(result))
            if handle is not None
        )
        if not handles:
            keys.append(None)
            reasons.append("InstanceMgr.GetInstances returned no nodes")
            continue
        keys.append(("instance_set", tuple(handles)))
        reasons.append(None)
    return keys, reasons


def _base_object_keys(nodes: Sequence[Any]) -> Tuple[List[Any], List[Optional[str]]]:
    """Key every node by the handle of the object it derives from.

    Instances of one object share that object, so they share its handle; a copy
    has its own object and therefore its own handle.
    """
    keys: List[Any] = []
    reasons: List[Optional[str]] = []
    for node in nodes:
        base = getattr(node, "baseObject", None)
        handle = _as_int(getattr(base, "handle", None)) if base is not None else None
        if handle is None:
            keys.append(None)
            reasons.append("the node exposes no base object handle")
        else:
            keys.append(("base_object", handle))
            reasons.append(None)
    return keys, reasons


def _instance_keys(nodes: Sequence[Any], runtime: Any) -> Tuple[List[Any], List[Optional[str]], str]:
    mgr_result = _instance_mgr_keys(nodes, runtime)
    if mgr_result is not None:
        keys, reasons = mgr_result
        return keys, reasons, "instance_mgr"
    keys, reasons = _base_object_keys(nodes)
    return keys, reasons, "base_object_handle"


def find_instances(
    nodes: Sequence[Any],
    *,
    runtime: Any = None,
    target: Any = None,
    include_unique: bool = False,
    limit: Any = None,
) -> Dict[str, Any]:
    """Group scene nodes into instance sets.

    A host that exposes ``InstanceMgr.GetInstances`` is asked first; otherwise
    the shared base-object handle is used as the instance key. When neither is
    available the query is refused - an empty group list would be read as "this
    scene has no instances" when the truth is "this host cannot tell".
    """
    safe_limit = _clamp_limit(limit, DEFAULT_QUERY_NODES, MAX_QUERY_NODES)
    nodes = list(nodes)
    keys, reasons, strategy = _instance_keys(nodes, runtime)

    unresolved = [
        dict(node_payload(node, runtime), reason=reason or "the instance query could not resolve this node")
        for node, key, reason in zip(nodes, keys, reasons)
        if key is None
    ]
    if nodes and len(unresolved) == len(nodes):
        raise SceneQueryError(
            "this 3ds Max host exposes no usable instance query: {}".format(unresolved[0]["reason"])
        )

    groups: Dict[Any, List[Any]] = {}
    order: List[Any] = []
    for node, key in zip(nodes, keys):
        if key is None:
            continue
        group = groups.get(key)
        if group is None:
            group = []
            groups[key] = group
            order.append(key)
        group.append(node)

    payloads: List[Dict[str, Any]] = []
    for key in order:
        members = sorted(groups[key], key=_name_key)
        if len(members) < 2 and not include_unique:
            continue
        payloads.append(
            {
                "instance_count": len(members),
                "class_name": node_identity(members[0])["class_name"],
                "nodes": [node_payload(node, runtime) for node in members[:safe_limit]],
                "count": min(len(members), safe_limit),
                "truncated": len(members) > safe_limit,
            }
        )
    payloads.sort(key=lambda item: (-item["instance_count"], item["nodes"][0]["node_name"].lower()))

    warnings: List[str] = []
    if unresolved:
        warnings.append(
            "{} of {} node(s) could not be classified and are listed in unresolved".format(
                len(unresolved), len(nodes)
            )
        )
    if strategy == "base_object_handle":
        warnings.append(
            "InstanceMgr.GetInstances is unavailable on this host, so instances were grouped by the shared "
            "base-object handle; references and instances then look alike"
        )

    data: Dict[str, Any] = {
        "strategy": strategy,
        "groups": payloads,
        "group_count": len(payloads),
        "instanced_node_count": sum(item["instance_count"] for item in payloads),
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
        "include_unique": bool(include_unique),
        "warnings": warnings,
    }

    if target is not None:
        target_key = None
        wanted = node_key(target)
        for node, key in zip(nodes, keys):
            if node_key(node) == wanted:
                target_key = key
                break
        if target_key is None:
            return dict(
                data,
                node=node_payload(target, runtime),
                group=None,
                instance_count=0,
                is_instanced=False,
                resolved=False,
            )
        members = sorted(groups.get(target_key, []), key=_name_key)
        return dict(
            data,
            node=node_payload(target, runtime),
            group={
                "instance_count": len(members),
                "class_name": node_identity(members[0])["class_name"],
                "nodes": [node_payload(node, runtime) for node in members[:safe_limit]],
                "count": min(len(members), safe_limit),
                "truncated": len(members) > safe_limit,
            },
            instance_count=len(members),
            is_instanced=len(members) > 1,
            resolved=True,
        )
    return data


# ── dependencies ───────────────────────────────────────────────────────


def collect_dependencies(runtime: Any, node: Any, *, limit: Any = None) -> Dict[str, Any]:
    """Read the dependency graph around one node through the ``refs`` interface.

    A host without ``refs`` - or whose ``refs`` calls all fail - gets a refusal,
    not an empty graph. An empty graph is the correct answer to "nothing depends
    on this", and collapsing an unavailable API into it is the silent-success
    failure mode this adapter keeps hitting.
    """
    safe_limit = _clamp_limit(limit, DEFAULT_QUERY_NODES, MAX_QUERY_NODES)
    refs = getattr(runtime, "refs", None)
    if refs is None:
        raise SceneQueryError(
            "this 3ds Max host exposes no refs interface, so dependencies cannot be resolved"
        )

    known = [name for _, name in DEPENDENCY_SECTIONS if callable(getattr(refs, name, None))]
    if not known:
        raise SceneQueryError(
            "the refs interface on this host exposes none of {}".format(", ".join(n for _, n in DEPENDENCY_SECTIONS))
        )

    sections: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []
    for section, method_name in DEPENDENCY_SECTIONS:
        method = getattr(refs, method_name, None)
        if not callable(method):
            sections[section] = {
                "available": False,
                "method": method_name,
                "count": 0,
                "items": [],
                "error": "this host does not expose refs.{}".format(method_name),
            }
            warnings.append("refs.{} is unavailable on this host".format(method_name))
            continue
        try:
            raw = method(node)
        except Exception as exc:  # noqa: BLE001
            sections[section] = {
                "available": False,
                "method": method_name,
                "count": 0,
                "items": [],
                "error": "{}: {}".format(type(exc).__name__, exc),
            }
            warnings.append("refs.{} failed: {}: {}".format(method_name, type(exc).__name__, exc))
            continue
        items = [_describe_reference(item) for item in _iter_refs(raw)]
        sections[section] = {
            "available": True,
            "method": method_name,
            "count": len(items),
            "items": items[:safe_limit],
            "truncated": len(items) > safe_limit,
        }

    if not any(section["available"] for section in sections.values()):
        first = next(message for message in warnings)
        raise SceneQueryError("every refs call failed on this host: {}".format(first))

    return {
        "strategy": "refs",
        "node": node_payload(node, runtime),
        "direct_dependents": sections["direct_dependents"],
        "dependent_nodes": sections["dependent_nodes"],
        "depends_on": sections["depends_on"],
        "warnings": warnings,
    }


# ── unified query ──────────────────────────────────────────────────────


def _unwrap_baseline_list(baseline: Dict[str, Any]) -> List[Any]:
    """Pick the node list out of a result object.

    ``overview`` and ``delta`` answer with counts, so their ``nodes`` list is an
    empty array even when ``snapshot`` carries the nodes. Taking the first key
    that happens to be a list would read that empty array as a real baseline and
    report the whole scene as newly added, silently. Prefer a list that has
    something in it.
    """
    candidates = []
    for key in ("nodes", "snapshot"):
        value = baseline.get(key)
        if isinstance(value, list):
            candidates.append(value)
    if not candidates:
        raise SceneQueryError("baseline must be an array of node entries or an object with a nodes list")
    for value in candidates:
        if value:
            return value
    return candidates[0]


def _normalize_baseline(baseline: Any) -> List[Dict[str, Any]]:
    """Normalize the snapshot a ``delta`` query is compared against."""
    if isinstance(baseline, dict):
        baseline = _unwrap_baseline_list(baseline)
    if not isinstance(baseline, list):
        raise SceneQueryError("baseline must be an array of node entries or names")

    entries: List[Dict[str, Any]] = []
    for index, item in enumerate(baseline):
        if isinstance(item, str):
            entries.append({"node_name": item, "object_id": None, "values": {}, "has_value": False, "value": None})
            continue
        if not isinstance(item, dict):
            raise SceneQueryError("baseline entry {} must be a node name or an object".format(index))
        name = item.get("node_name", item.get("name"))
        if not isinstance(name, str) or not name.strip():
            raise SceneQueryError("baseline entry {} is missing node_name".format(index))
        values = item.get("values")
        entries.append(
            {
                "node_name": name,
                "object_id": _as_int(item.get("object_id")),
                "values": values if isinstance(values, dict) else {},
                "has_value": "value" in item,
                "value": item.get("value"),
            }
        )
    return entries


def _baseline_value(entry: Dict[str, Any], property_name: str) -> Tuple[Any, bool]:
    if property_name and property_name in entry["values"]:
        return entry["values"][property_name], True
    if entry["has_value"]:
        return entry["value"], True
    return None, False


def _baseline_matches(entry: Dict[str, Any], by_id: Dict[int, Any], by_name: Dict[str, Any]) -> bool:
    """Whether a baseline entry still has a node in the current scene."""
    if entry["object_id"] is not None and entry["object_id"] in by_id:
        return True
    return entry["node_name"] in by_name


def _scope_nodes(nodes: Sequence[Any], name_filter: Any, include_hidden: bool) -> List[Any]:
    """Apply the name and visibility filters shared by every query mode."""
    if name_filter is not None and (not isinstance(name_filter, str) or len(name_filter) > 256):
        raise SceneQueryError("name_filter must be a string of at most 256 characters")
    needle = (name_filter or "").strip().lower()
    scoped = []
    for node in nodes:
        if needle and needle not in _text(getattr(node, "name", "")).lower():
            continue
        if not include_hidden and not node_visible(node):
            continue
        scoped.append(node)
    return scoped


def _overview(nodes: Sequence[Any], runtime: Any, selection: Sequence[Any]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    visible_count = 0
    for node in nodes:
        identity = node_identity(node)
        counts[identity["class_name"]] = counts.get(identity["class_name"], 0) + 1
        if node_visible(node):
            visible_count += 1
    classes = [
        {"class_name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]
    scene_name = None
    file_name = getattr(runtime, "maxFileName", None)
    if isinstance(file_name, str) and file_name.strip():
        scene_name = file_name
    return {
        "node_count": len(nodes),
        "visible_count": visible_count,
        "hidden_count": len(nodes) - visible_count,
        "selection_count": len(selection),
        "class_count": len(classes),
        "classes": classes,
        "scene_name": scene_name,
    }


def _property_query(
    nodes: Sequence[Any],
    runtime: Any,
    property_name: str,
    property_value: Any,
    safe_limit: int,
) -> Dict[str, Any]:
    matched: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    has_expected = property_value is not None
    truncated = False
    skipped_seen = 0
    skipped_reasons: set = set()
    for node in nodes:
        available, value, reason = read_node_property(node, property_name)
        if not available:
            # Reported, not dropped: "this node could not be read" is a
            # different answer from "this node does not have the property".
            skipped_seen += 1
            skipped_reasons.add(reason)
            # Bounded like the matches: an unreadable node is a finding, not a
            # licence to return an unbounded list of them.
            if len(skipped) < safe_limit:
                skipped.append({"node_name": node_identity(node)["node_name"], "reason": reason})
            else:
                truncated = True
            continue
        if has_expected and not property_values_match(value, property_value):
            continue
        if len(matched) >= safe_limit:
            truncated = True
            break
        payload = node_payload(node, runtime)
        payload["value"] = serialize_property_value(value)
        matched.append(payload)
    return {
        "property": property_name,
        "expected_value": property_value if has_expected else None,
        "nodes": matched,
        "count": len(matched),
        "skipped": skipped,
        "skipped_count": skipped_seen,
        "skipped_omitted": max(skipped_seen - len(skipped), 0),
        "skipped_reasons": sorted(skipped_reasons),
        "truncated": truncated,
    }


def _delta_query(
    nodes: Sequence[Any],
    runtime: Any,
    baseline: Any,
    property_name: Optional[str],
) -> Dict[str, Any]:
    entries = _normalize_baseline(baseline)
    # Indexed both ways: the handle survives a rename, and the name is the only
    # thing a hand-written or name-only baseline carries. Matching on the handle
    # alone would report a rename as a removal plus an addition; matching on the
    # name alone would report every name-only baseline entry as removed.
    by_id: Dict[int, Any] = {}
    by_name: Dict[str, Any] = {}
    ambiguous: List[str] = []
    for node in nodes:
        identity = node_identity(node)
        if identity["object_id"] is not None:
            if identity["object_id"] in by_id:
                ambiguous.append(identity["node_name"])
            else:
                by_id[identity["object_id"]] = node
        if identity["node_name"] in by_name:
            ambiguous.append(identity["node_name"])
        else:
            by_name[identity["node_name"]] = node

    added: List[Dict[str, Any]] = []
    renamed: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    unreadable: List[Dict[str, Any]] = []
    unchanged = 0
    matched: set = set()
    comparable = 0

    for entry in entries:
        node = None
        if entry["object_id"] is not None:
            node = by_id.get(entry["object_id"])
        if node is None:
            node = by_name.get(entry["node_name"])
        if node is None:
            continue
        matched.add(id(node))
        identity = node_identity(node)
        if identity["node_name"] != entry["node_name"]:
            renamed.append(
                {
                    "before": entry["node_name"],
                    "after": identity["node_name"],
                    "object_id": identity["object_id"],
                }
            )
        if property_name:
            before, had_value = _baseline_value(entry, property_name)
            if had_value:
                comparable += 1
                available, value, reason = read_node_property(node, property_name)
                if not available:
                    # Its own bucket, as in property mode: a node that could not
                    # be read is not a node whose value changed, and folding it
                    # into changed makes changed_count count read failures.
                    unreadable.append(
                        {
                            "node_name": identity["node_name"],
                            "object_id": identity["object_id"],
                            "before": before,
                            "reason": reason,
                        }
                    )
                elif not property_values_match(value, before):
                    changed.append(
                        {
                            "node_name": identity["node_name"],
                            "object_id": identity["object_id"],
                            "before": before,
                            "after": serialize_property_value(value),
                        }
                    )
                else:
                    unchanged += 1
            else:
                unchanged += 1
        else:
            unchanged += 1

    for node in nodes:
        if id(node) not in matched:
            added.append(node_payload(node, runtime))

    removed = [
        {
            "node_name": entry["node_name"],
            "object_id": entry["object_id"],
        }
        for entry in entries
        if not _baseline_matches(entry, by_id, by_name)
    ]

    added.sort(key=lambda item: item["node_name"].lower())
    removed.sort(key=lambda item: item["node_name"].lower())
    renamed.sort(key=lambda item: item["before"].lower())
    changed.sort(key=lambda item: item["node_name"].lower())

    return {
        "baseline_count": len(entries),
        "current_count": len(nodes),
        "added": added,
        "added_count": len(added),
        "removed": removed,
        "removed_count": len(removed),
        "renamed": renamed,
        "renamed_count": len(renamed),
        "changed": changed,
        "changed_count": len(changed),
        "unreadable": unreadable,
        "unreadable_count": len(unreadable),
        "unchanged_count": unchanged,
        "compared_property": property_name,
        "comparable_count": comparable,
        "ambiguous": ambiguous,
    }


def run_scene_query(
    runtime: Any,
    *,
    mode: str,
    name_filter: Any = None,
    class_name: Any = None,
    class_match: str = "exact",
    property_name: Any = None,
    property_value: Any = None,
    include_hidden: bool = True,
    limit: Any = None,
    baseline: Any = None,
    include_snapshot: bool = False,
) -> Dict[str, Any]:
    """Answer one of the six scene queries against the live scene.

    The mode decides what is returned, but every mode shares the filters, the
    node limit, and the rule that a question the host cannot answer shows up in
    ``warnings`` instead of disappearing from the result.
    """
    if mode not in QUERY_MODES:
        raise SceneQueryError("mode must be one of {}".format(", ".join(QUERY_MODES)))
    if class_match not in CLASS_MATCH_MODES:
        raise SceneQueryError("class_match must be one of {}".format(", ".join(CLASS_MATCH_MODES)))

    safe_limit = _clamp_limit(limit, DEFAULT_QUERY_NODES, MAX_QUERY_NODES)
    nodes = scene_nodes(runtime)
    scoped = _scope_nodes(nodes, name_filter, include_hidden)

    warnings: List[str] = []
    result: Dict[str, Any] = {"mode": mode}
    # The set a mode actually answered about, so include_snapshot can return a
    # snapshot that round-trips back as that mode's own baseline.
    compared_nodes: List[Any] = nodes

    if mode == "selection":
        selection, selection_error = scene_selection(runtime)
        if selection_error:
            raise SceneQueryError(selection_error)
        scoped_selection = _scope_nodes(selection, name_filter, include_hidden)
        result.update(
            {
                "nodes": [node_payload(node, runtime) for node in scoped_selection[:safe_limit]],
                "count": min(len(scoped_selection), safe_limit),
                "total_matched": len(scoped_selection),
                "truncated": len(scoped_selection) > safe_limit,
                "selection_count": len(selection),
            }
        )
    elif mode == "overview":
        selection, selection_error = scene_selection(runtime)
        if selection_error:
            warnings.append(selection_error)
            selection = []
        result["summary"] = _overview(scoped, runtime, selection)
        result.update({"nodes": [], "count": 0, "total_matched": 0, "truncated": False})
    elif mode == "filter":
        result.update(
            {
                "nodes": [node_payload(node, runtime) for node in scoped[:safe_limit]],
                "count": min(len(scoped), safe_limit),
                "total_matched": len(scoped),
                "truncated": len(scoped) > safe_limit,
                "name_filter": name_filter or None,
            }
        )
    elif mode == "class":
        if not isinstance(class_name, str) or not class_name.strip():
            raise SceneQueryError("class_name is required for mode class")
        wanted = class_name.strip()
        matched = [node for node in scoped if class_matches(node, wanted, class_match, runtime)]
        result.update(
            {
                "nodes": [node_payload(node, runtime) for node in matched[:safe_limit]],
                "count": min(len(matched), safe_limit),
                "total_matched": len(matched),
                "truncated": len(matched) > safe_limit,
                "class_name": wanted,
                "class_match": class_match,
            }
        )
    elif mode == "property":
        wanted_property = validate_property_name(property_name)
        result.update(_property_query(scoped, runtime, wanted_property, property_value, safe_limit))
        result["count"] = len(result["nodes"])
        result["total_matched"] = result["count"] + result["skipped_count"]
        if result["skipped_count"]:
            warnings.append(
                "{} node(s) could not be read and are listed in skipped rather than being treated as "
                "nodes without the property".format(result["skipped_count"])
            )
    else:  # mode == "delta"
        if baseline is None:
            raise SceneQueryError(
                "mode delta requires a baseline snapshot; run query_scene with include_snapshot true and pass "
                "the returned snapshot back as baseline"
            )
        wanted_property = validate_property_name(property_name) if property_name is not None else None
        # Delta compares the whole scene, not the filtered subset. A baseline
        # captured without a filter is a statement about the whole scene, so
        # narrowing only the current side would report every filtered-out node
        # as removed - a node that is still there, reported as deleted.
        result.update(_delta_query(nodes, runtime, baseline, wanted_property))
        compared_nodes = nodes
        result.update({"nodes": [], "count": 0, "total_matched": 0, "truncated": False})
        if name_filter or not include_hidden:
            warnings.append(
                "delta compares the whole scene, so name_filter and include_hidden were not applied to the "
                "comparison; they only narrow the snapshot returned with include_snapshot"
            )
        if result["baseline_count"] == 0 and result["current_count"]:
            warnings.append(
                "the baseline resolved to 0 entries, so every one of the {} node(s) in the scene is reported "
                "as added; pass the snapshot list itself rather than a result object whose nodes list is "
                "empty".format(result["current_count"])
            )
        if wanted_property and result["comparable_count"] == 0:
            warnings.append(
                "the baseline carries no value for {}, so property changes could not be compared; only "
                "additions, removals, and renames are reported".format(wanted_property)
            )
        if result["ambiguous"]:
            warnings.append(
                "{} node name(s) are duplicated in the current scene and were compared once each: {}".format(
                    len(result["ambiguous"]), ", ".join(result["ambiguous"])
                )
            )
        if result["unreadable_count"]:
            warnings.append(
                "{} node(s) could not be read for {} and are listed in unreadable rather than counted as "
                "changed".format(result["unreadable_count"], wanted_property)
            )
    if property_value is not None and mode != "property":
        warnings.append("property_value is ignored in mode {}".format(mode))
    if class_name is not None and mode != "class":
        warnings.append("class_name is ignored in mode {}".format(mode))

    if include_snapshot:
        snapshot_source = scoped
        if mode == "selection":
            snapshot_source = _scope_nodes(scene_selection(runtime)[0], name_filter, include_hidden)
        elif mode == "delta":
            # Snapshot what was actually compared, so the result round-trips.
            snapshot_source = compared_nodes
        snapshot = []
        for node in snapshot_source[:safe_limit]:
            identity = node_identity(node)
            snapshot.append(
                {
                    "node_name": identity["node_name"],
                    "object_id": identity["object_id"],
                    "class_name": identity["class_name"],
                    "visible": node_visible(node),
                }
            )
        result["snapshot"] = snapshot
        result["snapshot_count"] = len(snapshot)
        result["snapshot_truncated"] = len(snapshot_source) > safe_limit
        if result["snapshot_truncated"]:
            warnings.append(
                "the snapshot was cut at {} node(s); raise limit before using it as a delta baseline or the "
                "missing nodes will be reported as added".format(safe_limit)
            )

    result["warnings"] = warnings
    return result
