"""Boolean union / intersection / subtraction through the native Boolean object."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from dcc_mcp_3dsmax._curve_utils import (
    call_first,
    create_scene_object,
    delete_node,
    is_node_like,
    read_count,
    read_property_first,
    resolve_shape,
    set_property_first,
    validated_int,
    validated_name,
)
from dcc_mcp_3dsmax._mesh_ops import mesh_error, mesh_success
from dcc_mcp_3dsmax._scene_utils import node_identity
from dcc_mcp_3dsmax.api import get_runtime, with_max

_ACTIONS = (
    "create",
    "set_operation",
    "add_operands",
    "set_operand",
    "extract_operand",
    "remove_operand",
    "read",
)

_OPERATIONS = ("union", "intersection", "subtraction", "cut")

# 3ds Max encodes the boolean mode as an integer on `op`. The mapping below is
# verified by reading the value back, so a host that numbers the modes
# differently fails the call instead of producing the wrong solid.
_OPERATION_CODES = {"union": 0, "intersection": 1, "subtraction": 2, "cut": 3}

_OP_PROPERTIES = ("op", "operation", "Operation")
_OPERAND_COUNTS = ("NumOps", "numOps", "numOperands", "NumOperands")


def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _resolve_operand(runtime: Any, reference: Any) -> tuple:
    """Resolve one operand reference, accepting a name, a handle, or a node."""
    if isinstance(reference, dict):
        return resolve_shape(
            runtime,
            node_name=reference.get("node_name") or reference.get("name"),
            handle=reference.get("handle"),
        )
    if isinstance(reference, str):
        return resolve_shape(runtime, node_name=reference)
    return reference, None


def _add_operand(boolean_object: Any, operand: Any) -> tuple:
    """Register one operand and return ``(used_method, error)``."""
    ok, used_method, error = call_first(
        boolean_object,
        ("AddOp", "addOp", "AddOperand", "addOperand"),
        ((operand,),),
        owner_label="the Boolean object",
    )
    if not ok:
        return None, error
    return used_method, None


def _operand_count(runtime: Any, boolean_object: Any) -> tuple:
    """Return ``(count, error)`` for the registered operands."""
    count, verified = read_count(runtime, boolean_object, _OPERAND_COUNTS)
    if not verified:
        return None, (
            "the operand count cannot be read, so the operands cannot be confirmed; "
            "tried {}".format(", ".join(_OPERAND_COUNTS))
        )
    return count, None


@with_max
def main(
    action: str = "read",
    operation: Optional[str] = None,
    node_name: Optional[str] = None,
    handle: Optional[int] = None,
    base_node: Optional[str] = None,
    operands: Optional[Sequence[Any]] = None,
    operand_index: Optional[int] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a boolean operation and re-adjust its operands later.

    The boolean is the native Boolean / ProBoolean object, so the operands stay
    live: an operand can be swapped, extracted into its own node, or removed
    without rebuilding the stack. Every write is confirmed by reading the
    object back - a mode or operand the host did not register is reported as a
    failure, never as a warning on a successful call.
    """
    try:
        normalized_action = str(action or "read").strip().lower()
        if normalized_action not in _ACTIONS:
            raise ValueError("action must be one of {}".format(", ".join(_ACTIONS)))
        normalized_operation = None
        if operation is not None:
            normalized_operation = str(operation).strip().lower()
            if normalized_operation not in _OPERATIONS:
                raise ValueError("operation must be one of {}".format(", ".join(_OPERATIONS)))
        if normalized_action == "create" and normalized_operation is None:
            raise ValueError("operation is required for create")
        if normalized_action == "set_operation" and normalized_operation is None:
            raise ValueError("operation is required for set_operation")
        if normalized_action in ("add_operands", "set_operand") and not operands:
            raise ValueError("operands is required for {}".format(normalized_action))
        if normalized_action in ("extract_operand", "remove_operand", "set_operand") and operand_index is None:
            raise ValueError("operand_index is required for {}".format(normalized_action))
        normalized_index = None
        if operand_index is not None:
            normalized_index = validated_int(operand_index, "operand_index", default=1, minimum=1, maximum=4096)
        normalized_name = validated_name(name)
        normalized_base = validated_name(base_node, "base_node")
        if normalized_action == "create" and not normalized_base:
            raise ValueError("base_node is required for create")

        references: List[Any] = []
        if operands is not None:
            if isinstance(operands, (str, bytes)) or not isinstance(operands, Sequence):
                raise ValueError("operands must be an array of node names or name/handle objects")
            if not 1 <= len(operands) <= 64:
                raise ValueError("operands must contain between 1 and 64 entries")
            references = list(operands)
    except ValueError as exc:
        return _validation_error(str(exc))

    rt = get_runtime()
    boolean_object = None
    created_node = False

    try:
        if normalized_action == "create":
            base, error = resolve_shape(rt, node_name=normalized_base)
            if error:
                return error
            boolean_object, used_class, error = create_scene_object(rt, ("ProBoolean", "Boolean"))
            if error:
                return mesh_error(error)
            if not is_node_like(boolean_object):
                return mesh_error(
                    "3ds Max did not return a scene node for the Boolean constructor",
                    constructor_returned=type(boolean_object).__name__,
                )
            created_node = True

            used_property, error = set_property_first(
                boolean_object,
                _OP_PROPERTIES,
                _OPERATION_CODES[normalized_operation],
                owner_label="the Boolean object",
            )
            if error:
                delete_node(rt, boolean_object)
                return mesh_error(error, rolled_back=True)

            registered: List[Dict[str, Any]] = []
            for reference in [base] + references:
                operand, error = _resolve_operand(rt, reference)
                if error:
                    delete_node(rt, boolean_object)
                    return error
                _used_method, error = _add_operand(boolean_object, operand)
                if error:
                    delete_node(rt, boolean_object)
                    return mesh_error(error, rolled_back=True)
                registered.append(node_identity(operand))

            count, error = _operand_count(rt, boolean_object)
            if error:
                delete_node(rt, boolean_object)
                return mesh_error(error, rolled_back=True)
            if count != len(registered):
                delete_node(rt, boolean_object)
                return mesh_error(
                    "the boolean registered {} of {} operands".format(count, len(registered)),
                    requested_operand_count=len(registered),
                    registered_operand_count=count,
                    rolled_back=True,
                )

            if normalized_name is not None:
                try:
                    boolean_object.name = normalized_name
                except Exception as exc:  # noqa: BLE001 - a naming failure is a hard failure.
                    delete_node(rt, boolean_object)
                    return mesh_error("could not name the boolean node: {}".format(exc), rolled_back=True)

            found, stored_code = read_property_first(boolean_object, (used_property,))
            return mesh_success(
                "Created boolean: {}".format(str(boolean_object.name)),
                node=node_identity(boolean_object),
                boolean_class=used_class,
                operation=normalized_operation,
                operation_property=used_property,
                operation_code=stored_code if found else None,
                operands=registered,
                operand_count=count,
            )

        boolean_object, error = resolve_shape(rt, node_name=node_name, handle=handle)
        if error:
            return error

        if normalized_action == "read":
            found, stored_code = read_property_first(boolean_object, _OP_PROPERTIES)
            reverse = {code: label for label, code in _OPERATION_CODES.items()}
            count, count_error = _operand_count(rt, boolean_object)
            payload: Dict[str, Any] = {
                "node": node_identity(boolean_object),
                "operation_code": stored_code if found else None,
                "operation": reverse.get(stored_code) if found else None,
                "operand_count": count,
            }
            if count_error:
                payload["operand_count_error"] = count_error
            if not found:
                payload["operation_error"] = "the boolean exposes none of {}".format(", ".join(_OP_PROPERTIES))
            return mesh_success("Read boolean state", **payload)

        if normalized_action == "set_operation":
            used_property, error = set_property_first(
                boolean_object,
                _OP_PROPERTIES,
                _OPERATION_CODES[normalized_operation],
                owner_label="the Boolean object",
            )
            if error:
                return mesh_error(error)
            found, stored_code = read_property_first(boolean_object, (used_property,))
            return mesh_success(
                "Set boolean operation",
                node=node_identity(boolean_object),
                operation=normalized_operation,
                operation_property=used_property,
                operation_code=stored_code if found else None,
            )

        if normalized_action == "add_operands":
            registered = []
            for reference in references:
                operand, error = _resolve_operand(rt, reference)
                if error:
                    return mesh_error(error, added=registered)
                _used_method, error = _add_operand(boolean_object, operand)
                if error:
                    return mesh_error(error, added=registered)
                registered.append(node_identity(operand))
            count, error = _operand_count(rt, boolean_object)
            if error:
                return mesh_error(error, added=registered)
            return mesh_success(
                "Added {} boolean operand(s)".format(len(registered)),
                node=node_identity(boolean_object),
                added=registered,
                operand_count=count,
            )

        if normalized_action == "set_operand":
            operand, error = _resolve_operand(rt, references[0])
            if error:
                return error
            ok, used_method, error = call_first(
                boolean_object,
                ("SetOp", "setOp", "SetOperand"),
                ((normalized_index, operand),),
                owner_label="the Boolean object",
            )
            if not ok:
                return mesh_error(error)
            count, error = _operand_count(rt, boolean_object)
            if error:
                return mesh_error(error)
            if normalized_index > count:
                return mesh_error(
                    "operand_index {} is out of range".format(normalized_index),
                    operand_count=count,
                )
            return mesh_success(
                "Replaced boolean operand {}".format(normalized_index),
                node=node_identity(boolean_object),
                operand_index=normalized_index,
                operand=node_identity(operand),
                method=used_method,
                operand_count=count,
            )

        if normalized_action == "extract_operand":
            ok, used_method, error = call_first(
                boolean_object,
                ("ExtractOp", "extractOp", "ExtractOperand"),
                ((normalized_index,), (normalized_index, True)),
                owner_label="the Boolean object",
            )
            if not ok:
                return mesh_error(error)
            return mesh_success(
                "Extracted boolean operand {}".format(normalized_index),
                node=node_identity(boolean_object),
                operand_index=normalized_index,
                method=used_method,
                note="Re-read the scene: the extracted copy is a new node.",
            )

        ok, used_method, error = call_first(
            boolean_object,
            ("RemoveOp", "removeOp", "RemoveOperand"),
            ((normalized_index,),),
            owner_label="the Boolean object",
        )
        if not ok:
            return mesh_error(error)
        count, error = _operand_count(rt, boolean_object)
        if error:
            return mesh_error(error)
        return mesh_success(
            "Removed boolean operand {}".format(normalized_index),
            node=node_identity(boolean_object),
            operand_index=normalized_index,
            method=used_method,
            operand_count=count,
        )
    except Exception as exc:  # noqa: BLE001 - host failures roll a new node back.
        if created_node and boolean_object is not None:
            delete_node(rt, boolean_object)
        return mesh_error(
            "boolean_operation failed",
            exception_type=type(exc).__name__,
            exception=str(exc),
            rolled_back=created_node,
        )
