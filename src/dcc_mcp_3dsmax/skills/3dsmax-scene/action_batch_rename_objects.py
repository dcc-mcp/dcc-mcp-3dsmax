"""Rename many 3ds Max nodes from one deterministic naming rule."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_objects
from dcc_mcp_3dsmax.api import get_runtime, with_max

RENAME_MODES = ("suffix", "prefix", "replace", "pattern")


def _apply_mode(mode: str, current: str, base: str, search: str, padding: int, index: int) -> str:
    if mode == "suffix":
        return "{}{}".format(current, base)
    if mode == "prefix":
        return "{}{}".format(base, current)
    if mode == "replace":
        return current.replace(search, base)
    number = "{{:0{}d}}".format(padding).format(index)
    return base.replace("{index}", number).replace("{name}", current)


@with_max
def main(
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    mode: str = "suffix",
    base: str = "",
    search: str = "",
    start_index: int = 1,
    padding: int = 3,
) -> Dict[str, Any]:
    """Rename resolved nodes and verify every new name afterwards.

    New names are validated as a whole before anything is written, so a
    duplicate or empty target name fails the batch without a partial rename.
    """
    normalized_mode = str(mode or "suffix").strip().lower()
    if normalized_mode not in RENAME_MODES:
        return {
            "success": False,
            "message": "Unsupported rename mode",
            "data": {"mode": mode, "supported": list(RENAME_MODES)},
        }

    if normalized_mode == "replace" and not str(search):
        return {
            "success": False,
            "message": "search is required for replace mode",
            "data": {"mode": normalized_mode},
        }
    if normalized_mode in ("prefix", "suffix", "replace") and not str(base):
        return {
            "success": False,
            "message": "base is required for this rename mode",
            "data": {"mode": normalized_mode},
        }
    if normalized_mode == "pattern" and not str(base):
        return {
            "success": False,
            "message": "base is required for pattern mode",
            "data": {"mode": normalized_mode},
        }

    try:
        pad = int(padding)
        start = int(start_index)
    except (TypeError, ValueError):
        return {
            "success": False,
            "message": "padding and start_index must be integers",
            "data": {"padding": padding, "start_index": start_index},
        }
    if pad < 1 or pad > 12:
        return {
            "success": False,
            "message": "padding must be between 1 and 12",
            "data": {"padding": pad},
        }

    rt = get_runtime()
    result = resolve_node_objects(rt, node_names=node_names, handles=handles)
    if not result.get("success"):
        return {"success": False, "message": result["message"], "data": result}

    nodes = result["objects"]
    if not nodes:
        return {"success": False, "message": "No nodes selected for rename", "data": result}

    existing = {str(getattr(node, "name", "")) for node in getattr(rt, "objects", []) or []}

    plan: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}
    for offset, node in enumerate(nodes):
        current = str(getattr(node, "name", ""))
        new_name = _apply_mode(normalized_mode, current, str(base), str(search), pad, start + offset)
        new_name = new_name.strip()
        if not new_name:
            return {
                "success": False,
                "message": "Rename rule produced an empty name",
                "data": {"node": node_identity(node), "mode": normalized_mode},
            }
        if new_name != current and new_name in existing and new_name not in seen:
            return {
                "success": False,
                "message": "Rename target collides with an existing node",
                "data": {"node": node_identity(node), "target": new_name, "mode": normalized_mode},
            }
        if new_name in seen:
            return {
                "success": False,
                "message": "Rename rule produced duplicate names",
                "data": {"previous_node": seen[new_name], "target": new_name},
            }
        seen[new_name] = current
        plan.append({"node": node, "previous_name": current, "new_name": new_name})

    renamed: List[Dict[str, Any]] = []
    for entry in plan:
        node = entry["node"]
        try:
            node.name = entry["new_name"]
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "message": "3ds Max rejected a node rename",
                "data": {
                    "node": node_identity(node),
                    "previous_name": entry["previous_name"],
                    "target": entry["new_name"],
                    "error": "{}: {}".format(type(exc).__name__, exc),
                    "applied": renamed,
                },
            }
        readback = str(getattr(node, "name", ""))
        if readback != entry["new_name"]:
            return {
                "success": False,
                "message": "Rename did not take effect",
                "data": {
                    "node": node_identity(node),
                    "previous_name": entry["previous_name"],
                    "target": entry["new_name"],
                    "readback": readback,
                    "applied": renamed,
                },
            }
        renamed.append(
            {
                "previous_name": entry["previous_name"],
                "new_name": readback,
                "object_id": node_identity(node).get("object_id"),
            }
        )

    return {
        "success": True,
        "message": "Renamed {} node(s)".format(len(renamed)),
        "data": {"mode": normalized_mode, "count": len(renamed), "renamed": renamed},
    }
