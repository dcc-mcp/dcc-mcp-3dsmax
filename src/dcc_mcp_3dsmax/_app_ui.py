"""Qt-backed ``app_ui__*`` tools for 3ds Max.

Reuses the shared core :mod:`dcc_mcp_core.skills.qt_ui_inspector` widget
identity and discovery helpers, then exposes the canonical ``app_ui``
contract (``snapshot`` / ``find`` / ``act`` / ``wait_for``) with policy
controls and audit envelopes.

``qt_ui_inspector__*`` remains read-only diagnostics; ``app_ui__*`` owns
auditable, policy-gated UI actions.  ``raw_coordinate_click`` and
``keyboard_shortcut`` stay denied unless policy explicitly enables them.

Operator opt-out: ``DCC_MCP_3DSMAX_APP_UI=0``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from dcc_mcp_core import json_loads
from dcc_mcp_core._tool_registration import ToolSpec, register_tools
from dcc_mcp_core.adapter_contracts import (
    AppUiAuditRecord,
    AppUiPolicy,
    UiActionKind,
    UiActionResult,
    UiBounds,
    UiControlNode,
    UiErrorCode,
    UiSnapshot,
    UiWaitCondition,
    UiWaitConditionKind,
    UiWaitResult,
)
from dcc_mcp_core.skill import skill_error, skill_success
from dcc_mcp_core.skills.qt_ui_inspector import (
    _binding_unavailable,
    _BindingUnavailable,
    _find_by_id,
    _load_qt,
    _no_application,
    _widget_id,
    _widget_summary,
)

from dcc_mcp_3dsmax._qt_inspector import _MainThreadHandlerProxy

logger = logging.getLogger(__name__)

ENV_APP_UI = "DCC_MCP_3DSMAX_APP_UI"
_CATEGORY_APP_UI = "app-ui"
_TRUTHY = ("1", "true", "yes", "on")
_DEFAULT_MAX_DEPTH = 4
_DEFAULT_MAX_NODES = 256
_POLICY_KEYS = {
    "allow_snapshot",
    "allow_find",
    "allow_mutating_actions",
    "allow_text_entry",
    "allow_keyboard_shortcuts",
    "allow_raw_coordinates",
    "require_scoped_window",
    "allowed_window_titles",
    "allowed_process_ids",
    "audit_sensitive_values",
}
_CONDITION_KEYS = {
    "kind",
    "control_id",
    "query",
    "role",
    "label",
    "text",
    "value",
    "checked",
    "timeout_ms",
    "interval_ms",
}

_SESSIONS: Dict[str, Dict[str, Any]] = {}


def resolve_app_ui_enabled(env: Any = None) -> bool:
    """Return ``True`` unless ``DCC_MCP_3DSMAX_APP_UI`` is falsey."""
    environ = env if env is not None else os.environ
    return str(environ.get(ENV_APP_UI, "1")).strip().lower() in _TRUTHY


def _parse_params(params: Any) -> Dict[str, Any]:
    if isinstance(params, str):
        parsed = json_loads(params)
        return parsed if isinstance(parsed, dict) else {}
    return dict(params or {})


def _safe_session_id(session_id: Any) -> str:
    text = str(session_id or "default")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:80] or "default"


def _policy_from_params(params: Dict[str, Any]) -> AppUiPolicy:
    raw = params.get("policy") or {}
    if not isinstance(raw, dict):
        raw = {}
    return AppUiPolicy(**{key: raw[key] for key in _POLICY_KEYS if key in raw})


def _session_state(session_id: str) -> Dict[str, Any]:
    state = _SESSIONS.get(session_id)
    if state is None:
        state = {"session_id": session_id, "revision": 0, "window_title": "", "process_id": 0}
        _SESSIONS[session_id] = state
    return state


def _snapshot_token(state: Dict[str, Any]) -> str:
    return f"{state['session_id']}:{state['revision']}"


def _qt_role(widget: Any) -> str:
    klass = widget.__class__.__name__
    if "PushButton" in klass or "ToolButton" in klass:
        return "button"
    if "CheckBox" in klass or "RadioButton" in klass:
        return "checkbox"
    if "LineEdit" in klass or "TextEdit" in klass or "PlainTextEdit" in klass or "SpinBox" in klass:
        return "text_field"
    if "ComboBox" in klass:
        return "combo_box"
    if "Label" in klass:
        return "label"
    if "MainWindow" in klass or "Dialog" in klass:
        return "window"
    return "control"


def _widget_label(widget: Any) -> str:
    for getter in ("accessibleName", "windowTitle", "text", "objectName"):
        fn = getattr(widget, getter, None)
        if callable(fn):
            try:
                value = fn()
            except Exception:
                continue
            if value:
                return str(value)
    return widget.__class__.__name__


def _widget_text(widget: Any) -> Optional[str]:
    for getter in ("text", "currentText", "placeholderText"):
        fn = getattr(widget, getter, None)
        if callable(fn):
            try:
                value = fn()
            except Exception:
                continue
            if value is not None:
                return str(value)
    return None


def _widget_checked(widget: Any) -> Optional[bool]:
    fn = getattr(widget, "isChecked", None)
    if not callable(fn):
        return None
    try:
        return bool(fn())
    except Exception:
        return None


def _widget_bounds(widget: Any) -> Optional[UiBounds]:
    try:
        rect = widget.geometry()
    except Exception:
        return None
    return UiBounds(
        x=float(rect.x()),
        y=float(rect.y()),
        width=float(rect.width()),
        height=float(rect.height()),
    )


def _resolve_root_window(app: Any) -> Any:
    candidates = []
    for widget in app.topLevelWidgets():
        try:
            if not widget.isVisible():
                continue
        except Exception:
            continue
        candidates.append(widget)
    if not candidates:
        return None
    for widget in candidates:
        try:
            title = str(widget.windowTitle() or "")
        except Exception:
            title = ""
        if "3ds max" in title.lower() or "autodesk" in title.lower():
            return widget
    return candidates[0]


def _scope_metadata(root: Any) -> Dict[str, Any]:
    title = ""
    try:
        title = str(root.windowTitle() or "")
    except Exception:
        title = ""
    return {"window_title": title, "process_id": os.getpid()}


def _window_allowed(state: Dict[str, Any], policy: AppUiPolicy) -> bool:
    title = str(state.get("window_title") or "").strip()
    process_id = int(state.get("process_id") or 0)
    if policy.require_scoped_window and not title and process_id <= 0:
        return False
    if policy.allowed_window_titles:
        lowered = title.lower()
        allowed = [str(item).lower() for item in policy.allowed_window_titles]
        if not any(item in lowered for item in allowed):
            return False
    if policy.allowed_process_ids and process_id not in policy.allowed_process_ids:
        return False
    return True


def _control_node(
    widget: Any,
    *,
    snapshot_id: str,
    children: Optional[List[UiControlNode]] = None,
) -> UiControlNode:
    summary = _widget_summary(widget)
    return UiControlNode(
        id=summary["widget_id"],
        role=_qt_role(widget),
        label=_widget_label(widget),
        text=_widget_text(widget),
        object_name=summary.get("object_name") or None,
        enabled=bool(summary.get("enabled")),
        visible=bool(summary.get("visible")),
        bounds=_widget_bounds(widget),
        value=_widget_text(widget),
        checked=_widget_checked(widget),
        children=children or [],
        metadata={"app_ui": {"backend": "qt", "snapshot_id": snapshot_id}},
    )


def _walk_widget_tree(
    widget: Any,
    *,
    snapshot_id: str,
    depth: int,
    max_depth: int,
    budget: List[int],
) -> UiControlNode:
    if budget[0] <= 0:
        return UiControlNode(
            id=_widget_id(widget),
            role=_qt_role(widget),
            label=_widget_label(widget),
            visible=False,
            metadata={"app_ui": {"backend": "qt", "truncated": True, "snapshot_id": snapshot_id}},
        )
    budget[0] -= 1
    children: List[UiControlNode] = []
    truncated = False
    if depth < max_depth:
        try:
            for child in widget.children():
                if not hasattr(child, "isWidgetType") or not child.isWidgetType():
                    continue
                if budget[0] <= 0:
                    truncated = True
                    break
                children.append(
                    _walk_widget_tree(
                        child,
                        snapshot_id=snapshot_id,
                        depth=depth + 1,
                        max_depth=max_depth,
                        budget=budget,
                    )
                )
        except Exception:
            children = []
    node = _control_node(widget, snapshot_id=snapshot_id, children=children)
    if truncated:
        node.metadata.setdefault("app_ui", {})["truncated"] = True
    return node


def _build_snapshot(
    session_id: str, *, max_depth: int = _DEFAULT_MAX_DEPTH, max_nodes: int = _DEFAULT_MAX_NODES
) -> Dict[str, Any]:
    try:
        binding = _load_qt()
    except _BindingUnavailable as exc:
        return {"error": _binding_unavailable(exc)}
    app = binding.widgets.QApplication.instance()
    if app is None:
        return {"error": _no_application()}
    root_widget = _resolve_root_window(app)
    if root_widget is None:
        return {
            "error": skill_error(
                "No visible Qt top-level window found in 3ds Max.",
                UiErrorCode.MISSING_WINDOW,
                error_code=UiErrorCode.MISSING_WINDOW,
            )
        }
    state = _session_state(session_id)
    scope = _scope_metadata(root_widget)
    state.update(scope)
    snapshot_id = _snapshot_token(state)
    budget = [max(1, min(int(max_nodes), 4096))]
    root_node = _walk_widget_tree(
        root_widget,
        snapshot_id=snapshot_id,
        depth=0,
        max_depth=max(0, min(int(max_depth), 16)),
        budget=budget,
    )
    focus_id = None
    try:
        focused = app.focusWidget()
        if focused is not None:
            focus_id = _widget_id(focused)
    except Exception:
        focus_id = None
    node_count = 1 + sum(1 for _ in _iter_nodes(root_node.to_dict()))
    snapshot = UiSnapshot(
        root=root_node,
        session_id=session_id,
        focus_id=focus_id,
        truncated=budget[0] <= 0 or bool(root_node.metadata.get("app_ui", {}).get("truncated")),
        node_count=node_count,
        metadata={
            "snapshot_id": snapshot_id,
            "app_ui": {
                "backend": "qt",
                "binding": binding.name,
                "window_title": scope["window_title"],
                "process_id": scope["process_id"],
            },
        },
    ).to_dict()
    state["snapshot"] = snapshot
    return {"snapshot": snapshot, "binding": binding.name}


def _iter_nodes(node: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    yield node
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from _iter_nodes(child)


def _find_controls(snapshot: Dict[str, Any], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    query = str(params.get("query") or "").lower()
    role = str(params.get("role") or "").lower()
    label = str(params.get("label") or "").lower()
    object_name = str(params.get("object_name") or "").lower()
    limit = max(1, int(params.get("limit") or 10))
    matches: List[Dict[str, Any]] = []
    for node in _iter_nodes(snapshot["root"]):
        if role and str(node.get("role") or "").lower() != role:
            continue
        if label and label not in str(node.get("label") or "").lower():
            continue
        if object_name and object_name not in str(node.get("object_name") or "").lower():
            continue
        if query:
            haystack = " ".join(
                str(node.get(key) or "") for key in ("id", "label", "text", "value", "object_name", "role")
            ).lower()
            if query not in haystack:
                continue
        matches.append(node)
        if len(matches) >= limit:
            break
    return matches


def _find_by_control_id(snapshot: Dict[str, Any], control_id: str) -> Optional[Dict[str, Any]]:
    for node in _iter_nodes(snapshot["root"]):
        if node.get("id") == control_id:
            return node
    return None


def _audit_record(
    action: str,
    success: bool,
    control: Optional[Dict[str, Any]],
    state: Dict[str, Any],
    policy: AppUiPolicy,
    *,
    error_code: Optional[str] = None,
    message: Optional[str] = None,
    before_focus_id: Optional[str] = None,
    after_focus_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    redacted: List[str] = []
    if action == UiActionKind.SET_TEXT and not policy.audit_sensitive_values:
        redacted.append("text")
    audit_metadata = {"backend": "qt", "snapshot_id": _snapshot_token(state)}
    if metadata:
        audit_metadata.update(metadata)
    return AppUiAuditRecord(
        action_kind=action,
        success=success,
        target_control_id=control.get("id") if control else None,
        target_role=control.get("role") if control else None,
        target_label=control.get("label") if control else None,
        before_focus_id=before_focus_id if before_focus_id is not None else state.get("focus_id"),
        after_focus_id=after_focus_id if after_focus_id is not None else state.get("focus_id"),
        error_code=error_code,
        message=message,
        session_id=state.get("session_id"),
        redacted_fields=redacted,
        metadata=audit_metadata,
    ).to_dict()


def _policy_denied(
    action: str,
    control_id: str,
    control: Optional[Dict[str, Any]],
    state: Dict[str, Any],
    policy: AppUiPolicy,
    message: str,
) -> Dict[str, Any]:
    result = UiActionResult(
        success=False,
        control_id=control_id,
        error_code=UiErrorCode.POLICY_DISABLED,
        message=message,
        before_focus_id=state.get("focus_id"),
        after_focus_id=state.get("focus_id"),
    ).to_dict()
    audit = _audit_record(
        action, False, control, state, policy, error_code=UiErrorCode.POLICY_DISABLED, message=message
    )
    return skill_error(message, UiErrorCode.POLICY_DISABLED, result=result, audit=audit)


def _unsupported_action(
    action: str,
    control: Dict[str, Any],
    state: Dict[str, Any],
    policy: AppUiPolicy,
    message: str,
) -> Dict[str, Any]:
    result = UiActionResult(
        success=False,
        control_id=str(control.get("id") or ""),
        error_code=UiErrorCode.UNSUPPORTED_ACTION,
        message=message,
        before_focus_id=state.get("focus_id"),
        after_focus_id=state.get("focus_id"),
    ).to_dict()
    audit = _audit_record(
        action,
        False,
        control,
        state,
        policy,
        error_code=UiErrorCode.UNSUPPORTED_ACTION,
        message=message,
    )
    return skill_error(message, UiErrorCode.UNSUPPORTED_ACTION, result=result, audit=audit)


def _perform_qt_action(widget: Any, action: str, params: Dict[str, Any]) -> Optional[str]:
    role = _qt_role(widget)
    if action == UiActionKind.FOCUS:
        widget.setFocus()
        return None
    if action == UiActionKind.SET_TEXT:
        if role != "text_field":
            return "set_text requires a text_field control"
        setter = getattr(widget, "setText", None)
        if not callable(setter):
            return "set_text is unsupported for this control"
        setter(str(params.get("text") or ""))
        return None
    if action in (UiActionKind.TOGGLE, UiActionKind.SET_CHECKED):
        if role != "checkbox":
            return f"{action} requires a checkbox control"
        if action == UiActionKind.SET_CHECKED:
            widget.setChecked(bool(params.get("checked")))
        else:
            widget.setChecked(not bool(widget.isChecked()))
        return None
    if action == UiActionKind.SELECT_OPTION:
        if role != "combo_box":
            return "select_option requires a combo_box control"
        option = str(params.get("option") or params.get("text") or "")
        if hasattr(widget, "setCurrentText"):
            widget.setCurrentText(option)
            return None
        return "select_option is unsupported for this combo_box"
    if action == UiActionKind.CLICK:
        click = getattr(widget, "click", None)
        if callable(click):
            click()
            return None
        if role in ("button", "checkbox"):
            toggle = getattr(widget, "toggle", None)
            if callable(toggle):
                toggle()
                return None
        return "click is unsupported for this control role"
    if action == UiActionKind.RAW_COORDINATE_CLICK:
        return "raw_coordinate_click requires allow_raw_coordinates policy"
    if action == UiActionKind.KEYBOARD_SHORTCUT:
        return "keyboard_shortcut requires allow_keyboard_shortcuts policy"
    return f"unsupported app_ui action {action!r}"


def app_ui_snapshot(**kwargs: Any) -> Dict[str, Any]:
    params = dict(kwargs)
    session_id = _safe_session_id(params.get("session_id"))
    policy = _policy_from_params(params)
    if not policy.allow_snapshot:
        return skill_error(
            "app_ui snapshot disabled by policy",
            UiErrorCode.POLICY_DISABLED,
            error_code=UiErrorCode.POLICY_DISABLED,
        )
    built = _build_snapshot(session_id)
    if "error" in built:
        return built["error"]
    state = _session_state(session_id)
    if not _window_allowed(state, policy):
        return skill_error(
            "scoped app_ui window is not allowed by policy",
            UiErrorCode.MISSING_WINDOW,
            error_code=UiErrorCode.MISSING_WINDOW,
        )
    snapshot = built["snapshot"]
    return skill_success(
        "Captured Qt app_ui snapshot from 3ds Max.",
        prompt="Use app_ui__find to resolve a control, then app_ui__act with the returned snapshot_id.",
        session_id=session_id,
        snapshot_id=snapshot["metadata"]["snapshot_id"],
        snapshot=snapshot,
        policy=policy.to_dict(),
        binding=built.get("binding"),
    )


def app_ui_find(**kwargs: Any) -> Dict[str, Any]:
    params = dict(kwargs)
    session_id = _safe_session_id(params.get("session_id"))
    policy = _policy_from_params(params)
    if not policy.allow_find:
        return skill_error(
            "app_ui find disabled by policy",
            UiErrorCode.POLICY_DISABLED,
            error_code=UiErrorCode.POLICY_DISABLED,
        )
    state = _session_state(session_id)
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, dict):
        built = _build_snapshot(session_id)
        if "error" in built:
            return built["error"]
        snapshot = built["snapshot"]
    if not _window_allowed(state, policy):
        return skill_error(
            "scoped app_ui window is not allowed by policy",
            UiErrorCode.MISSING_WINDOW,
            error_code=UiErrorCode.MISSING_WINDOW,
        )
    matches = _find_controls(snapshot, params)
    return skill_success(
        f"Found {len(matches)} Qt app_ui control(s).",
        prompt="Use app_ui__act with a returned control id, then app_ui__wait_for.",
        session_id=session_id,
        snapshot_id=snapshot["metadata"]["snapshot_id"],
        matches=matches,
        count=len(matches),
    )


def app_ui_act(**kwargs: Any) -> Dict[str, Any]:
    params = dict(kwargs)
    session_id = _safe_session_id(params.get("session_id"))
    state = _session_state(session_id)
    policy = _policy_from_params(params)
    control_id = str(params.get("control_id") or "")
    action = str(params.get("action") or "")
    requested_snapshot_id = str(params.get("snapshot_id") or "")
    current_snapshot_id = _snapshot_token(state)
    if requested_snapshot_id and requested_snapshot_id != current_snapshot_id:
        result = UiActionResult.stale(control_id).to_dict()
        result["metadata"] = {
            "requested_snapshot_id": requested_snapshot_id,
            "current_snapshot_id": current_snapshot_id,
        }
        audit = _audit_record(
            action,
            False,
            {"id": control_id},
            state,
            policy,
            error_code=UiErrorCode.STALE_CONTROL,
            message="control is stale; refresh the UI snapshot",
            metadata=result["metadata"],
        )
        return skill_error(
            "Control is stale; refresh the app_ui snapshot.",
            UiErrorCode.STALE_CONTROL,
            result=result,
            audit=audit,
            current_snapshot_id=current_snapshot_id,
        )
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, dict):
        built = _build_snapshot(session_id)
        if "error" in built:
            return built["error"]
        snapshot = built["snapshot"]
    control = _find_by_control_id(snapshot, control_id)
    if not control:
        message = "control not found in scoped app_ui window"
        result = UiActionResult(
            success=False,
            control_id=control_id,
            error_code=UiErrorCode.NOT_FOUND,
            message=message,
            before_focus_id=state.get("focus_id"),
            after_focus_id=state.get("focus_id"),
        ).to_dict()
        audit = _audit_record(action, False, None, state, policy, error_code=UiErrorCode.NOT_FOUND, message=message)
        return skill_error(
            "Control not found in scoped app_ui window.",
            UiErrorCode.NOT_FOUND,
            result=result,
            audit=audit,
            current_snapshot_id=current_snapshot_id,
        )
    if not _window_allowed(state, policy):
        return _policy_denied(
            action, control_id, control, state, policy, "scoped app_ui window is not allowed by policy"
        )
    if not policy.allows_action(action):
        return _policy_denied(
            action, control_id, control, state, policy, f"app_ui action {action!r} disabled by policy"
        )

    try:
        binding = _load_qt()
    except _BindingUnavailable as exc:
        return _binding_unavailable(exc)
    app = binding.widgets.QApplication.instance()
    if app is None:
        return _no_application()
    widget = _find_by_id(app, control_id)
    if widget is None:
        return _unsupported_action(action, control, state, policy, "control widget is no longer available")

    before_focus = state.get("focus_id")
    try:
        focus_widget = app.focusWidget()
        if focus_widget is not None:
            before_focus = _widget_id(focus_widget)
    except Exception:
        before_focus = state.get("focus_id")

    message = _perform_qt_action(widget, action, params)
    if message:
        return _unsupported_action(action, control, state, policy, message)

    state["revision"] = int(state.get("revision") or 0) + 1
    rebuilt = _build_snapshot(session_id)
    if "error" not in rebuilt:
        snapshot = rebuilt["snapshot"]
    after_focus = snapshot.get("focus_id")
    result = UiActionResult(
        success=True,
        control_id=control_id,
        message="app_ui action completed",
        before_focus_id=before_focus,
        after_focus_id=after_focus,
        metadata={"snapshot_id": _snapshot_token(state)},
    ).to_dict()
    audit = _audit_record(
        action, True, control, state, policy, None, "app_ui action completed", before_focus_id=before_focus
    )
    return skill_success(
        f"Completed Qt app_ui action {action!r} on {control_id}.",
        prompt="Use app_ui__wait_for to poll for the expected UI state, then app_ui__snapshot to verify.",
        session_id=session_id,
        snapshot_id=_snapshot_token(state),
        result=result,
        audit=audit,
    )


def _condition_from_params(raw: Dict[str, Any]) -> UiWaitCondition:
    data = {key: raw[key] for key in _CONDITION_KEYS if key in raw}
    data.setdefault("kind", UiWaitConditionKind.CONTROL_EXISTS)
    return UiWaitCondition(**data)


def _resolve_condition_control(snapshot: Dict[str, Any], condition: UiWaitCondition) -> Optional[Dict[str, Any]]:
    if condition.control_id:
        return _find_by_control_id(snapshot, condition.control_id)
    matches = _find_controls(snapshot, condition.to_dict())
    return matches[0] if matches else None


def _condition_matches(snapshot: Dict[str, Any], condition: UiWaitCondition) -> bool:
    control = _resolve_condition_control(snapshot, condition)
    if condition.kind == UiWaitConditionKind.CONTROL_MISSING:
        return control is None
    if control is None:
        return False
    if condition.kind == UiWaitConditionKind.CONTROL_EXISTS:
        return True
    if condition.kind == UiWaitConditionKind.TEXT_EQUALS:
        return str(control.get("text") or "") == str(condition.text or "")
    if condition.kind == UiWaitConditionKind.VALUE_EQUALS:
        return str(control.get("value") or "") == str(condition.value or "")
    if condition.kind == UiWaitConditionKind.CHECKED_EQUALS:
        return bool(control.get("checked")) is bool(condition.checked)
    if condition.kind == UiWaitConditionKind.ENABLED:
        return bool(control.get("enabled"))
    if condition.kind == UiWaitConditionKind.DISABLED:
        return not bool(control.get("enabled"))
    if condition.kind == UiWaitConditionKind.FOCUSED:
        return snapshot.get("focus_id") == control.get("id")
    return False


def app_ui_wait_for(**kwargs: Any) -> Dict[str, Any]:
    params = dict(kwargs)
    session_id = _safe_session_id(params.get("session_id"))
    policy = _policy_from_params(params)
    condition = _condition_from_params(params.get("condition") or {})
    timeout_ms = max(0, int(condition.timeout_ms))
    interval_ms = max(10, int(condition.interval_ms))
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    attempts = 0
    last_snapshot = None
    start = time.monotonic()
    state = _session_state(session_id)
    while True:
        if not _window_allowed(state, policy):
            elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
            message = "scoped app_ui window is not allowed by policy"
            result = UiWaitResult(
                success=False,
                condition=condition,
                elapsed_ms=elapsed_ms,
                attempts=attempts,
                snapshot=None,
                error_code=UiErrorCode.POLICY_DISABLED,
                message=message,
            ).to_dict()
            audit = _audit_record(
                "wait_for", False, None, state, policy, error_code=UiErrorCode.POLICY_DISABLED, message=message
            )
            return skill_error(message, UiErrorCode.POLICY_DISABLED, session_id=session_id, result=result, audit=audit)
        built = _build_snapshot(session_id)
        if "error" in built:
            return built["error"]
        last_snapshot = built["snapshot"]
        attempts += 1
        if _condition_matches(last_snapshot, condition):
            elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
            control = _resolve_condition_control(last_snapshot, condition)
            result = UiWaitResult(
                success=True,
                condition=condition,
                elapsed_ms=elapsed_ms,
                attempts=attempts,
                snapshot=None,
                message="condition became true",
                metadata={"last_snapshot": last_snapshot},
            ).to_dict()
            return skill_success(
                "app_ui wait condition satisfied.",
                session_id=session_id,
                snapshot_id=last_snapshot["metadata"]["snapshot_id"],
                result=result,
                audit=_audit_record(
                    "wait_for",
                    True,
                    control,
                    state,
                    policy,
                    None,
                    "condition became true",
                    metadata={"condition": condition.to_dict()},
                ),
            )
        if time.monotonic() >= deadline:
            break
        time.sleep(min(interval_ms / 1000.0, max(0.0, deadline - time.monotonic())))

    elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
    result = UiWaitResult(
        success=False,
        condition=condition,
        elapsed_ms=elapsed_ms,
        attempts=attempts,
        snapshot=None,
        error_code=UiErrorCode.TIMEOUT,
        message="condition did not become true before timeout",
        metadata={"last_snapshot": last_snapshot},
    ).to_dict()
    control = _resolve_condition_control(last_snapshot, condition) if last_snapshot else None
    audit = _audit_record(
        "wait_for",
        False,
        control,
        state,
        policy,
        error_code=UiErrorCode.TIMEOUT,
        message="condition did not become true before timeout",
        metadata={"condition": condition.to_dict()},
    )
    return skill_error(
        "app_ui wait_for timed out.",
        UiErrorCode.TIMEOUT,
        session_id=session_id,
        result=result,
        audit=audit,
        attempts=attempts,
    )


_SNAPSHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "policy": {"type": "object"},
    },
}

_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "query": {"type": "string"},
        "role": {"type": "string"},
        "label": {"type": "string"},
        "object_name": {"type": "string"},
        "limit": {"type": "integer", "default": 10},
        "policy": {"type": "object"},
    },
}

_ACT_SCHEMA = {
    "type": "object",
    "required": ["control_id", "action"],
    "properties": {
        "session_id": {"type": "string"},
        "control_id": {"type": "string"},
        "action": {
            "type": "string",
            "enum": [
                "click",
                "set_text",
                "toggle",
                "set_checked",
                "select_option",
                "focus",
                "raw_coordinate_click",
                "keyboard_shortcut",
            ],
        },
        "text": {"type": "string"},
        "checked": {"type": "boolean"},
        "option": {"type": "string"},
        "snapshot_id": {"type": "string"},
        "policy": {"type": "object"},
    },
}

_WAIT_FOR_SCHEMA = {
    "type": "object",
    "required": ["condition"],
    "properties": {
        "session_id": {"type": "string"},
        "condition": {
            "type": "object",
            "required": ["kind"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "control_exists",
                        "control_missing",
                        "text_equals",
                        "value_equals",
                        "checked_equals",
                        "enabled",
                        "disabled",
                        "focused",
                    ],
                },
                "control_id": {"type": "string"},
                "query": {"type": "string"},
                "role": {"type": "string"},
                "label": {"type": "string"},
                "text": {"type": "string"},
                "value": {"type": "string"},
                "checked": {"type": "boolean"},
                "timeout_ms": {"type": "integer", "default": 5000},
                "interval_ms": {"type": "integer", "default": 100},
            },
        },
        "policy": {"type": "object"},
    },
}


def register_3dsmax_app_ui(
    inner_server: Any,
    *,
    dcc_name: str = "3dsmax",
    dispatcher: Any = None,
) -> bool:
    """Register the four ``app_ui__*`` tools on the inner MCP server."""
    if not resolve_app_ui_enabled():
        logger.info("[%s] app-ui disabled via %s", dcc_name, ENV_APP_UI)
        return False

    proxy = _MainThreadHandlerProxy(inner_server, dispatcher)

    def _handler(fn: Callable[..., Dict[str, Any]]) -> Callable[[Any], Any]:
        def wrapper(params: Any) -> Any:
            args = _parse_params(params)
            return fn(**args)

        return wrapper

    specs = [
        ToolSpec(
            name="app_ui__snapshot",
            description="Capture a bounded Qt app_ui snapshot from the scoped 3ds Max window.",
            input_schema=_SNAPSHOT_SCHEMA,
            handler=_handler(app_ui_snapshot),
            category=_CATEGORY_APP_UI,
            tags=["app-ui", "qt", "3dsmax"],
        ),
        ToolSpec(
            name="app_ui__find",
            description="Locate Qt controls in the scoped app_ui snapshot by query, role, label, or object name.",
            input_schema=_FIND_SCHEMA,
            handler=_handler(app_ui_find),
            category=_CATEGORY_APP_UI,
            tags=["app-ui", "qt", "3dsmax"],
        ),
        ToolSpec(
            name="app_ui__act",
            description=(
                "Perform one scoped Qt app_ui action. Safe actions include focus, click, toggle, "
                "set_checked, set_text, and select_option. Raw coordinates stay denied by default policy."
            ),
            input_schema=_ACT_SCHEMA,
            handler=_handler(app_ui_act),
            category=_CATEGORY_APP_UI,
            tags=["app-ui", "qt", "3dsmax"],
        ),
        ToolSpec(
            name="app_ui__wait_for",
            description="Poll the scoped Qt UI until a condition becomes true or times out.",
            input_schema=_WAIT_FOR_SCHEMA,
            handler=_handler(app_ui_wait_for),
            category=_CATEGORY_APP_UI,
            tags=["app-ui", "qt", "3dsmax"],
        ),
    ]
    try:
        register_tools(proxy, specs, dcc_name=dcc_name, log_prefix="register_3dsmax_app_ui", logger=logger)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] app-ui registration failed: %s", dcc_name, exc)
        return False
    logger.info("[%s] app-ui tools registered (Qt backend, main-thread routed)", dcc_name)
    return True


__all__ = [
    "ENV_APP_UI",
    "app_ui_act",
    "app_ui_find",
    "app_ui_snapshot",
    "app_ui_wait_for",
    "register_3dsmax_app_ui",
    "resolve_app_ui_enabled",
]
