"""Create an object from an arbitrary creatable 3ds Max class."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import node_bounding_box, point3_to_list, serialize_property_value
from dcc_mcp_3dsmax.api import get_runtime, with_max

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Runtime symbols that must never be reachable through object creation.
BLOCKED_CLASSES = frozenset(
    {
        "execute",
        "delete",
        "quitMax",
        "fileIn",
        "fileOut",
        "loadMaxFile",
        "saveMaxFile",
        "mergeMaxFile",
        "getNodeByName",
        "select",
        "clearSelection",
        "gc",
        "setProperty",
        "getProperty",
        "pyhelper",
    }
)


@with_max
def main(
    object_type: str,
    name: Optional[str] = None,
    position: Any = None,
    rotation: Any = None,
    scale: Any = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create one node from a creatable 3ds Max class and report its placement.

    The class is looked up on the runtime and the returned value must be a
    scene node with a name, so a failed or non-node creation is reported as a
    failure instead of an empty success.
    """
    class_name = str(object_type or "").strip()
    if not IDENTIFIER_RE.match(class_name):
        return {
            "success": False,
            "message": "object_type must be a plain 3ds Max class name",
            "data": {"object_type": object_type},
        }
    if class_name in BLOCKED_CLASSES:
        return {
            "success": False,
            "message": "object_type is not creatable",
            "data": {"object_type": class_name},
        }

    rt = get_runtime()
    factory = getattr(rt, class_name, None)
    if factory is None:
        return {
            "success": False,
            "message": "3ds Max runtime has no such class",
            "data": {"object_type": class_name},
        }
    if not callable(factory):
        return {
            "success": False,
            "message": "Runtime symbol is not a creatable class",
            "data": {"object_type": class_name, "symbol_type": type(factory).__name__},
        }

    kwargs: Dict[str, Any] = {}
    for key, item in (params or {}).items():
        if not IDENTIFIER_RE.match(str(key)):
            return {
                "success": False,
                "message": "Invalid parameter name for object creation",
                "data": {"object_type": class_name, "parameter": str(key)},
            }
        kwargs[str(key)] = item

    try:
        node = factory(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "message": "3ds Max could not create the object",
            "data": {
                "object_type": class_name,
                "params": sorted(kwargs),
                "error": "{}: {}".format(type(exc).__name__, exc),
            },
        }

    node_name = getattr(node, "name", None)
    if node_name is None:
        return {
            "success": False,
            "message": "Creation did not return a scene node",
            "data": {"object_type": class_name, "returned_type": type(node).__name__},
        }

    if name:
        try:
            node.name = str(name)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "message": "Object was created but could not be named",
                "data": {
                    "object_type": class_name,
                    "requested_name": str(name),
                    "error": "{}: {}".format(type(exc).__name__, exc),
                },
            }
        if str(getattr(node, "name", "")) != str(name):
            return {
                "success": False,
                "message": "Object name was not applied",
                "data": {
                    "object_type": class_name,
                    "requested_name": str(name),
                    "readback": str(getattr(node, "name", "")),
                },
            }

    placement: Dict[str, Any] = {}
    if position is not None:
        try:
            node.pos = rt.Point3(*[float(item) for item in position])
            placement["position"] = point3_to_list(node.pos)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "message": "Object was created but position was rejected",
                "data": {"object_type": class_name, "error": str(exc), "requested": position},
            }
    if rotation is not None:
        try:
            node.rotation = rt.EulerAngles(*[float(item) for item in rotation])
            placement["rotation"] = point3_to_list(node.rotation)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "message": "Object was created but rotation was rejected",
                "data": {"object_type": class_name, "error": str(exc), "requested": rotation},
            }
    if scale is not None:
        try:
            node.scale = rt.Point3(*[float(item) for item in scale])
            placement["scale"] = point3_to_list(node.scale)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "message": "Object was created but scale was rejected",
                "data": {"object_type": class_name, "error": str(exc), "requested": scale},
            }

    identity: Dict[str, Any] = {
        "node_name": str(getattr(node, "name", "")),
        "object_id": getattr(node, "handle", None),
        "class_name": class_name,
    }

    data: Dict[str, Any] = {
        "node": identity,
        "object_type": class_name,
        "params": {key: serialize_property_value(item) for key, item in kwargs.items()},
        "placement": {
            "position": point3_to_list(getattr(node, "pos", None)),
            "rotation": point3_to_list(getattr(node, "rotation", None)),
            "scale": point3_to_list(getattr(node, "scale", None)),
        },
        "bounding_box": node_bounding_box(node),
    }
    data["placement"].update(placement)

    return {
        "success": True,
        "message": "Created {} object: {}".format(class_name, identity["node_name"]),
        "data": data,
    }
