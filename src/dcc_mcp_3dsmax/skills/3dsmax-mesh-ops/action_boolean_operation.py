"""Boolean union / intersection / subtraction through the native Boolean object."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._curve_utils import (
    create_scene_object,
    delete_node,
    is_node_like,
    read_count,
    resolve_shape,
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

# Operand transfer method: 1 instance, 2 reference, 3 copy, 4 move (deletes the
# operand node). Reference keeps the operand alive in the scene, which is what
# "re-adjust the operands later" requires.
_ADD_METHOD_REFERENCE = 2
# Material method: 1 combines materials without changing them or their IDs.
_MAT_METHOD_COMBINE = 1


def _invoke(
    runtime: Any,
    boolean: Any,
    namespace: Optional[str],
    names: Sequence[str],
    arg_sets: Sequence[Sequence[Any]],
) -> Tuple[bool, Any, str]:
    """Call the first accepted shape of a boolean method.

    3ds Max exposes the same operation in three places depending on the class:
    a namespaced interface struct (``ProBoolean.SetBoolOp``), a method on the
    object (``boolObj.setBoolOp``), or a runtime function that takes the object
    first. All three are tried, and every rejection is recorded, so a missing
    capability is reported instead of being downgraded to a no-op.
    """
    attempts: List[str] = []
    owners: List[Tuple[Any, bool]] = []
    if namespace:
        struct_object = getattr(runtime, namespace, None)
        if struct_object is not None:
            owners.append((struct_object, True))
    owners.append((boolean, False))
    owners.append((runtime, True))

    for owner, pass_object in owners:
        for name in names:
            function = getattr(owner, name, None)
            if not callable(function):
                attempts.append("{}: not exposed".format(name))
                continue
            for args in arg_sets:
                call_args = (boolean,) + tuple(args) if pass_object else tuple(args)
                try:
                    result = function(*call_args)
                except Exception as exc:  # noqa: BLE001 - the next shape may work.
                    attempts.append("{}({}): {}".format(name, len(call_args), exc))
                    continue
                return True, result, name
    return False, None, "no accepted call among {} ({})".format(", ".join(names), "; ".join(attempts))


class _BooleanAdapter(object):
    """Class-specific surface over one 3ds Max boolean implementation.

    ProBoolean and the legacy Boolean / Boolean2 object expose different
    operand methods and different operation codes: ``cut`` is 5 on Boolean2 and
    absent from ProBoolean, where 3 means Merge. Sharing one code path between
    them silently produces the wrong solid, so each class carries its own map.
    """

    name = "Boolean"
    constructor_names: Sequence[str] = ()
    namespace: Optional[str] = None
    operation_codes: Dict[str, int] = {}
    operation_getters: Sequence[str] = ()
    operation_setters: Sequence[str] = ()
    operand_counters: Sequence[str] = ()
    operand_adders: Sequence[str] = ()
    operand_setters: Sequence[str] = ()
    operand_removers: Sequence[str] = ()
    operand_extractors: Sequence[str] = ()
    operand_getters: Sequence[str] = ()

    def operation_label(self, code: int) -> Optional[str]:
        """Return the operation name for a native code of this class."""
        for label, native_code in self.operation_codes.items():
            if native_code == code:
                return label
        return None

    def operation_code(self, operation: str) -> Tuple[Optional[int], Optional[str]]:
        """Return the native code for one operation, or why it is unsupported."""
        if operation not in self.operation_codes:
            return None, "{} does not support the {} operation (supported: {})".format(
                self.name, operation, ", ".join(sorted(self.operation_codes))
            )
        return self.operation_codes[operation], None

    def get_operation(self, runtime: Any, boolean: Any) -> Optional[int]:
        """Return the current native mode, or ``None`` when it cannot be read."""
        ok, value, _used = _invoke(runtime, boolean, self.namespace, self.operation_getters, ((),))
        if not ok:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def set_operation(self, runtime: Any, boolean: Any, operation: str) -> Tuple[Optional[int], Optional[str]]:
        """Set the mode and confirm it through the native getter."""
        code, error = self.operation_code(operation)
        if error:
            return None, error
        ok, _value, used = _invoke(runtime, boolean, self.namespace, self.operation_setters, ((code,),))
        if not ok:
            return None, used
        readback = self.get_operation(runtime, boolean)
        if readback is None:
            return None, "{} accepted the operation write but the mode cannot be read back".format(self.name)
        if readback != code:
            return None, "{} kept operation {} instead of the requested {}".format(self.name, readback, code)
        return code, None

    def operand_count(self, runtime: Any, boolean: Any) -> Tuple[Optional[int], Optional[str]]:
        """Return the registered operand count, or why it cannot be confirmed."""
        count, verified = read_count(runtime, boolean, self.operand_counters)
        if not verified:
            return None, (
                "the operand count cannot be read (tried {}), so the operands cannot be confirmed".format(
                    ", ".join(self.operand_counters)
                )
            )
        return count, None

    def _mutate(
        self,
        runtime: Any,
        boolean: Any,
        names: Sequence[str],
        arg_sets: Sequence[Sequence[Any]],
        expected_count: int,
        *,
        action_label: str,
    ) -> Tuple[Optional[int], Optional[str]]:
        """Run one operand mutation and verify the resulting count."""
        ok, _value, detail = _invoke(runtime, boolean, self.namespace, names, arg_sets)
        if not ok:
            return None, detail
        count, error = self.operand_count(runtime, boolean)
        if error:
            return None, error
        if count != expected_count:
            return None, "{} left {} operand(s) instead of the expected {}".format(
                action_label, count, expected_count
            )
        return count, None

    def add_operand(
        self, runtime: Any, boolean: Any, operand: Any, count_before: int
    ) -> Tuple[Optional[int], Optional[str]]:
        """Register one operand and require the count to grow by exactly one."""
        return self._mutate(
            runtime,
            boolean,
            self.operand_adders,
            (
                (operand, _ADD_METHOD_REFERENCE, _MAT_METHOD_COMBINE),
                (operand, _ADD_METHOD_REFERENCE),
                (operand,),
            ),
            count_before + 1,
            action_label="adding an operand",
        )

    def set_operand(
        self, runtime: Any, boolean: Any, index: int, operand: Any, count_before: int
    ) -> Tuple[Optional[int], Optional[bool], Optional[str]]:
        """Replace one operand and confirm the count and, when readable, identity."""
        count, error = self._mutate(
            runtime,
            boolean,
            self.operand_setters,
            ((index, operand), (index, operand, _ADD_METHOD_REFERENCE)),
            count_before,
            action_label="replacing operand {}".format(index),
        )
        if error:
            return None, None, error
        identity_verified = self._operand_matches(runtime, boolean, index, operand)
        return count, identity_verified, None

    def remove_operand(
        self, runtime: Any, boolean: Any, index: int, count_before: int
    ) -> Tuple[Optional[int], Optional[str]]:
        """Remove one operand and require the count to drop by exactly one."""
        return self._mutate(
            runtime,
            boolean,
            self.operand_removers,
            ((index,),),
            count_before - 1,
            action_label="removing operand {}".format(index),
        )

    def extract_operand(
        self, runtime: Any, boolean: Any, index: int, count_before: int
    ) -> Tuple[Optional[int], Optional[str]]:
        """Extract an operand copy and require the operand list to stay intact."""
        return self._mutate(
            runtime,
            boolean,
            self.operand_extractors,
            ((index,), (index, True)),
            count_before,
            action_label="extracting operand {}".format(index),
        )

    def _operand_matches(self, runtime: Any, boolean: Any, index: int, operand: Any) -> Optional[bool]:
        """Return whether slot ``index`` holds ``operand``, or ``None`` if unreadable."""
        ok, value, _used = _invoke(runtime, boolean, self.namespace, self.operand_getters, ((index,),))
        if not ok:
            return None
        wanted = getattr(operand, "handle", None)
        if wanted is None:
            return None
        return getattr(value, "handle", None) == wanted


class _ProBooleanAdapter(_BooleanAdapter):
    """ProBoolean compound object, driven through the ``ProBoolean`` interface."""

    name = "ProBoolean"
    constructor_names = ("ProBoolean",)
    namespace = "ProBoolean"
    # ProBoolean.SetBoolOp takes the 0-based radio state and has no cut mode;
    # 3 is Merge, not a cut, so it is deliberately absent.
    operation_codes = {"union": 0, "intersection": 1, "subtraction": 2}
    operation_getters = ("GetBoolOp", "getBoolOp")
    operation_setters = ("SetBoolOp", "setBoolOp")
    operand_counters = ("NumOps", "numOps", "numOperands", "NumOperands")
    operand_adders = ("SetOperandB", "setOperandB", "AddOp", "addOp", "AddOperand", "addOperand")
    operand_setters = ("SetOp", "setOp", "SetOperand", "setOperand")
    operand_removers = ("RemoveOp", "removeOp", "RemoveOperand", "removeOperand")
    operand_extractors = ("ExtractOp", "extractOp", "ExtractOperand", "extractOperand")
    operand_getters = ("GetOp", "getOp", "GetOperand", "getOperand")


class _Boolean2Adapter(_BooleanAdapter):
    """Legacy Boolean / Boolean2 compound object."""

    name = "Boolean2"
    constructor_names = ("Boolean2", "Boolean")
    # Boolean2 setBoolOp is 1-based: 3 is Subtraction (A-B) and 5 is Cut.
    operation_codes = {"union": 1, "intersection": 2, "subtraction": 3, "cut": 5}
    operation_getters = ("getBoolOp", "GetBoolOp")
    operation_setters = ("setBoolOp", "SetBoolOp")
    operand_counters = ("NumOps", "numOps", "numOperands", "NumOperands")
    operand_adders = ("setOperandB", "SetOperandB", "AddOp", "addOp", "AddOperand", "addOperand")
    operand_setters = ("SetOp", "setOp", "SetOperand", "setOperand")
    operand_removers = ("RemoveOp", "removeOp", "RemoveOperand", "removeOperand")
    operand_extractors = ("ExtractOp", "extractOp", "ExtractOperand", "extractOperand")
    operand_getters = ("GetOp", "getOp", "GetOperand", "getOperand")


_ADAPTERS: Tuple[_BooleanAdapter, ...] = (_ProBooleanAdapter(), _Boolean2Adapter())

def _validation_error(message: str) -> Dict[str, Any]:
    return {"success": False, "status": "error", "message": message, "data": {}}


def _describe_operand(reference: Any) -> str:
    """Return a readable label for one rejected operand entry."""
    if isinstance(reference, str):
        return "the name {!r}".format(reference)
    if isinstance(reference, dict):
        return "the mapping {!r}".format(sorted(reference))
    return "a {} value".format(type(reference).__name__)


def _validate_operand_reference(reference: Any) -> Optional[str]:
    """Return an error when an operand entry cannot identify a scene node."""
    if isinstance(reference, str):
        return None if reference.strip() else "operands entries must be non-empty node names"
    if isinstance(reference, dict):
        if reference.get("node_name") or reference.get("name") or reference.get("handle") is not None:
            return None
        return "operands entries must carry a node_name, name, or handle"
    if is_node_like(reference):
        return None
    return "operands entries must be node names, name/handle objects, or scene nodes, not {}".format(
        _describe_operand(reference)
    )


def _resolve_operand(runtime: Any, reference: Any) -> tuple:
    """Resolve one validated operand reference into a scene node."""
    error = _validate_operand_reference(reference)
    if error:
        return None, mesh_error(error)
    if isinstance(reference, dict):
        return resolve_shape(
            runtime,
            node_name=reference.get("node_name") or reference.get("name"),
            handle=reference.get("handle"),
        )
    if isinstance(reference, str):
        return resolve_shape(runtime, node_name=reference)
    return reference, None


def _class_name(runtime: Any, boolean: Any) -> str:
    """Return the node's class name when the host reports one."""
    class_of = getattr(runtime, "classOf", None)
    if callable(class_of):
        try:
            return str(class_of(boolean))
        except Exception:  # noqa: BLE001 - fall through to the reported attribute.
            pass
    return str(getattr(boolean, "class_name", "") or "")


def _detect_adapter(runtime: Any, boolean: Any) -> Tuple[Optional[_BooleanAdapter], Optional[str]]:
    """Pick the adapter whose interface the node actually answers to.

    Both classes share the operand-count property, so the count alone cannot
    tell them apart. Three signals are tried in order of strength: the reported
    class name, a mode that only one class's code map explains (Boolean2 is
    1-based and owns 5 for cut; ProBoolean starts at 0 and has no cut), and
    finally the presence of any readable operand count.
    """
    class_name = _class_name(runtime, boolean)
    if class_name:
        for adapter in _ADAPTERS:
            if adapter.name.lower() in class_name.lower():
                return adapter, None

    distinctive = []
    for adapter in _ADAPTERS:
        code = adapter.get_operation(runtime, boolean)
        if code is not None and code in adapter.operation_codes.values():
            distinctive.append(adapter)
    if len(distinctive) == 1:
        return distinctive[0], None
    if distinctive:
        return distinctive[0], None

    for adapter in _ADAPTERS:
        count, _error = adapter.operand_count(runtime, boolean)
        if count is not None:
            return adapter, None
    return None, (
        "the node exposes no known Boolean/ProBoolean operand interface, so its mode and "
        "operands cannot be read or changed"
    )


def _create_boolean(runtime: Any, operation: str) -> tuple:
    """Construct a boolean using the first class that can express ``operation``."""
    failures: List[str] = []
    for adapter in _ADAPTERS:
        _code, code_error = adapter.operation_code(operation)
        if code_error:
            failures.append(code_error)
            continue
        node, used_class, error = create_scene_object(runtime, adapter.constructor_names)
        if error:
            failures.append(error)
            continue
        if not is_node_like(node):
            failures.append(
                "3ds Max did not return a scene node for the {} constructor".format(used_class)
            )
            continue
        return adapter, node, used_class, None
    return None, None, None, "; ".join(failures) or "no boolean class is available"


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

    The boolean is the native ProBoolean or Boolean/Boolean2 compound object,
    reached through a class-specific adapter, so the operands stay live: one can
    be swapped, extracted into its own node, or removed without rebuilding the
    stack.

    Every write is confirmed by reading the object back. A mode the host
    coerced, an operand that did not register, or an operand count the host
    does not expose all fail the call, and a failed ``create`` removes the node
    it made and reports whether that removal was actually confirmed.
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
            for entry in operands:
                reference_error = _validate_operand_reference(entry)
                if reference_error:
                    raise ValueError(reference_error)
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

            adapter, boolean_object, used_class, error = _create_boolean(rt, normalized_operation)
            if error:
                return mesh_error(error)
            created_node = True

            code, error = adapter.set_operation(rt, boolean_object, normalized_operation)
            if error:
                rolled_back = delete_node(rt, boolean_object)
                return mesh_error(error, rolled_back=rolled_back)

            registered: List[Dict[str, Any]] = []
            for reference in [base] + references:
                operand, error = _resolve_operand(rt, reference)
                if error:
                    payload = dict(error["data"])
                    payload["rolled_back"] = delete_node(rt, boolean_object)
                    return mesh_error(error["message"], **payload)
                count_before, error = adapter.operand_count(rt, boolean_object)
                if error:
                    rolled_back = delete_node(rt, boolean_object)
                    return mesh_error(error, rolled_back=rolled_back)
                count_after, error = adapter.add_operand(rt, boolean_object, operand, count_before)
                if error:
                    rolled_back = delete_node(rt, boolean_object)
                    return mesh_error(error, rolled_back=rolled_back)
                registered.append(node_identity(operand))
                count_before = count_after

            if normalized_name is not None:
                try:
                    boolean_object.name = normalized_name
                except Exception as exc:  # noqa: BLE001 - a naming failure is a hard failure.
                    rolled_back = delete_node(rt, boolean_object)
                    return mesh_error(
                        "could not name the boolean node: {}".format(exc), rolled_back=rolled_back
                    )

            return mesh_success(
                "Created boolean: {}".format(str(boolean_object.name)),
                node=node_identity(boolean_object),
                boolean_class=adapter.name,
                created_with=used_class,
                operation=normalized_operation,
                operation_code=code,
                operands=registered,
                operand_count=count_before,
            )

        boolean_object, error = resolve_shape(rt, node_name=node_name, handle=handle)
        if error:
            return error
        adapter, error = _detect_adapter(rt, boolean_object)
        if error:
            return mesh_error(error, node=node_identity(boolean_object))

        if normalized_action == "read":
            code = adapter.get_operation(rt, boolean_object)
            count, count_error = adapter.operand_count(rt, boolean_object)
            payload: Dict[str, Any] = {
                "node": node_identity(boolean_object),
                "boolean_class": adapter.name,
                "operation_code": code,
                "operation": adapter.operation_label(code) if code is not None else None,
                "operand_count": count,
            }
            if count_error:
                payload["operand_count_error"] = count_error
            if code is None:
                payload["operation_error"] = "the node reports no boolean mode the adapter can read"
            return mesh_success("Read boolean state", **payload)

        if normalized_action == "set_operation":
            code, error = adapter.set_operation(rt, boolean_object, normalized_operation)
            if error:
                return mesh_error(error, node=node_identity(boolean_object))
            return mesh_success(
                "Set boolean operation",
                node=node_identity(boolean_object),
                boolean_class=adapter.name,
                operation=normalized_operation,
                operation_code=code,
            )

        # Every remaining action mutates the operand list, so the starting
        # count is read first and each mutation is verified against it.
        count_before, error = adapter.operand_count(rt, boolean_object)
        if error:
            return mesh_error(error, node=node_identity(boolean_object))

        if normalized_action == "add_operands":
            registered = []
            for reference in references:
                operand, error = _resolve_operand(rt, reference)
                if error:
                    return error
                count_after, error = adapter.add_operand(rt, boolean_object, operand, count_before)
                if error:
                    return mesh_error(error, added=registered, node=node_identity(boolean_object))
                registered.append(node_identity(operand))
                count_before = count_after
            return mesh_success(
                "Added {} boolean operand(s)".format(len(registered)),
                node=node_identity(boolean_object),
                boolean_class=adapter.name,
                added=registered,
                operand_count=count_before,
            )

        if normalized_index > count_before:
            return mesh_error(
                "operand_index {} is out of range".format(normalized_index),
                node=node_identity(boolean_object),
                operand_count=count_before,
            )

        if normalized_action == "set_operand":
            operand, error = _resolve_operand(rt, references[0])
            if error:
                return error
            count_after, identity_verified, error = adapter.set_operand(
                rt, boolean_object, normalized_index, operand, count_before
            )
            if error:
                return mesh_error(error, node=node_identity(boolean_object))
            payload = {
                "node": node_identity(boolean_object),
                "boolean_class": adapter.name,
                "operand_index": normalized_index,
                "operand": node_identity(operand),
                "operand_count": count_after,
                "operand_identity_verified": bool(identity_verified),
            }
            if identity_verified is None:
                payload["warnings"] = [
                    "the host does not expose an operand getter, so the slot contents were not confirmed"
                ]
            return mesh_success(
                "Replaced boolean operand {}".format(normalized_index), **payload
            )

        if normalized_action == "extract_operand":
            count_after, error = adapter.extract_operand(rt, boolean_object, normalized_index, count_before)
            if error:
                return mesh_error(error, node=node_identity(boolean_object))
            return mesh_success(
                "Extracted boolean operand {}".format(normalized_index),
                node=node_identity(boolean_object),
                boolean_class=adapter.name,
                operand_index=normalized_index,
                operand_count=count_after,
                note="Re-read the scene: the extracted copy is a new node.",
            )

        count_after, error = adapter.remove_operand(rt, boolean_object, normalized_index, count_before)
        if error:
            return mesh_error(error, node=node_identity(boolean_object))
        return mesh_success(
            "Removed boolean operand {}".format(normalized_index),
            node=node_identity(boolean_object),
            boolean_class=adapter.name,
            operand_index=normalized_index,
            operand_count=count_after,
        )
    except Exception as exc:  # noqa: BLE001 - host failures roll a new node back.
        rolled_back = delete_node(rt, boolean_object) if (created_node and boolean_object is not None) else False
        return mesh_error(
            "boolean_operation failed",
            exception_type=type(exc).__name__,
            exception=str(exc),
            rolled_back=rolled_back,
        )
