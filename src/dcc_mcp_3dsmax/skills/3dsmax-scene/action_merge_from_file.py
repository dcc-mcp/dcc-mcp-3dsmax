"""Merge objects from an external .max file into the current scene."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dcc_mcp_3dsmax._max_file_io import (
    MATCH_MODES,
    MAX_OBJECT_NAME_LENGTH,
    MAX_PATTERN_LENGTH,
    MaxFileReadError,
    MaxMergeReadbackError,
    is_max_file,
    match_object_names,
    merge_nodes_from_file,
    read_object_names,
    resolve_max_file_path,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


def _validated_names(object_names: Any) -> List[str]:
    if not isinstance(object_names, list) or not 1 <= len(object_names) <= 1000:
        raise ValueError("object_names must contain between 1 and 1000 names")
    names: List[str] = []
    for name in object_names:
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_OBJECT_NAME_LENGTH or "\x00" in name:
            raise ValueError("object_names entries must be non-empty strings of at most {} characters".format(
                MAX_OBJECT_NAME_LENGTH
            ))
        names.append(name.strip())
    return names


def _resolve(
    object_names: Optional[List[str]],
    name_pattern: Optional[str],
    *,
    case_sensitive: bool,
    match_mode: str,
) -> Tuple[str, List[str]]:
    """Return ``(selection_mode, requested_names)`` for one selection request."""
    if object_names is not None and name_pattern is not None:
        raise ValueError("object_names and name_pattern are mutually exclusive")
    if object_names is not None:
        return "object_names", _validated_names(object_names)
    if name_pattern is not None:
        if not isinstance(name_pattern, str) or not name_pattern.strip() or len(name_pattern) > MAX_PATTERN_LENGTH:
            raise ValueError(
                "name_pattern must be a non-empty string of at most {} characters".format(MAX_PATTERN_LENGTH)
            )
        return "name_pattern", []
    return "all", []


def _match_requested(requested: List[str], available: List[str], *, case_sensitive: bool):
    """Split requested names into resolved and unresolved source names."""
    by_exact = {name: name for name in available}
    lowered = {name.lower(): name for name in available}
    resolved: List[str] = []
    unresolved: List[str] = []
    for name in requested:
        if name in by_exact:
            resolved.append(by_exact[name])
        elif not case_sensitive and name.lower() in lowered:
            resolved.append(lowered[name.lower()])
        else:
            unresolved.append(name)
    return resolved, unresolved


@with_max
def main(
    file_path: str,
    object_names: Optional[List[str]] = None,
    name_pattern: Optional[str] = None,
    case_sensitive: bool = False,
    match_mode: str = "contains",
    duplicate_names: str = "rename",
    material_duplicates: str = "rename",
    reparent: str = "never",
    select_merged: bool = False,
    require_all: bool = False,
) -> Dict[str, Any]:
    """Merge objects selected by name or pattern from an external .max file.

    The source object list is resolved against the file before the merge, so a
    name or pattern that matches nothing fails the call instead of merging an
    arbitrary subset. Unresolved names are always reported, and ``require_all``
    turns them into a hard failure before anything is merged.
    """
    rt = get_runtime()

    path, path_error = resolve_max_file_path(file_path)
    if path_error:
        return {
            "success": False,
            "message": "file_path must be an existing, readable absolute .max file",
            "data": {
                "failure_stage": "precondition",
                "failure_reason": path_error,
                "file_path": str(file_path or ""),
            },
        }

    try:
        if not isinstance(case_sensitive, bool) or not isinstance(require_all, bool):
            raise ValueError("case_sensitive and require_all must be booleans")
        if match_mode not in MATCH_MODES:
            raise ValueError("Unsupported match_mode")
        mode, requested = _resolve(
            object_names, name_pattern, case_sensitive=case_sensitive, match_mode=match_mode
        )
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc) or "Unsupported merge selection",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_merge_selection"},
        }

    try:
        if not is_max_file(rt, path):
            return {
                "success": False,
                "message": "3ds Max rejected the input as an invalid scene file",
                "data": {"failure_stage": "precondition", "failure_reason": "invalid_max_file"},
            }
        available = read_object_names(rt, path)
    except MaxFileReadError as exc:
        return {
            "success": False,
            "message": "Could not read the source scene file: {}".format(exc.reason),
            "data": {
                "failure_stage": "read",
                "failure_reason": exc.reason,
                "error_detail": exc.detail,
                "file_path": str(path),
            },
        }

    unresolved: List[str] = []
    if mode == "object_names":
        resolved, unresolved = _match_requested(requested, available, case_sensitive=case_sensitive)
    elif mode == "name_pattern":
        resolved = match_object_names(
            available, str(name_pattern).strip(), case_sensitive=case_sensitive, match_mode=match_mode
        )
    else:
        resolved = list(available)

    selection: Dict[str, Any] = {
        "selection_mode": mode,
        "requested_object_names": requested,
        "unresolved_object_names": unresolved,
        "source_object_count": len(available),
    }
    if not resolved:
        return {
            "success": False,
            "message": "No object in the source scene file matched the requested selection",
            "data": dict(selection, failure_stage="precondition", failure_reason="no_source_objects_matched"),
        }
    if unresolved and require_all:
        return {
            "success": False,
            "message": "{} requested object(s) are not present in the source scene file".format(len(unresolved)),
            "data": dict(selection, failure_stage="precondition", failure_reason="unresolved_object_names"),
        }

    try:
        outcome = merge_nodes_from_file(
            rt,
            path,
            resolved,
            duplicate_names=duplicate_names,
            material_duplicates=material_duplicates,
            reparent=reparent,
            select_merged=select_merged,
        )
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc) or "Unsupported merge option",
            "data": dict(selection, failure_stage="precondition", failure_reason="invalid_merge_options"),
        }
    except MaxMergeReadbackError as exc:
        return {
            "success": False,
            "message": (
                "3ds Max cannot report which objects a merge produced, so nothing was merged; "
                "a retry cannot duplicate objects"
            ),
            "data": dict(
                selection,
                failure_stage="precondition",
                failure_reason=exc.reason,
                scene_modified=False,
            ),
        }

    warnings: List[str] = []
    if unresolved:
        warnings.append(
            "merged {} of {} requested object(s); not found in source: {}".format(
                len(resolved), len(requested), ", ".join(unresolved)
            )
        )
    data: Dict[str, Any] = {
        "before": outcome["before"],
        "after": outcome["after"],
        "source_file_path": str(path),
        "merged_nodes": outcome["merged_nodes"],
        "merged_count": len(outcome["merged_nodes"]),
        "verified": outcome["verified"],
        "merge_returned": outcome["merge_returned"],
        "scene_modified": outcome["scene_modified"],
        "warnings": warnings,
    }
    data.update(selection)
    if outcome["scene_modified"] and not outcome["verified"]:
        data["warnings"].append(
            "the host accepted the merge call; call undo_last(count=1) before retrying "
            "so the merged objects are not duplicated"
        )
    if not outcome["verified"]:
        return {
            "success": False,
            "message": "3ds Max did not confirm merged objects in the current scene",
            "data": dict(
                data,
                failure_stage="verify",
                failure_reason="scene_merge_readback_mismatch",
                scene_modified=outcome["scene_modified"],
            ),
        }
    return {
        "success": True,
        "message": "Merged and verified {} object(s) from the external scene file".format(len(outcome["merged_nodes"])),
        "data": data,
    }
