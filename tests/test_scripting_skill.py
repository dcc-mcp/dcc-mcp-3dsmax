"""Tests for the bundled 3ds Max scripting skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scripting"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNode:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle


class _FakeMacros:
    def list(self):
        return [{"category": "DCC", "name": "SampleAction"}]


class _FakeRuntime:
    def __init__(self) -> None:
        self.objects = [_FakeNode("hero_box", 42)]
        self.macros = _FakeMacros()
        self.executed_script = None

    def execute(self, script):
        self.executed_script = script
        return {"script": script, "ok": True}

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def sampleCallable(self, value=1):  # noqa: N802 - mirrors pymxs runtime naming.
        """Sample callable for signature inspection."""
        return value


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_execute_python_requires_confirmation(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    action = _load_action("action_execute_python.py")

    result = action.main("result = 1", confirm_execution=False)

    assert result["status"] == "error"
    assert "confirm_execution" in result["message"]


def test_execute_python_returns_stdout_and_result(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    action = _load_action("action_execute_python.py")

    result = action.main(
        "print('hello from max')\nresult = {'node_count': len(rt.objects)}",
        confirm_execution=True,
    )

    assert result["success"] is True
    assert result["data"]["stdout"] == "hello from max\n"
    assert result["data"]["stderr"] == ""
    assert result["data"]["result"] == {"node_count": 1}


def test_execute_python_resolves_top_level_functions_in_shared_namespace(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    action = _load_action("action_execute_python.py")

    result = action.main(
        "def answer():\n"
        "    return 42\n"
        "def call_answer():\n"
        "    return answer()\n"
        "result = call_answer()",
        confirm_execution=True,
    )

    assert result["success"] is True
    assert result["data"]["result"] == 42


def test_execute_python_returns_error_envelope(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    action = _load_action("action_execute_python.py")

    result = action.main("raise RuntimeError('boom')", confirm_execution=True)

    assert result["success"] is False
    assert result["data"]["exception_type"] == "RuntimeError"
    assert "boom" in result["data"]["error"]


def test_execute_maxscript_delegates_to_runtime_execute(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    action = _load_action("action_execute_maxscript.py")

    result = action.main("selection.count", confirm_execution=True)

    assert result["success"] is True
    assert runtime.executed_script == "selection.count"
    assert result["data"]["result"] == {"script": "selection.count", "ok": True}


def test_runtime_introspection_tools(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    symbols = _load_action("action_list_runtime_symbols.py").main(prefix="sample")
    inspected = _load_action("action_inspect_runtime_symbol.py").main("sampleCallable")
    macros = _load_action("action_list_macros.py").main()

    assert symbols["success"] is True
    assert symbols["data"]["symbols"][0]["name"] == "sampleCallable"
    assert inspected["success"] is True
    assert inspected["data"]["callable"] is True
    assert macros["success"] is True
    assert macros["data"]["macros"] == [{"category": "DCC", "name": "SampleAction"}]


def test_resolve_node_reference_by_name_and_handle(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    action = _load_action("action_resolve_node_reference.py")

    by_name = action.main(node_name="hero_box")
    by_handle = action.main(handle=42)

    assert by_name["success"] is True
    assert by_name["data"]["node"]["node_name"] == "hero_box"
    assert by_handle["success"] is True
    assert by_handle["data"]["node"]["object_id"] == 42


def test_reload_adapter_module_rejects_external_modules():
    action = _load_action("action_reload_adapter_module.py")

    result = action.main("os")

    assert result["status"] == "error"
    assert "adapter-owned" in result["message"]
