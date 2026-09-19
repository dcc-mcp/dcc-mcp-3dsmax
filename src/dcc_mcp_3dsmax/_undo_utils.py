"""Shared undo/redo semantics for 3ds Max skill scripts.

This module is the single place that decides how the adapter talks to the 3ds
Max undo stack. It exists because the adapter exposes destructive tools but has
no way to ask the host whether an operation was undoable, so undo has to be
*verified* rather than assumed.

Three things live here:

1. :func:`scene_fingerprint` — a cheap, deterministic signature of the parts of
   the scene a destructive tool can change. Undo/redo success is confirmed by
   comparing fingerprints, so a host that silently ignores the request is
   reported as a failure instead of a bare success.
2. :func:`history_channels` / :func:`run_history_steps` — ordered, capability
   detected execution channels for ``undo`` and ``redo``, plus the step loop
   that reports exactly how many steps the host actually accepted.
3. :func:`undo_step` — the MAXScript ``undo``-equivalent hold wrapper that a
   future atomic batch tool (``scene_patch``) will use to land as a *single*
   undo entry. It is deliberately not wired into any shipped tool yet; see
   ``docs/UNDO.md``.
"""

from __future__ import annotations

import contextlib
import hashlib
from typing import Any, Callable, Dict, List, Optional, Tuple

from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes

# ── Public constants ────────────────────────────────────────────────────

UNDO_TOOL = "3dsmax-undo__undo_last"
REDO_TOOL = "3dsmax-undo__redo_last"

# Undo granularity vocabulary. These are the only values allowed in the
# ``undo.granularity`` metadata of a tool declaration, so agents can reason
# about how many ``undo_last`` calls reverse one tool call. Destructive tools
# must declare one of these; write paths that are not destructive may declare
# one to state how a batch collapses onto the host undo stack.
GRANULARITY_SINGLE_CALL = "single_call"
GRANULARITY_PER_NODE = "per_node"
GRANULARITY_BATCH_CALL = "batch_call"
GRANULARITY_SCRIPT_DEFINED = "script_defined"
GRANULARITY_NONE = "none"

VALID_GRANULARITIES = (
    GRANULARITY_SINGLE_CALL,
    GRANULARITY_PER_NODE,
    GRANULARITY_BATCH_CALL,
    GRANULARITY_SCRIPT_DEFINED,
    GRANULARITY_NONE,
)

GRANULARITY_DESCRIPTIONS = {
    GRANULARITY_SINGLE_CALL: "One host undo entry per tool call; one undo_last call reverses it.",
    GRANULARITY_PER_NODE: "One host undo entry per node touched; call undo_last once per reported node.",
    GRANULARITY_BATCH_CALL: (
        "One call writes N nodes. The host usually groups the batch into a single undo entry, but the "
        "grouping cannot be queried: undo once, re-read the nodes, then repeat while the scene still differs."
    ),
    GRANULARITY_SCRIPT_DEFINED: "Undo coverage is decided by the script body and cannot be verified by the adapter.",
    GRANULARITY_NONE: "Not reversible through the host undo stack; back up the scene before calling it.",
}

# Upper bound on one undo/redo request. The host stack is finite and each step
# is verified individually, so an unbounded loop would be both slow and risky.
MAX_HISTORY_STEPS = 100

# Nodes sampled per fingerprint. Large scenes stay responsive; a truncated
# fingerprint is flagged so nobody mistakes it for a full-scene comparison.
MAX_FINGERPRINT_NODES = 4000

# Ordered undo/redo channels. The first channel whose entry points exist on the
# host is used for the whole request, so a single call can never fire two
# different undo mechanisms at the same history position.
UNDO_SCRIPT = "max undo"
REDO_SCRIPT = "max redo"

UNDO_DIRECTION = "undo"
REDO_DIRECTION = "redo"


def undo_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def undo_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


# ── Scene fingerprint ───────────────────────────────────────────────────


def _read_float(value: Any) -> str:
    try:
        return "{:.4f}".format(float(value))
    except (TypeError, ValueError):
        return "?"


def _read_axis(node: Any, axis: str) -> str:
    position = getattr(node, "position", None)
    if position is None:
        return "?"
    return _read_float(getattr(position, axis, None))


def _read_count(value: Any) -> str:
    if value is None:
        return "?"
    try:
        return str(len(value))
    except TypeError:
        return "?"


def _node_signature(node: Any) -> Tuple[str, int]:
    """Return ``(signature, unreadable_count)`` for one node.

    Every read is guarded: a node whose transform cannot be read still
    contributes a stable ``?`` placeholder, and the placeholder is counted so
    the caller can tell a degraded fingerprint from a clean one.
    """
    unreadable = 0
    parts: List[str] = []
    for reader in (
        lambda: str(getattr(node, "name", "") or ""),
        lambda: str(getattr(getattr(node, "baseObject", None), "__class__", type(node)).__name__),
        lambda: str(getattr(getattr(node, "parent", None), "name", "") or "-"),
        lambda: _read_axis(node, "x"),
        lambda: _read_axis(node, "y"),
        lambda: _read_axis(node, "z"),
        lambda: _read_count(getattr(node, "modifiers", None)),
        lambda: str(getattr(getattr(node, "material", None), "name", getattr(node, "material", None)) or "-"),
    ):
        try:
            parts.append(reader())
        except Exception:  # noqa: BLE001 - a single unreadable field must not break the digest
            parts.append("?")
            unreadable += 1
    return "|".join(parts), unreadable


def _current_time(rt: Any) -> str:
    try:
        return str(int(rt.currentTime))
    except Exception:  # noqa: BLE001
        return "?"


def _selection_names(rt: Any) -> str:
    try:
        return ",".join(sorted(str(getattr(node, "name", "") or "") for node in rt.selection))
    except Exception:  # noqa: BLE001
        return "?"


def scene_fingerprint(rt: Any, *, node_limit: int = MAX_FINGERPRINT_NODES) -> Dict[str, Any]:
    """Return a deterministic signature of the scene state an undo can change.

    The digest covers node identities, transforms, modifier counts, material
    bindings, the current time, and the selection - wide enough that any
    destructive tool in this adapter produces a different digest when it
    succeeds, and cheap enough to run twice per undo step.
    """
    nodes = iter_scene_nodes(rt)
    truncated = len(nodes) > node_limit
    unreadable = 0
    lines = [
        "objects={}".format(len(nodes)),
        "time={}".format(_current_time(rt)),
        "selection={}".format(_selection_names(rt)),
    ]
    for node in nodes[:node_limit]:
        signature, node_unreadable = _node_signature(node)
        unreadable += node_unreadable
        lines.append(signature)

    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return {
        "digest": digest[:16],
        "node_count": len(nodes),
        "sampled_nodes": min(len(nodes), node_limit),
        "truncated": truncated,
        "unreadable_fields": unreadable,
    }


# ── Undo / redo channels ────────────────────────────────────────────────


def _maxscript_channel(rt: Any, script: str) -> Optional[Tuple[str, Callable[[], Any]]]:
    execute = getattr(rt, "execute", None)
    if not callable(execute):
        return None
    return ("maxscript:{}".format(script), lambda: execute(script))


def _hold_channel(rt: Any, method: str) -> Optional[Tuple[str, Callable[[], Any]]]:
    hold = getattr(rt, "theHold", None)
    func = getattr(hold, method, None)
    if not callable(func):
        return None
    return ("theHold.{}".format(method), func)


def history_channels(rt: Any, direction: str) -> List[Tuple[str, Callable[[], Any]]]:
    """Return the ordered, available ``(name, callable)`` channels for *direction*.

    The channel list is a capability probe - nothing is executed - so it is safe
    to call from a read-only tool. ``max undo`` / ``max redo`` are the documented
    user-level commands and take priority; the SDK hold manager is the fallback
    for hosts that only expose the low-level entry points.
    """
    if direction == UNDO_DIRECTION:
        candidates = (_maxscript_channel(rt, UNDO_SCRIPT), _hold_channel(rt, "Restore"))
    elif direction == REDO_DIRECTION:
        candidates = (_maxscript_channel(rt, REDO_SCRIPT), _hold_channel(rt, "Redo"))
    else:  # pragma: no cover - guarded by the caller
        raise ValueError("direction must be 'undo' or 'redo'")
    return [channel for channel in candidates if channel is not None]


def _holding_state(hold: Any) -> Tuple[bool, str]:
    """Return ``(is_holding, error)`` for a hold manager, never raising."""
    holding = getattr(hold, "Holding", None)
    if not callable(holding):
        return False, ""
    try:
        return bool(holding()), ""
    except Exception as exc:  # noqa: BLE001
        return False, "theHold.Holding() failed: {}".format(exc)


def _describe_channels(rt: Any, direction: str) -> List[str]:
    return [name for name, _func in history_channels(rt, direction)]


def _validate_count(count: Any) -> Optional[str]:
    if isinstance(count, bool) or not isinstance(count, int):
        return "count must be an integer between 1 and {}".format(MAX_HISTORY_STEPS)
    if count < 1 or count > MAX_HISTORY_STEPS:
        return "count must be between 1 and {}".format(MAX_HISTORY_STEPS)
    return None


def run_history_steps(
    rt: Any,
    *,
    direction: str,
    count: int,
    allow_no_op: bool = False,
) -> Dict[str, Any]:
    """Run *count* undo or redo steps and report how many the host accepted.

    Each step is executed once through a single channel and verified against
    the scene fingerprint. A step that the host reports as successful but that
    leaves the scene byte-identical is treated as a no-op: the loop stops there
    and the no-op is surfaced, because the usual cause is an exhausted history
    stack rather than an undo that "worked" invisibly.

    On a scene larger than :data:`MAX_FINGERPRINT_NODES` the fingerprint only
    samples part of the scene, so verification is best-effort. That caveat is
    attached to *every* result - including the empty-stack failure and the
    ``allow_no_op`` success - because it is exactly the case in which a real
    undo can look like a no-op.
    """
    label = "undo" if direction == UNDO_DIRECTION else "redo"
    error = _validate_count(count)
    if error:
        return undo_error(error, direction=direction, requested=count, applied=0, steps=[])

    channels = history_channels(rt, direction)
    if not channels:
        return undo_error(
            "this 3ds Max host exposes no {} entry point (tried '{}' and theHold)".format(
                label, UNDO_SCRIPT if direction == UNDO_DIRECTION else REDO_SCRIPT
            ),
            direction=direction,
            requested=count,
            applied=0,
            steps=[],
            channels=[],
        )
    channel_name, channel = channels[0]

    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    applied = 0
    failure: Optional[str] = None

    for index in range(count):
        before = scene_fingerprint(rt)
        try:
            channel()
        except Exception as exc:  # noqa: BLE001 - a host rejection must become a result, not a traceback
            failure = "the host rejected {} step {}: {}".format(label, index + 1, exc)
            break
        after = scene_fingerprint(rt)
        changed = before["digest"] != after["digest"]
        if changed:
            applied += 1
        steps.append(
            {
                "step": index + 1,
                "applied": changed,
                "channel": channel_name,
                "fingerprint_before": before["digest"],
                "fingerprint_after": after["digest"],
            }
        )
        if not changed:
            break

    data: Dict[str, Any] = {
        "direction": direction,
        "requested": count,
        "applied": applied,
        "completed": applied == count,
        "channel": channel_name,
        "steps": steps,
        "fingerprint": scene_fingerprint(rt),
    }

    # Attached before every exit below: a step that looks like a no-op may only
    # look that way because the fingerprint never sampled the nodes that moved.
    if data["fingerprint"]["truncated"]:
        warnings.append(
            "the scene fingerprint sampled only the first {} nodes, so step verification is best-effort".format(
                data["fingerprint"]["sampled_nodes"]
            )
        )

    if failure:
        data["warnings"] = warnings
        return undo_error(failure, **data)

    if applied == 0:
        message = (
            "no {} step changed the scene - the 3ds Max history stack is empty or the "
            "last operation produced no observable scene change".format(label)
        )
        if allow_no_op:
            warnings.append(message)
            data["warnings"] = warnings
            return undo_success(
                "Requested {} {} step(s); the host reported no scene change".format(count, label),
                **data
            )
        data["warnings"] = warnings
        return undo_error(message, **data)

    if applied < count:
        warnings.append(
            "only {} of {} {} step(s) changed the scene; the history stack was exhausted".format(
                applied, count, label
            )
        )

    data["warnings"] = warnings
    return undo_success(
        "{} {} step(s) applied to the 3ds Max scene".format(applied, "undo" if direction == UNDO_DIRECTION else "redo"),
        **data
    )


# ── Single-hold wrapper for future atomic batches ───────────────────────


@contextlib.contextmanager
def undo_step(rt: Any, label: str = "dcc-mcp edit"):
    """Wrap a block of host mutations in a single 3ds Max undo entry.

    This is the adapter-side equivalent of the MAXScript ``undo "label" ( ...)``
    wrapper. It opens a hold with ``theHold.Begin()`` and closes it with
    ``theHold.Accept(label)`` on success or ``theHold.Cancel()`` on an
    exception, which is what a future atomic batch tool (``scene_patch``) needs
    so that N edits collapse into one undo entry.

    The yielded dict is filled in as the block runs:

    ``engaged``
        True only while the hold is open. When False the block ran without undo
        grouping and *reason* says why - callers must surface that instead of
        pretending the batch is one undo step.
    ``reason``
        Why the hold was not engaged, or empty when it was.

    Nothing in the shipped tool set calls this yet: keeping every existing tool
    on its own host undo entries is the conservative behaviour, and the
    semantics documented in ``docs/UNDO.md`` stay correct either way.
    """
    state: Dict[str, Any] = {"engaged": False, "reason": "", "label": str(label)}
    hold = getattr(rt, "theHold", None)
    begin = getattr(hold, "Begin", None)
    accept = getattr(hold, "Accept", None)
    cancel = getattr(hold, "Cancel", None)

    if not all(callable(func) for func in (begin, accept, cancel)):
        state["reason"] = "theHold does not expose Begin/Accept/Cancel on this host"
        yield state
        return

    is_holding, error = _holding_state(hold)
    if error:
        state["reason"] = error
        yield state
        return
    if is_holding:
        state["reason"] = "a hold is already open on this host"
        yield state
        return

    try:
        begin()
    except Exception as exc:  # noqa: BLE001
        state["reason"] = "theHold.Begin() failed: {}".format(exc)
        yield state
        return

    state["engaged"] = True
    try:
        yield state
    except Exception:
        try:
            cancel()
        except Exception:  # noqa: BLE001 - cancelling is the best available recovery
            pass
        state["engaged"] = False
        state["reason"] = "hold cancelled after an exception"
        raise
    else:
        try:
            accept(str(label))
        except Exception as exc:  # noqa: BLE001
            state["reason"] = "theHold.Accept() failed: {}".format(exc)


def undo_capabilities(rt: Any) -> Dict[str, Any]:
    """Return a non-destructive probe of the host undo/redo entry points."""
    undo = _describe_channels(rt, UNDO_DIRECTION)
    redo = _describe_channels(rt, REDO_DIRECTION)
    hold = getattr(rt, "theHold", None)
    return {
        "undo_supported": bool(undo),
        "redo_supported": bool(redo),
        "undo_channels": undo,
        "redo_channels": redo,
        "single_step_grouping_supported": all(
            callable(getattr(hold, name, None)) for name in ("Begin", "Accept", "Cancel")
        ),
        "undo_tool": UNDO_TOOL,
        "redo_tool": REDO_TOOL,
        "max_steps_per_call": MAX_HISTORY_STEPS,
    }


__all__ = [
    "GRANULARITY_BATCH_CALL",
    "GRANULARITY_DESCRIPTIONS",
    "GRANULARITY_NONE",
    "GRANULARITY_PER_NODE",
    "GRANULARITY_SCRIPT_DEFINED",
    "GRANULARITY_SINGLE_CALL",
    "MAX_FINGERPRINT_NODES",
    "MAX_HISTORY_STEPS",
    "REDO_DIRECTION",
    "REDO_TOOL",
    "UNDO_DIRECTION",
    "UNDO_TOOL",
    "VALID_GRANULARITIES",
    "history_channels",
    "run_history_steps",
    "scene_fingerprint",
    "undo_capabilities",
    "undo_error",
    "undo_step",
    "undo_success",
]
