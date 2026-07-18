"""Unit tests for the Qt-backed 3ds Max app_ui integration."""

from __future__ import annotations

from dcc_mcp_core.adapter_contracts import AppUiPolicy, UiActionKind

from dcc_mcp_3dsmax import _app_ui as app_ui_mod


class _FakeInnerServer:
    def __init__(self) -> None:
        self.registry = _FakeRegistry()
        self.handlers = {}

    def register_handler(self, name, handler):
        self.handlers[name] = handler
        return handler


class _FakeRegistry:
    def __init__(self) -> None:
        self.registered = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


def test_app_ui_disabled_via_env(monkeypatch):
    monkeypatch.setenv(app_ui_mod.ENV_APP_UI, "0")
    assert app_ui_mod.register_3dsmax_app_ui(_FakeInnerServer()) is False


def test_app_ui_registers_when_enabled(monkeypatch):
    monkeypatch.setenv(app_ui_mod.ENV_APP_UI, "1")
    server = _FakeInnerServer()
    result = app_ui_mod.register_3dsmax_app_ui(server, dispatcher=None)
    assert result is True
    names = {item["name"] for item in server.registry.registered}
    assert names == {
        "app_ui__snapshot",
        "app_ui__find",
        "app_ui__act",
        "app_ui__wait_for",
    }


def test_default_policy_blocks_raw_coordinate_click():
    policy = AppUiPolicy()
    assert policy.allows_action(UiActionKind.CLICK) is True
    assert policy.allows_action(UiActionKind.FOCUS) is True
    assert policy.allows_action(UiActionKind.RAW_COORDINATE_CLICK) is False
    assert policy.allows_action(UiActionKind.KEYBOARD_SHORTCUT) is False


def test_find_controls_matches_query_and_role():
    snapshot = {
        "root": {
            "id": "window",
            "role": "window",
            "label": "DCC MCP",
            "children": [
                {
                    "id": "menu-open",
                    "role": "button",
                    "label": "Open MCP Status",
                    "object_name": "mcpStatusButton",
                    "text": "Open MCP Status",
                },
                {
                    "id": "close",
                    "role": "button",
                    "label": "Close",
                    "object_name": "closeButton",
                    "text": "Close",
                },
            ],
        }
    }
    matches = app_ui_mod._find_controls(snapshot, {"query": "mcp", "role": "button", "limit": 5})
    assert len(matches) == 1
    assert matches[0]["id"] == "menu-open"


def test_stale_snapshot_id_is_detected_in_act(monkeypatch):
    monkeypatch.setattr(app_ui_mod, "_load_qt", lambda: (_ for _ in ()).throw(AssertionError("should not load Qt")))
    app_ui_mod._SESSIONS.clear()
    session_id = "stale-test"
    state = app_ui_mod._session_state(session_id)
    state["revision"] = 3
    state["snapshot"] = {
        "root": {"id": "btn", "role": "button", "label": "Apply"},
        "metadata": {"snapshot_id": app_ui_mod._snapshot_token(state)},
        "focus_id": None,
    }
    result = app_ui_mod.app_ui_act(
        session_id=session_id,
        control_id="btn",
        action="click",
        snapshot_id="stale-test:1",
    )
    assert result["success"] is False
    assert result["error"] == "stale_control"
