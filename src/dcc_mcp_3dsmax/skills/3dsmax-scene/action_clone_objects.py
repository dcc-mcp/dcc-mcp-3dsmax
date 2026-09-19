"""Clone 3ds Max nodes as copies, instances, or references."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_objects
from dcc_mcp_3dsmax.api import get_runtime, with_max

CLONE_MODES = ("copy", "instance", "reference")


def _base_object(node: Any) -> Any:
    return getattr(node, "baseObject", None)


@with_max
def main(
    node_names: Optional[Sequence[str]] = None,
    handles: Optional[Sequence[int]] = None,
    mode: str = "copy",
    name_suffix: str = "_clone",
) -> Dict[str, Any]:
    """Clone nodes and verify the clone actually has the requested sharing.

    ``copy`` must not share the source base object, ``instance`` must share it,
    and ``reference`` must be distinct while not reported as an instance. When
    the runtime cannot confirm a reference clone the result carries an explicit
    ``warning`` instead of an unverified success.
    """
    normalized_mode = str(mode or "copy").strip().lower()
    if normalized_mode not in CLONE_MODES:
        return {
            "success": False,
            "message": "Unsupported clone mode",
            "data": {"mode": mode, "supported": list(CLONE_MODES)},
        }

    rt = get_runtime()
    result = resolve_node_objects(rt, node_names=node_names, handles=handles)
    if not result.get("success"):
        return {"success": False, "message": result["message"], "data": result}

    factory = getattr(rt, normalized_mode, None)
    if not callable(factory):
        return {
            "success": False,
            "message": "3ds Max runtime does not expose this clone mode",
            "data": {"mode": normalized_mode},
        }

    cloned: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for node in result["objects"]:
        identity = node_identity(node)
        try:
            new_node = factory(node)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "message": "3ds Max could not clone the node",
                "data": {
                    "node": identity,
                    "mode": normalized_mode,
                    "error": "{}: {}".format(type(exc).__name__, exc),
                    "cloned": cloned,
                },
            }

        new_identity = node_identity(new_node)
        if new_identity.get("object_id") is not None and new_identity.get("object_id") == identity.get("object_id"):
            return {
                "success": False,
                "message": "Clone did not create a new node",
                "data": {"node": identity, "mode": normalized_mode, "cloned": cloned},
            }

        if name_suffix:
            target = "{}{}".format(str(getattr(node, "name", "node")), name_suffix)
            try:
                new_node.name = target
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "message": "Clone was created but could not be named",
                    "data": {
                        "node": identity,
                        "mode": normalized_mode,
                        "error": "{}: {}".format(type(exc).__name__, exc),
                    },
                }
            if str(getattr(new_node, "name", "")) != target:
                return {
                    "success": False,
                    "message": "Clone name did not take effect",
                    "data": {
                        "node": identity,
                        "mode": normalized_mode,
                        "target": target,
                        "readback": str(getattr(new_node, "name", "")),
                    },
                }

        source_base = _base_object(node)
        clone_base = _base_object(new_node)
        shared = source_base is not None and source_base is clone_base

        if normalized_mode == "copy" and shared:
            return {
                "success": False,
                "message": "Copy clone shares the source geometry",
                "data": {"node": identity, "mode": normalized_mode, "clone": new_identity},
            }
        if normalized_mode == "instance" and not shared:
            return {
                "success": False,
                "message": "Instance clone does not share the source geometry",
                "data": {"node": identity, "mode": normalized_mode, "clone": new_identity},
            }
        if normalized_mode == "reference":
            if shared:
                return {
                    "success": False,
                    "message": "Reference clone shares the source geometry like an instance",
                    "data": {"node": identity, "mode": normalized_mode, "clone": new_identity},
                }
            predicate = getattr(rt, "areNodesInstances", None)
            if callable(predicate):
                try:
                    if bool(predicate(node, new_node)):
                        return {
                            "success": False,
                            "message": "Reference clone was reported as an instance",
                            "data": {"node": identity, "mode": normalized_mode, "clone": new_identity},
                        }
                except Exception:  # noqa: BLE001
                    warnings.append(
                        "Could not confirm reference semantics for {}".format(new_identity.get("node_name"))
                    )
            else:
                warnings.append(
                    "Runtime does not expose areNodesInstances; reference semantics for {} are unverified".format(
                        new_identity.get("node_name")
                    )
                )

        entry: Dict[str, Any] = {
            "source": identity,
            "clone": new_identity,
            "mode": normalized_mode,
            "shares_geometry": bool(shared),
        }
        cloned.append(entry)

    data: Dict[str, Any] = {"mode": normalized_mode, "count": len(cloned), "clones": cloned}
    if warnings:
        data["warnings"] = warnings
    return {
        "success": True,
        "message": "Cloned {} node(s) as {}".format(len(cloned), normalized_mode),
        "data": data,
    }
