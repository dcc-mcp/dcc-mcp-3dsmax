"""Set visibility or freeze state for 3ds Max nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import node_identity, resolve_node_objects, set_node_visible
from dcc_mcp_3dsmax.api import get_runtime, with_max

VISIBILITY_STATES = ("show", "hide", "freeze", "unfreeze")


def _apply_freeze(runtime: Any, node: Any, frozen: bool) -> None:
    """Freeze or unfreeze one node, preferring the property over helpers."""
    try:
        node.isFrozen = bool(frozen)
        return
    except Exception:  # noqa: BLE001
        pass
    helper = getattr(runtime, "freeze", None) if frozen else getattr(runtime, "unfreeze", None)
    if not callable(helper):
        raise RuntimeError("runtime does not expose freeze/unfreeze helpers")
    helper(node)


def _is_frozen(node: Any) -> Optional[bool]:
    value = getattr(node, "isFrozen", None)
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, bool):
        return value
    return None


@with_max
def main(
    visible: Optional[bool] = None,
    node_names: Optional[list] = None,
    handles: Optional[list] = None,
    state: Optional[str] = None,
) -> Dict[str, Any]:
    """Show, hide, freeze, or unfreeze nodes.

    ``state`` is the explicit four-way control. The boolean ``visible``
    parameter stays supported and maps to ``show`` / ``hide``. Freeze results
    are confirmed by readback, so a host that ignored the request is reported
    as a failure rather than a success.
    """
    if state is None and visible is None:
        return {
            "success": False,
            "message": "state or visible is required",
            "data": {"supported_states": list(VISIBILITY_STATES)},
        }

    if state is not None:
        normalized = str(state).strip().lower()
        if normalized not in VISIBILITY_STATES:
            return {
                "success": False,
                "message": "Unsupported visibility state",
                "data": {"state": state, "supported": list(VISIBILITY_STATES)},
            }
    else:
        normalized = "show" if bool(visible) else "hide"

    rt = get_runtime()
    result = resolve_node_objects(rt, node_names=node_names, handles=handles)
    if not result.get("success"):
        return {"success": False, "message": result["message"], "data": result}

    nodes = []
    visible_flag = None
    for node in result["objects"]:
        identity = node_identity(node)
        if normalized in ("show", "hide"):
            set_node_visible(rt, node, normalized == "show")
            readback = not bool(getattr(node, "isHidden", False))
            if readback != (normalized == "show"):
                return {
                    "success": False,
                    "message": "Visibility write did not take effect",
                    "data": {"node": identity, "state": normalized, "readback_visible": readback},
                }
            identity["visible"] = readback
            visible_flag = readback
        else:
            try:
                _apply_freeze(rt, node, normalized == "freeze")
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "message": "Could not change freeze state",
                    "data": {
                        "node": identity,
                        "state": normalized,
                        "error": "{}: {}".format(type(exc).__name__, exc),
                    },
                }
            readback = _is_frozen(node)
            if readback is None:
                return {
                    "success": False,
                    "message": "Freeze state could not be read back",
                    "data": {"node": identity, "state": normalized},
                }
            if readback != (normalized == "freeze"):
                return {
                    "success": False,
                    "message": "Freeze state write did not take effect",
                    "data": {"node": identity, "state": normalized, "readback_frozen": readback},
                }
            identity["frozen"] = readback
        nodes.append(identity)

    data: Dict[str, Any] = {"nodes": nodes, "state": normalized}
    if visible_flag is not None:
        data["visible"] = visible_flag
    return {
        "success": True,
        "message": "Updated {} node(s) to {}".format(len(nodes), normalized),
        "data": data,
    }
