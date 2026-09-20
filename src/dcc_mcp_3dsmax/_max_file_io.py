"""Shared helpers for reading and merging external 3ds Max scene files.

Every host function used here is **probed, not assumed**: a missing or failing
reader is reported as an explicit per-file failure with a stable reason
instead of an empty list that looks like a successful read. Callers therefore
never have to guess whether "no objects" means "empty file" or "unreadable
file".

The merge half centralises the fixed no-prompt conflict policies so
``merge_file`` and ``merge_from_file`` cannot drift apart. The merge readback
capability is probed **before** the scene is modified: a host that cannot
report what it merged is rejected up front, because "merged but unverified"
would leave a caller free to retry and duplicate the objects.
"""

from __future__ import annotations

import fnmatch
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_lifecycle import normalize_scene_path, scene_status

# A batch is a serial, main-thread host read per path, so the supported size is
# deliberately smaller than the per-request page limits: callers are told to
# split bigger inventories instead of waiting on one long call.
MAX_BATCH_FILES = 20
RECOMMENDED_BATCH_FILES = 10
MAX_NAME_LIMIT = 1000
DEFAULT_NAME_LIMIT = 200
MAX_PATTERN_LENGTH = 256
MAX_OBJECT_NAME_LENGTH = 256
MATCH_MODES = ("contains", "glob")

_DUPLICATE_NAME_FLAGS = {
    "rename": "autoRenameDups",
    "skip": "skipDups",
    "merge": "mergeDups",
}
_MATERIAL_FLAGS = {
    "rename": "renameMtlDups",
    "use_scene": "useSceneMtlDups",
    "use_merged": "useMergedMtlDups",
}
_REPARENT_FLAGS = {"never": "neverReparent", "always": "alwaysReparent"}

REASON_MESSAGES = {
    "file_path_required": "file_path must be a non-empty string",
    "invalid_file_path": "file_path is not a usable path",
    "absolute_max_path_required": "file_path must be an absolute .max path",
    "scene_file_not_found": "no file exists at file_path",
    "scene_file_not_readable": "the process cannot read file_path",
    "invalid_max_file": "3ds Max rejected the input as an invalid scene file",
    "is_max_file_unavailable": "this 3ds Max host exposes no isMaxFile predicate",
    "is_max_file_failed": "isMaxFile raised while validating the file",
    "external_scene_reader_unavailable": "this 3ds Max host exposes no getMAXFileObjectNames reader",
    "object_names_read_failed": "getMAXFileObjectNames raised while reading the file",
    "merge_readback_unavailable": "this 3ds Max host exposes no getLastMergedNodes readback",
}

BATCH_SIZE_WARNING = (
    "this batch reads {} scene files serially on the 3ds Max main thread and may be slow; "
    "split it into batches of {} paths or fewer"
)

UNVERIFIED_RETRY_WARNING = (
    "the host accepted the merge call; call undo_last(count=1) before retrying "
    "so the merged objects are not duplicated"
)
READBACK_FAILED_WARNING = (
    "the merge happened but the host failed to report the merged nodes ({}); "
    "call undo_last(count=1) before retrying so the merged objects are not duplicated"
)


class MaxFileReadError(Exception):
    """Raised when an external .max file cannot be read at all."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class MaxMergeReadbackError(Exception):
    """Raised **before** a merge when the host cannot report what it merged.

    Carrying this out of the shared helper means a caller can never see "the
    host merged something but the adapter could not confirm it", which is the
    state where a retry would duplicate the merged objects.
    """

    reason = "merge_readback_unavailable"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.reason)
        self.detail = detail


# ── host probes ─────────────────────────────────────────────────────────


def is_max_file(rt: Any, path: Any) -> bool:
    """Return whether the host accepts ``path`` as a scene file."""
    predicate = getattr(rt, "isMaxFile", None)
    if predicate is None:
        raise MaxFileReadError("is_max_file_unavailable")
    try:
        return bool(predicate(str(path)))
    except Exception as exc:  # noqa: BLE001 - host errors are reported, not swallowed.
        raise MaxFileReadError("is_max_file_failed", str(exc))


def read_object_names(rt: Any, path: Any) -> List[str]:
    """Return the object names stored in an external .max file."""
    reader = getattr(rt, "getMAXFileObjectNames", None)
    if reader is None:
        raise MaxFileReadError("external_scene_reader_unavailable")
    try:
        names = reader(str(path), quiet=True)
    except TypeError:
        # Only an unsupported ``quiet`` keyword earns a positional retry: a real
        # read failure must not read the same file twice.
        try:
            names = reader(str(path))
        except Exception as exc:  # noqa: BLE001
            raise MaxFileReadError("object_names_read_failed", str(exc))
    except Exception as exc:  # noqa: BLE001 - every other failure is reported once.
        raise MaxFileReadError("object_names_read_failed", str(exc))
    return [str(name) for name in list(names or [])]


def read_file_version(rt: Any, path: Any) -> Tuple[Optional[str], Optional[str]]:
    """Probe the file's 3ds Max version.

    Returns ``(version, warning)``. The version is ``None`` with a warning when
    the host exposes no reader, the reader raises, or the reader returns
    nothing, so a missing version is never reported as a fact.
    """
    reader = getattr(rt, "getMAXFileVersion", None)
    if reader is None:
        return None, "this 3ds Max host exposes no getMAXFileVersion reader; max_file_version is unavailable"
    try:
        value = reader(str(path))
    except Exception as exc:  # noqa: BLE001
        return None, "getMAXFileVersion failed: {}".format(exc)
    text = str(value).lstrip("#").strip()
    if not text:
        return None, "getMAXFileVersion returned no version; max_file_version is unavailable"
    return text, None


# ── path and name helpers ───────────────────────────────────────────────


def resolve_max_file_path(file_path: Any) -> Tuple[Optional[Any], Optional[str]]:
    """Return ``(path, reason)`` for one bounded, readable .max path."""
    path, path_error = normalize_scene_path(file_path, must_exist=True)
    if path_error:
        return None, path_error
    if not os.access(str(path), os.R_OK):
        return None, "scene_file_not_readable"
    return path, None


def bounded_limit(limit: Any, default: int = DEFAULT_NAME_LIMIT) -> int:
    """Clamp a requested page size into ``[1, MAX_NAME_LIMIT]``."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_NAME_LIMIT))


def matches_pattern(name: str, pattern: str, *, case_sensitive: bool, match_mode: str) -> bool:
    """Match one object name against a pattern in a platform-stable way."""
    if match_mode == "glob":
        if case_sensitive:
            return fnmatch.fnmatchcase(name, pattern)
        return fnmatch.fnmatchcase(name.lower(), pattern.lower())
    if case_sensitive:
        return pattern in name
    return pattern.lower() in name.lower()


def match_object_names(
    names: Sequence[str],
    pattern: str,
    *,
    case_sensitive: bool = False,
    match_mode: str = "contains",
) -> List[str]:
    """Return the names that match ``pattern`` in the requested mode."""
    return [name for name in names if matches_pattern(name, pattern, case_sensitive=case_sensitive, match_mode=match_mode)]


def _filesystem_facts(path: Any) -> Tuple[Dict[str, Any], List[str]]:
    try:
        stat = path.stat()
    except OSError as exc:
        return (
            {"file_size_bytes": None, "modified_time": None},
            ["file metadata unavailable: {}".format(exc)],
        )
    return {"file_size_bytes": int(stat.st_size), "modified_time": float(stat.st_mtime)}, []


def _failure_record(file_path: Any, reason: str, detail: str = "") -> Dict[str, Any]:
    message = REASON_MESSAGES.get(reason, reason)
    if detail:
        message = "{}: {}".format(message, detail)
    return {
        "file_path": str(file_path or ""),
        "status": "error",
        "error_reason": reason,
        "error": message,
        "object_count": 0,
        "object_names": [],
        "matched_count": 0,
        "truncated": False,
        "file_size_bytes": None,
        "modified_time": None,
        "max_file_version": None,
        "metadata_source": "unavailable",
        "warnings": [],
    }


# ── one-file read ───────────────────────────────────────────────────────


def read_max_file_record(
    rt: Any,
    file_path: Any,
    *,
    name_filter: Optional[str] = None,
    limit: Any = DEFAULT_NAME_LIMIT,
    include_object_names: bool = True,
) -> Dict[str, Any]:
    """Read one external .max file into a self-describing record.

    The record carries ``status`` (``ok`` or ``error``), the reason and message
    for an error, and a ``warnings`` list for facts that could not be read.
    Nothing is reported as a successful read unless the host actually returned
    the object names.
    """
    path, reason = resolve_max_file_path(file_path)
    if reason:
        return _failure_record(file_path, reason)

    try:
        if not is_max_file(rt, path):
            return _failure_record(str(path), "invalid_max_file")
        names = read_object_names(rt, path)
    except MaxFileReadError as exc:
        return _failure_record(str(path), exc.reason, exc.detail)

    selected = list(names)
    if name_filter:
        needle = str(name_filter).lower()
        selected = [name for name in selected if needle in name.lower()]
    matched_count = len(selected)
    safe_limit = bounded_limit(limit)
    page = selected[:safe_limit]

    version, version_warning = read_file_version(rt, path)
    facts, warnings = _filesystem_facts(path)
    if version_warning:
        warnings.append(version_warning)

    record: Dict[str, Any] = {
        "file_path": str(path),
        "status": "ok",
        "error_reason": None,
        "error": None,
        "object_count": len(names),
        "object_names": page if include_object_names else [],
        "matched_count": matched_count,
        "truncated": matched_count > safe_limit,
        "max_file_version": version,
        "metadata_source": "host" if version else "filesystem",
        "warnings": warnings,
    }
    record.update(facts)
    return record


# ── merge ───────────────────────────────────────────────────────────────


def validate_merge_options(
    duplicate_names: str,
    material_duplicates: str,
    reparent: str,
    select_merged: bool,
) -> Tuple[str, str, str]:
    """Return the three conflict-policy flags or raise ``ValueError``."""
    if not isinstance(select_merged, bool):
        raise ValueError("select_merged must be a boolean")
    if not all(isinstance(value, str) for value in (duplicate_names, material_duplicates, reparent)):
        raise ValueError("merge policies must be strings")
    if duplicate_names not in _DUPLICATE_NAME_FLAGS:
        raise ValueError("Unsupported duplicate_names policy")
    if material_duplicates not in _MATERIAL_FLAGS:
        raise ValueError("Unsupported material_duplicates policy")
    if reparent not in _REPARENT_FLAGS:
        raise ValueError("Unsupported reparent policy")
    return (
        _DUPLICATE_NAME_FLAGS[duplicate_names],
        _MATERIAL_FLAGS[material_duplicates],
        _REPARENT_FLAGS[reparent],
    )


def merge_failure_warnings(outcome: Dict[str, Any]) -> List[str]:
    """Warnings that tell a caller how to retry a merge that was not confirmed.

    The merge already happened in both cases, so the only safe retry is one
    that undoes first; without this a caller cannot tell "nothing merged" from
    "merged, retrying duplicates".
    """
    if outcome.get("readback_error"):
        return [READBACK_FAILED_WARNING.format(outcome["readback_error"])]
    if outcome.get("scene_modified"):
        return [UNVERIFIED_RETRY_WARNING]
    return []


def _node_records(nodes: Any) -> List[Dict[str, Any]]:
    return [
        {"node_name": str(getattr(node, "name", "")), "handle": int(getattr(node, "handle", 0))} for node in list(nodes)
    ]


def merge_nodes_from_file(
    rt: Any,
    path: Any,
    node_names: Optional[Sequence[str]],
    *,
    duplicate_names: str = "rename",
    material_duplicates: str = "rename",
    reparent: str = "never",
    select_merged: bool = False,
) -> Dict[str, Any]:
    """Merge nodes with fixed no-prompt policies and read the result back.

    Raises ``ValueError`` for unsupported options and ``MaxMergeReadbackError``
    when the host cannot report a merge, so callers always fail before touching
    the scene. The readback is therefore **called** during the preflight, not
    only looked up: a host that exposes the entry point but fails when it runs
    would otherwise surface the failure after the scene was already modified.

    A readback that fails *after* the merge is reported, not raised: the scene
    is already modified at that point, so the caller needs the verdict and the
    undo guidance instead of an exception. The returned dict reports both the
    native return value and the readback verdict; callers must treat
    ``verified`` as the outcome.
    """
    duplicate_flag, material_flag, reparent_flag = validate_merge_options(
        duplicate_names, material_duplicates, reparent, select_merged
    )

    # Preflight, not post-mortem: if the host cannot say what it merged, refuse
    # before the scene changes instead of reporting an unverifiable merge that a
    # caller might retry.
    readback = getattr(rt, "getLastMergedNodes", None)
    if readback is None:
        raise MaxMergeReadbackError()
    try:
        readback()
    except Exception as exc:  # noqa: BLE001 - any failure means no usable readback.
        raise MaxMergeReadbackError(str(exc))

    before = scene_status(rt)
    before_handles = {int(getattr(node, "handle", 0)) for node in list(rt.objects)}

    args: List[Any] = []
    if node_names is not None:
        args.append([str(name) for name in node_names])
    args.extend((rt.Name(duplicate_flag), rt.Name(material_flag), rt.Name(reparent_flag)))
    if select_merged:
        args.append(rt.Name("select"))

    merge_returned = rt.mergeMAXFile(str(path), *args, quiet=True)
    after = scene_status(rt)

    # The scene is already modified here, so a readback failure is reported as an
    # unverified result with undo guidance rather than an escaping exception.
    readback_error = ""
    try:
        merged_nodes = _node_records(readback())
    except Exception as exc:  # noqa: BLE001
        merged_nodes = []
        readback_error = str(exc) or exc.__class__.__name__
    merged_handles = {item["handle"] for item in merged_nodes if item["handle"]}
    after_handles = {int(getattr(node, "handle", 0)) for node in list(rt.objects)}

    verified = (
        bool(merge_returned)
        and bool(merged_nodes)
        and merged_handles.isdisjoint(before_handles)
        and merged_handles.issubset(after_handles)
        and after["object_count"] > before["object_count"]
        and after["current_file_path"] == before["current_file_path"]
        and after["dirty"]
    )
    return {
        "before": before,
        "after": after,
        "merged_nodes": merged_nodes,
        "merge_returned": bool(merge_returned),
        "verified": bool(verified),
        "scene_modified": bool(merge_returned) or after["object_count"] > before["object_count"],
        "readback_error": readback_error or None,
    }
