"""Merge objects from a bounded 3ds Max scene file."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dcc_mcp_3dsmax._scene_lifecycle import normalize_scene_path, scene_status
from dcc_mcp_3dsmax.api import get_runtime, with_max

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


def _validated_node_names(node_names: Optional[List[str]]) -> Optional[List[str]]:
    if node_names is None:
        return None
    if not isinstance(node_names, list) or not 1 <= len(node_names) <= 1000:
        raise ValueError("node_names must contain between 1 and 1000 names")
    normalized = []
    for name in node_names:
        if not isinstance(name, str) or not name.strip() or len(name) > 256 or "\x00" in name:
            raise ValueError("node_names entries must be non-empty strings of at most 256 characters")
        normalized.append(name.strip())
    return normalized


def _node_records(nodes: Any) -> List[Dict[str, Any]]:
    return [
        {"node_name": str(getattr(node, "name", "")), "handle": int(getattr(node, "handle", 0))} for node in list(nodes)
    ]


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
    if not bool(rt.isMaxFile(str(path))):
        return {
            "success": False,
            "message": "3ds Max rejected the input as an invalid scene file",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_max_file"},
        }
    try:
        if not isinstance(select_merged, bool):
            raise ValueError("select_merged must be a boolean")
        if not all(isinstance(value, str) for value in (duplicate_names, material_duplicates, reparent)):
            raise ValueError("merge policies must be strings")
        names = _validated_node_names(node_names)
        if duplicate_names not in _DUPLICATE_NAME_FLAGS:
            raise ValueError("Unsupported duplicate_names policy")
        if material_duplicates not in _MATERIAL_FLAGS:
            raise ValueError("Unsupported material_duplicates policy")
        if reparent not in _REPARENT_FLAGS:
            raise ValueError("Unsupported reparent policy")
        duplicate_flag = _DUPLICATE_NAME_FLAGS[duplicate_names]
        material_flag = _MATERIAL_FLAGS[material_duplicates]
        reparent_flag = _REPARENT_FLAGS[reparent]
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc) or "Unsupported merge option",
            "data": {"failure_stage": "precondition", "failure_reason": "invalid_merge_options"},
        }

    before = scene_status(rt)
    before_handles = {int(getattr(node, "handle", 0)) for node in list(rt.objects)}
    args = []
    if names is not None:
        args.append(names)
    args.extend((rt.Name(duplicate_flag), rt.Name(material_flag), rt.Name(reparent_flag)))
    if select_merged:
        args.append(rt.Name("select"))

    merged = rt.mergeMAXFile(str(path), *args, quiet=True)
    after = scene_status(rt)
    merged_nodes = _node_records(rt.getLastMergedNodes())
    merged_handles = {item["handle"] for item in merged_nodes if item["handle"]}
    after_handles = {int(getattr(node, "handle", 0)) for node in list(rt.objects)}
    verified = (
        bool(merged)
        and bool(merged_nodes)
        and merged_handles.isdisjoint(before_handles)
        and merged_handles.issubset(after_handles)
        and after["object_count"] > before["object_count"]
        and after["current_file_path"] == before["current_file_path"]
        and after["dirty"]
    )
    if not verified:
        return {
            "success": False,
            "message": "3ds Max did not confirm merged objects in the current scene",
            "data": {
                "failure_stage": "verify",
                "failure_reason": "scene_merge_readback_mismatch",
                "before": before,
                "after": after,
                "merged_nodes": merged_nodes,
                "verified": False,
            },
        }
    return {
        "success": True,
        "message": "Merged and verified 3ds Max scene objects",
        "data": {
            "before": before,
            "after": after,
            "source_file_path": str(path),
            "merged_nodes": merged_nodes,
            "verified": True,
        },
    }
