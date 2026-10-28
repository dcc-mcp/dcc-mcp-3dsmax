"""Create an object from a creatable 3ds Max class."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from dcc_mcp_3dsmax._curve_utils import delete_node
from dcc_mcp_3dsmax._env import ENV_DISABLE_ARBITRARY_SCRIPT, resolve_arbitrary_script_disabled
from dcc_mcp_3dsmax._scene_utils import node_bounding_box, point3_to_list, serialize_property_value, vector_equal
from dcc_mcp_3dsmax.api import get_runtime, with_max

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _fail_created(runtime: Any, node: Any, message: str, **data: Any) -> Dict[str, Any]:
    """Delete a node this call created and report whether the rollback verified.

    Every failure after ``factory(**kwargs)`` has produced a node must take the
    node back out of the scene; otherwise retrying the request keeps creating
    orphan nodes. The verified ``rolled_back`` flag is reported to the caller.
    """
    data["rolled_back"] = delete_node(runtime, node)
    return {"success": False, "message": message, "data": data}

# Layer 1: defense in depth. Runtime symbols that must never be reachable
# through object creation, even if the class check below is ever bypassed.
BLOCKED_CLASSES = frozenset(
    {
        # Script/eval entry points.
        "execute",
        "pyhelper",
        # Scene and node destruction.
        "delete",
        "quitMax",
        "resetMaxFile",
        "saveNodes",
        "createInstance",
        # Filesystem and scene IO.
        "deleteFile",
        "createFile",
        "openFile",
        "importFile",
        "exportFile",
        "fileIn",
        "fileOut",
        "loadMaxFile",
        "saveMaxFile",
        "mergeMaxFile",
        # Render and selection side effects.
        "render",
        "select",
        "clearSelection",
        "getNodeByName",
        "setProperty",
        "getProperty",
        "gc",
    }
)


def _prove_creatable_class(runtime: Any, symbol: Any) -> Tuple[bool, bool, Dict[str, Any]]:
    """Prove a runtime symbol is a creatable 3ds Max class.

    Only classes take part in the MAX class hierarchy, so ``superClassOf``
    succeeds for them and fails for bare global functions. That single
    structural check closes the whole "arbitrary runtime symbol" surface
    without maintaining an unbounded list of dangerous function names.

    A symbol is proven as soon as the call returns anything other than
    ``None``. The return shape is deliberately not inspected any further:
    depending on the pymxs mapping it is either a wrapper instance or the
    generated class object, and every Python class is callable, so testing
    ``callable()`` would reject the very value that proves the symbol is a
    class. Bare global functions are already rejected by the ``except``
    branch below.

    Returns ``(proven, predicate_available, evidence)``.
    """
    predicate = getattr(runtime, "superClassOf", None)
    if not callable(predicate):
        return False, False, {"predicate": "superClassOf", "available": False}

    try:
        value = predicate(symbol)
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            True,
            {
                "predicate": "superClassOf",
                "available": True,
                "error": "{}: {}".format(type(exc).__name__, exc),
            },
        )

    if value is None:
        return (
            False,
            True,
            {"predicate": "superClassOf", "available": True, "superclass": None},
        )

    return (
        True,
        True,
        {
            "predicate": "superClassOf",
            "available": True,
            "superclass": str(value),
            # Diagnostic only, never a rejection signal: a real host maps the
            # superclass onto a class object, which is always callable.
            "superclass_callable": callable(value),
        },
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

    The class is looked up on the runtime, proven to be a creatable class
    rather than a bare global function, and the returned value must be a scene
    node with a name, so a failed or non-node creation is reported as a
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

    arbitrary_script_disabled = resolve_arbitrary_script_disabled()
    proven, predicate_available, evidence = _prove_creatable_class(rt, factory)
    if not proven:
        # The gate is unconditional: an unprovable symbol is refused whether or
        # not the variable below is set. It neither tightens nor loosens this
        # check and is named only so operators can locate the refusal.
        if predicate_available:
            message = "Runtime symbol is not a creatable 3ds Max class"
        else:
            message = (
                "Runtime symbol is not a creatable 3ds Max class: this runtime exposes "
                "no creatable-class predicate, so no symbol can be proven"
            )
        message = "{} ({} neither tightens nor loosens this check)".format(message, ENV_DISABLE_ARBITRARY_SCRIPT)
        return {
            "success": False,
            "message": message,
            "data": {
                "object_type": class_name,
                "arbitrary_script_disabled": arbitrary_script_disabled,
                "evidence": evidence,
            },
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
        return _fail_created(
            rt, node, "Creation did not return a scene node",
            object_type=class_name, returned_type=type(node).__name__,
        )

    if name:
        try:
            node.name = str(name)
        except Exception as exc:  # noqa: BLE001
            return _fail_created(
                rt, node, "Object was created but could not be named",
                object_type=class_name,
                requested_name=str(name),
                error="{}: {}".format(type(exc).__name__, exc),
            )
        if str(getattr(node, "name", "")) != str(name):
            return _fail_created(
                rt, node, "Object name was not applied",
                object_type=class_name,
                requested_name=str(name),
                readback=str(getattr(node, "name", "")),
            )

    if rotation is not None:
        try:
            node.rotation = rt.EulerAngles(*[float(item) for item in rotation])
        except Exception as exc:  # noqa: BLE001
            return _fail_created(
                rt, node, "Object was created but rotation was rejected",
                object_type=class_name, error=str(exc), requested=rotation,
            )
    if scale is not None:
        try:
            node.scale = rt.Point3(*[float(item) for item in scale])
        except Exception as exc:  # noqa: BLE001
            return _fail_created(
                rt, node, "Object was created but scale was rejected",
                object_type=class_name, error=str(exc), requested=scale,
            )

    # World rotation/scale can orbit a node, so apply its requested position last.
    if position is not None:
        try:
            node.pos = rt.Point3(*[float(item) for item in position])
        except Exception as exc:  # noqa: BLE001
            return _fail_created(
                rt, node, "Object was created but position was rejected",
                object_type=class_name, error=str(exc), requested=position,
            )
    if position is not None and not vector_equal(getattr(node, "pos", None), position):
        # The node already exists; leaving it behind would make every retry add
        # yet another orphan. Delete it and report whether the rollback verified.
        return _fail_created(
            rt, node, "Object placement did not verify",
            object_type=class_name,
            requested=position,
            readback=point3_to_list(getattr(node, "pos", None)),
        )
    rotation_value = getattr(node, "rotation", None)
    if hasattr(rotation_value, "w"):
        rotation_value = rt.quatToEuler(rotation_value)

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
            "rotation": point3_to_list(rotation_value),
            "scale": point3_to_list(getattr(node, "scale", None)),
        },
        "bounding_box": node_bounding_box(node),
        "creatable_class_evidence": evidence,
    }

    return {
        "success": True,
        "message": "Created {} object: {}".format(class_name, identity["node_name"]),
        "data": data,
    }
