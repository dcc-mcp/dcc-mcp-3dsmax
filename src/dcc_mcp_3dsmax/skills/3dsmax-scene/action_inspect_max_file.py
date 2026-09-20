"""Inspect an external .max file without opening it in the current scene."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._max_file_io import read_max_file_record
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    file_path: str,
    name_filter: Optional[str] = None,
    limit: int = 200,
    include_object_names: bool = True,
) -> Dict[str, Any]:
    """Report the objects and metadata stored in an external .max file."""
    rt = get_runtime()
    if name_filter is not None and (not isinstance(name_filter, str) or len(name_filter) > 256):
        return {
            "success": False,
            "message": "name_filter must be a string of at most 256 characters",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_name_filter"},
        }
    if not isinstance(include_object_names, bool):
        return {
            "success": False,
            "message": "include_object_names must be a boolean",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_include_object_names"},
        }

    record = read_max_file_record(
        rt,
        file_path,
        name_filter=name_filter,
        limit=limit,
        include_object_names=include_object_names,
    )
    if record["status"] != "ok":
        return {
            "success": False,
            "message": record["error"],
            "data": {
                "failure_stage": "read",
                "failure_reason": record["error_reason"],
                "file_path": record["file_path"],
                "file": record,
            },
        }
    return {
        "success": True,
        "message": "Read {} object(s) from the external scene file".format(record["object_count"]),
        "data": record,
    }
