"""Read metadata for a batch of external .max files in one call."""

from __future__ import annotations

from typing import Any, Dict, List

from dcc_mcp_3dsmax._max_file_io import (
    BATCH_SIZE_WARNING,
    DEFAULT_NAME_LIMIT,
    MAX_BATCH_FILES,
    RECOMMENDED_BATCH_FILES,
    read_max_file_record,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


def _validated_paths(file_paths: Any) -> List[str]:
    if not isinstance(file_paths, list) or not 1 <= len(file_paths) <= MAX_BATCH_FILES:
        raise ValueError(
            "file_paths must contain between 1 and {} paths; split a larger inventory into "
            "several calls".format(MAX_BATCH_FILES)
        )
    paths: List[str] = []
    for item in file_paths:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("file_paths entries must be non-empty strings")
        paths.append(item.strip())
    return paths


@with_max
def main(
    file_paths: List[str],
    limit: int = DEFAULT_NAME_LIMIT,
    include_object_names: bool = False,
) -> Dict[str, Any]:
    """Report per-file metadata for every requested .max file.

    A file that is missing, unreadable, or not a scene file is reported as an
    explicit ``status: error`` record and fails the call; the per-file results
    are still returned so a caller can see what did work.
    """
    rt = get_runtime()
    try:
        if not isinstance(include_object_names, bool):
            raise ValueError("include_object_names must be a boolean")
        paths = _validated_paths(file_paths)
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc) or "Unsupported batch options",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_file_paths"},
        }

    requested = list(dict.fromkeys(paths))
    duplicates_ignored = len(paths) - len(requested)

    records = [
        read_max_file_record(rt, path, limit=limit, include_object_names=include_object_names) for path in requested
    ]
    failed = [record for record in records if record["status"] != "ok"]
    warnings: List[str] = []
    for record in records:
        for warning in record["warnings"]:
            warnings.append("{}: {}".format(record["file_path"], warning))
        if record["status"] != "ok":
            warnings.append(
                "{}: {} ({})".format(record["file_path"], record["error"], record["error_reason"])
            )
    if duplicates_ignored:
        warnings.append("ignored {} duplicate path(s) in file_paths".format(duplicates_ignored))
    if len(requested) > RECOMMENDED_BATCH_FILES:
        warnings.append(BATCH_SIZE_WARNING.format(len(requested), RECOMMENDED_BATCH_FILES))

    data: Dict[str, Any] = {
        "files": records,
        "requested_count": len(requested),
        "ok_count": len(records) - len(failed),
        "failed_count": len(failed),
        "failed": [{"file_path": record["file_path"], "error_reason": record["error_reason"]} for record in failed],
        "duplicate_paths_ignored": duplicates_ignored,
        "warnings": warnings,
        "partial": bool(failed) and len(failed) < len(records),
    }
    if failed:
        return {
            "success": False,
            "message": "Could not read {} of {} requested scene file(s)".format(len(failed), len(records)),
            "data": data,
        }
    return {
        "success": True,
        "message": "Read metadata for {} scene file(s)".format(len(records)),
        "data": data,
    }
