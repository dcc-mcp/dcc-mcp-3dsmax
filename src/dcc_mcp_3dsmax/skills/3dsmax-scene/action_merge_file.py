"""Merge objects from a bounded 3ds Max scene file."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dcc_mcp_3dsmax._max_file_io import (
    MAX_OBJECT_NAME_LENGTH,
    MaxFileReadError,
    is_max_file,
    merge_nodes_from_file,
)
from dcc_mcp_3dsmax._scene_lifecycle import normalize_scene_path
from dcc_mcp_3dsmax.api import get_runtime, with_max


def _validated_node_names(node_names: Optional[List[str]]) -> Optional[List[str]]:
    if node_names is None:
        return None
    if not isinstance(node_names, list) or not 1 <= len(node_names) <= 1000:
        raise ValueError("node_names must contain between 1 and 1000 names")
    normalized = []
    for name in node_names:
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_OBJECT_NAME_LENGTH or "\x00" in name:
            raise ValueError("node_names entries must be non-empty strings of at most 256 characters")
        normalized.append(name.strip())
    return normalized


@with_max
def main(
    file_path: str,
    node_names: Optional[List[str]] = None,
    duplicate_names: str = "rename",
    material_duplicates: str = "rename",
    reparent: str = "never",
    select_merged: bool = False,
) -> Dict[str, Any]:
    """Merge selected or all nodes using fixed no-prompt conflict policies."""
    rt = get_runtime()
    path, path_error = normalize_scene_path(file_path, must_exist=True)
    if path_error:
        return {
            "success": False,
            "message": "file_path must be an existing absolute .max file",
            "data": {"failure_stage": "precondition", "failure_reason": path_error},
        }
    try:
        if not bool(is_max_file(rt, path)):
            return {
                "success": False,
                "message": "3ds Max rejected the input as an invalid scene file",
                "data": {"failure_stage": "precondition", "failure_reason": "invalid_max_file"},
            }
    except MaxFileReadError as exc:
        return {
            "success": False,
            "message": "Could not validate the source scene file: {}".format(exc.reason),
            "data": {
                "failure_stage": "precondition",
                "failure_reason": "invalid_max_file",
                "error_detail": exc.detail,
            },
        }

    try:
        if not isinstance(select_merged, bool):
            raise ValueError("select_merged must be a boolean")
        names = _validated_node_names(node_names)
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc) or "Unsupported merge option",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_merge_options"},
        }

    try:
        outcome = merge_nodes_from_file(
            rt,
            path,
            names,
            duplicate_names=duplicate_names,
            material_duplicates=material_duplicates,
            reparent=reparent,
            select_merged=select_merged,
        )
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc) or "Unsupported merge option",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_merge_options"},
        }

    if not outcome["verified"]:
        return {
            "success": False,
            "message": "3ds Max did not confirm merged objects in the current scene",
            "data": {
                "failure_stage": "verify",
                "failure_reason": "scene_merge_readback_mismatch",
                "before": outcome["before"],
                "after": outcome["after"],
                "merged_nodes": outcome["merged_nodes"],
                "verified": False,
            },
        }
    return {
        "success": True,
        "message": "Merged and verified 3ds Max scene objects",
        "data": {
            "before": outcome["before"],
            "after": outcome["after"],
            "source_file_path": str(path),
            "merged_nodes": outcome["merged_nodes"],
            "verified": True,
        },
    }
