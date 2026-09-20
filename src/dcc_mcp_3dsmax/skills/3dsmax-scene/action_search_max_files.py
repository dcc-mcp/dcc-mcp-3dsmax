"""Search external .max files for objects matching a name pattern."""

from __future__ import annotations

from typing import Any, Dict, List

from dcc_mcp_3dsmax._max_file_io import (
    BATCH_SIZE_WARNING,
    DEFAULT_NAME_LIMIT,
    MATCH_MODES,
    MAX_BATCH_FILES,
    MAX_PATTERN_LENGTH,
    RECOMMENDED_BATCH_FILES,
    MaxFileReadError,
    bounded_limit,
    is_max_file,
    match_object_names,
    read_object_names,
    resolve_max_file_path,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    file_paths: List[str],
    name_pattern: str,
    case_sensitive: bool = False,
    match_mode: str = "contains",
    limit: int = DEFAULT_NAME_LIMIT,
) -> Dict[str, Any]:
    """Search a batch of .max files for objects whose name matches a pattern.

    A file that cannot be read is reported as an explicit per-file error and
    fails the call, so a partial search is never presented as a complete one.
    """
    rt = get_runtime()
    try:
        if not isinstance(file_paths, list) or not 1 <= len(file_paths) <= MAX_BATCH_FILES:
            raise ValueError(
                "file_paths must contain between 1 and {} paths; split a larger inventory into "
                "several calls".format(MAX_BATCH_FILES)
            )
        if not isinstance(name_pattern, str) or not name_pattern.strip() or len(name_pattern) > MAX_PATTERN_LENGTH:
            raise ValueError(
                "name_pattern must be a non-empty string of at most {} characters".format(MAX_PATTERN_LENGTH)
            )
        if not isinstance(case_sensitive, bool):
            raise ValueError("case_sensitive must be a boolean")
        if match_mode not in MATCH_MODES:
            raise ValueError("Unsupported match_mode")
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc) or "Unsupported search options",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_search_options"},
        }

    requested = []
    for item in file_paths:
        if not isinstance(item, str) or not item.strip():
            return {
                "success": False,
                "message": "file_paths entries must be non-empty strings",
                "data": {"failure_stage": "precondition", "failure_reason": "invalid_file_paths"},
            }
        requested.append(item.strip())
    requested = list(dict.fromkeys(requested))

    safe_limit = bounded_limit(limit)
    per_file: List[Dict[str, Any]] = []
    matches: List[Dict[str, str]] = []
    match_count = 0
    failed: List[Dict[str, Any]] = []

    for raw_path in requested:
        path, reason = resolve_max_file_path(raw_path)
        if reason:
            entry = {"file_path": raw_path, "status": "error", "error_reason": reason, "matched_count": 0}
            per_file.append(entry)
            failed.append(entry)
            continue
        try:
            if not is_max_file(rt, path):
                entry = {
                    "file_path": str(path),
                    "status": "error",
                    "error_reason": "invalid_max_file",
                    "matched_count": 0,
                }
                per_file.append(entry)
                failed.append(entry)
                continue
            names = read_object_names(rt, path)
        except MaxFileReadError as exc:
            entry = {
                "file_path": str(path),
                "status": "error",
                "error_reason": exc.reason,
                "matched_count": 0,
                "error_detail": exc.detail,
            }
            per_file.append(entry)
            failed.append(entry)
            continue

        found = match_object_names(names, name_pattern, case_sensitive=case_sensitive, match_mode=match_mode)
        match_count += len(found)
        per_file.append({"file_path": str(path), "status": "ok", "matched_count": len(found)})
        for name in found:
            if len(matches) < safe_limit:
                matches.append({"file_path": str(path), "object_name": name})

    warnings: List[str] = []
    if len(requested) > RECOMMENDED_BATCH_FILES:
        warnings.append(BATCH_SIZE_WARNING.format(len(requested), RECOMMENDED_BATCH_FILES))

    data: Dict[str, Any] = {
        "files": per_file,
        "matches": matches,
        "requested_count": len(requested),
        "searched_count": len(per_file) - len(failed),
        "failed_count": len(failed),
        "failed": failed,
        "match_count": match_count,
        "returned_count": len(matches),
        "truncated": match_count > len(matches),
        "name_pattern": name_pattern,
        "match_mode": match_mode,
        "case_sensitive": case_sensitive,
        "partial": bool(failed) and len(failed) < len(per_file),
        "warnings": warnings,
    }
    if failed:
        return {
            "success": False,
            "message": "Could not search {} of {} requested scene file(s)".format(len(failed), len(per_file)),
            "data": data,
        }
    return {
        "success": True,
        "message": "Found {} matching object(s) in {} scene file(s)".format(match_count, len(per_file)),
        "data": data,
    }
