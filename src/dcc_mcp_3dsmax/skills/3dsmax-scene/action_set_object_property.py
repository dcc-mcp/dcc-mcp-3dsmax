"""Write one property on a 3ds Max node with verified readback."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import (
    build_property_value,
    matrix_equal,
    read_property,
    resolve_node_object,
    scalar_equal,
    serialize_property_value,
    vector_equal,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

RESERVED_PROPERTIES = frozenset({"handle", "inode"})


def _values_match(current: Any, expected: Any) -> bool:
    """Compare the readback value with the value that was requested."""
    if isinstance(expected, bool) or isinstance(current, bool):
        return bool(current) is bool(expected)
    if isinstance(expected, (int, float)) and isinstance(current, (int, float)):
        return scalar_equal(current, expected)
    if isinstance(expected, str):
        return str(current) == expected
    serialized_expected = serialize_property_value(expected)
    if isinstance(serialized_expected, list):
        return vector_equal(current, expected)
    rows = serialize_property_value(expected)
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        return matrix_equal(current, expected)
    return str(current) == str(expected)


@with_max
def main(
    property: str,
    value: Any,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
) -> Dict[str, Any]:
    """Set one node property and verify the write took effect.

    The tool fails when the property does not exist, when the value type is not
    accepted by the property, or when the post-write readback does not match.
    A successful result always carries the verified readback value.
    """
    name = str(property or "").strip()
    if not name:
        return {"success": False, "message": "property is required", "data": {"property": property}}

    if name.startswith("_"):
        return {
            "success": False,
            "message": "Refusing to write private property",
            "data": {"property": name},
        }
    if name in RESERVED_PROPERTIES:
        return {
            "success": False,
            "message": "Property is read-only",
            "data": {"property": name},
        }

    rt = get_runtime()
    result, node = resolve_node_object(rt, node_name=node_name, handle=handle)
    if node is None:
        return {"success": False, "message": result["message"], "data": result}

    try:
        current_value = getattr(node, name)
    except AttributeError as exc:
        return {
            "success": False,
            "message": "Property does not exist on this node",
            "data": {"node": result.get("node"), "property": name, "error": str(exc)},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "message": "Property could not be read before writing",
            "data": {"node": result.get("node"), "property": name, "error": str(exc)},
        }
    if callable(current_value):
        return {
            "success": False,
            "message": "Refusing to overwrite a method",
            "data": {"node": result.get("node"), "property": name},
        }

    before = read_property(node, name)
    if not before.get("available"):
        return {
            "success": False,
            "message": "Property is not readable on this node",
            "data": {"node": result.get("node"), "property": name, "detail": before},
        }

    try:
        target = build_property_value(rt, current_value, value, name)
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc),
            "data": {
                "node": result.get("node"),
                "property": name,
                "expected_type": type(current_value).__name__,
                "received_value": serialize_property_value(value),
            },
        }

    try:
        setattr(node, name, target)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "message": "3ds Max rejected the property value",
            "data": {
                "node": result.get("node"),
                "property": name,
                "error": "{}: {}".format(type(exc).__name__, exc),
                "expected_type": type(current_value).__name__,
                "received_value": serialize_property_value(value),
            },
        }

    after = read_property(node, name)
    if not after.get("available"):
        return {
            "success": False,
            "message": "Property could not be read back after writing",
            "data": {"node": result.get("node"), "property": name, "detail": after},
        }

    try:
        readback = getattr(node, name)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "message": "Property readback failed after writing",
            "data": {"node": result.get("node"), "property": name, "error": str(exc)},
        }

    if not _values_match(readback, target):
        return {
            "success": False,
            "message": "Property write did not take effect",
            "data": {
                "node": result.get("node"),
                "property": name,
                "requested": serialize_property_value(target),
                "readback": serialize_property_value(readback),
                "previous": before.get("value"),
            },
        }

    return {
        "success": True,
        "message": "Set property {}".format(name),
        "data": {
            "node": result.get("node"),
            "property": name,
            "previous": before.get("value"),
            "requested": serialize_property_value(target),
            "value": serialize_property_value(readback),
            "verified": True,
        },
    }
