"""Offline tests for render completion signals and the V-Ray IPR control.

The doubles model a host callback system. Every assertion checks that a
rejected registration, an unsupported action, an unverifiable state, or an
expired wait is reported instead of looking like a finished render.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _render_signals as signals  # noqa: E402
from dcc_mcp_3dsmax import _viewport_utils as viewport_utils  # noqa: E402

RENDER_SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-render"

# 3ds Max only runs on Windows, so paths carry backslashes. Built from chr(92)
# to keep the literals in this file unambiguous.
BS = chr(92)


def _load_action(script_name: str):
    path = RENDER_SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_signal_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Callbacks:
    """Host callback system double with a readable registration table."""

    def __init__(self, *, raise_on_add: bool = False, raise_on_remove: bool = False) -> None:
        self._scripts: Dict[str, str] = {}
        self.added: List[str] = []
        self.removed: List[str] = []
        self.raise_on_add = raise_on_add
        self.raise_on_remove = raise_on_remove

    @property
    def scripts(self) -> Dict[str, str]:
        return self._scripts

    def addScript(self, symbol: Any, script: str, id: Optional[str] = None) -> Any:  # noqa: N802, A002
        if self.raise_on_add:
            raise RuntimeError("callback system is locked")
        key = id if id is not None else symbol
        self._scripts[key] = script
        self.added.append(key)
        return True

    def removeScripts(self, id: Optional[str] = None) -> Any:  # noqa: N802, A002
        if self.raise_on_remove:
            raise RuntimeError("callback system is locked")
        self._scripts.pop(id, None)
        self.removed.append(id)
        return True

    def getScript(self, id: str) -> Any:  # noqa: N802, A002
        return self._scripts.get(id)


class _SilentCallbacks(_Callbacks):
    """A host that takes registrations but reports nothing back."""

    @property
    def scripts(self) -> Dict[str, str]:
        raise AttributeError("scripts")

    def getScript(self, id: str) -> Any:  # noqa: N802, A002
        raise AttributeError("getScript")


class _Renderer:
    def __init__(self, name: str = "VRayRenderer", *, ipr_running: Any = None) -> None:
        self.IPRRunning = ipr_running
        self.started: List[str] = []
        self.stopped: List[str] = []
        self.refreshed: List[str] = []

    def startIPR(self) -> None:  # noqa: N802 - mirrors pymxs naming.
        self.started.append("start")
        self.IPRRunning = True

    def stopIPR(self) -> None:  # noqa: N802 - mirrors pymxs naming.
        self.stopped.append("stop")
        self.IPRRunning = False

    def refreshIPR(self) -> None:  # noqa: N802 - mirrors pymxs naming.
        self.refreshed.append("refresh")


class _Runtime:
    def __init__(self, *, callbacks: Any = None, renderer: Any = None) -> None:
        self.callbacks = callbacks
        self.renderer = renderer if renderer is not None else _Renderer()
        self.renderers = types.SimpleNamespace(current=self.renderer)

    def name(self, value):  # noqa: N802 - mirrors pymxs naming.
        return value

    def isProperty(self, node, attribute):  # noqa: N802 - mirrors pymxs naming.
        return hasattr(node, attribute)


def _install_fake_pymxs(monkeypatch, runtime: _Runtime) -> _Runtime:
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime, byref=lambda value: value))
    return runtime


@pytest.fixture(autouse=True)
def _reset_state():
    signals._ARMED_SIGNALS.clear()
    viewport_utils.reset_agent_viewports()
    yield
    signals._ARMED_SIGNALS.clear()
    viewport_utils.reset_agent_viewports()


# ---------------------------------------------------------------------------
# render_automations
# ---------------------------------------------------------------------------


def test_render_automations_arms_and_reports_completion(monkeypatch, tmp_path):
    callbacks = _Callbacks()
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(callbacks=callbacks))
    signal_file = tmp_path / "signal.json"

    armed = _load_action("action_render_automations.py").main(
        actions=["log", "notify"], wait=False, signal_file=str(signal_file), message="render done"
    )

    assert armed["success"] is True
    assert armed["data"]["armed"] is True
    assert armed["data"]["registration"] == "registered"
    assert callbacks.added
    signal_id = armed["data"]["signal_id"]
    assert signal_id in callbacks.scripts
    assert str(signal_file) in callbacks.scripts[signal_id]
    assert not signal_file.exists()

    # The host runs the registered script when the render finishes; the sleep
    # hook stands in for the render taking time.
    record = {"completed": True, "signal_id": signal_id, "time": "now", "message": "render done"}

    def render_then_wait(interval):
        signal_file.write_text(json.dumps(record), encoding="utf-8")

    finished = signals.render_automations(
        runtime,
        actions=["log"],
        wait=True,
        timeout_sec=1.0,
        signal_file=str(signal_file),
        label="batch",
        sleep=render_then_wait,
    )

    assert finished["success"] is True
    assert finished["data"]["completed"] is True
    assert finished["data"]["status"] == "completed"
    assert finished["data"]["record"]["signal_id"] == signal_id
    assert finished["data"]["cleanup"]["removed"] is True
    # Re-arming replaced the first signal, so both registrations are gone.
    assert signal_id in callbacks.removed
    assert finished["data"]["signal_id"] in callbacks.removed
    assert callbacks.scripts == {}
    assert signals.armed_signals() == {}


def test_render_automations_reports_unsupported_actions(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))

    result = _load_action("action_render_automations.py").main(
        actions=["log", "email_the_team"], wait=False, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert "Unsupported render automation action" in result["message"]
    assert "email_the_team" in result["message"]


def test_render_automations_reports_a_host_that_refuses_the_callback(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks(raise_on_add=True)))
    signal_file = tmp_path / "signal.json"

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=False, signal_file=str(signal_file)
    )

    assert result["success"] is False
    assert "Callback registration failed" in result["message"]
    assert not signal_file.exists()


def test_render_automations_reports_a_host_without_callbacks(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=None))

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert "No callback system" in result["message"]


def test_render_automations_reports_unverified_registration(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_SilentCallbacks()))

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is True
    assert result["data"]["registration"] == "unverified"
    assert any("unverified" in warning for warning in result["data"]["warnings"])


def test_render_automations_reports_a_timeout_without_claiming_completion(monkeypatch, tmp_path):
    callbacks = _Callbacks()
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=callbacks))
    signal_file = tmp_path / "signal.json"

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=True, timeout_sec=0.05, signal_file=str(signal_file)
    )

    assert result["success"] is False
    assert result["data"]["status"] == "timeout"
    assert result["data"]["armed"] is True
    assert result["data"]["completed"] is False


def test_render_automations_reports_a_corrupt_signal_file(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))
    signal_file = tmp_path / "signal.json"

    def render_then_wait(interval):
        signal_file.write_text("{not json", encoding="utf-8")

    result = signals.render_automations(
        runtime,
        actions=["log"],
        wait=True,
        timeout_sec=0.05,
        signal_file=str(signal_file),
        sleep=render_then_wait,
    )

    assert result["success"] is False
    assert "valid completion record" in result["message"]


def test_render_automations_disarms_and_validates_paths(monkeypatch, tmp_path):
    callbacks = _Callbacks()
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=callbacks))
    action = _load_action("action_render_automations.py")

    action.main(actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json"), label="nightly")
    result = action.main(disarm=True, label="nightly")

    assert result["success"] is True
    assert result["data"]["removed"]
    assert signals.armed_signals() == {}

    unknown = action.main(disarm=True, label="missing")
    assert unknown["success"] is False

    bad_dir = action.main(actions=["log"], wait=False, signal_file=str(tmp_path / "nope" / "signal.json"))
    assert bad_dir["success"] is False
    assert "directory does not exist" in bad_dir["message"]


def test_render_automations_save_output_requires_a_path(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))

    result = _load_action("action_render_automations.py").main(
        actions=["save_output"], wait=False, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert "save_output" in result["message"]


def _format_literal(script: str) -> str:
    """Return the MAXScript format string literal, without its delimiters."""
    line = next(line for line in script.splitlines() if line.startswith('format "') and "completed" in line)
    return line[len('format "') : line.rindex('" (localTime)')]


def _as_maxscript_writes_it(literal: str, timestamp: str = "2026-01-01 00:00:00") -> str:
    """Replay what MAXScript does: un-escape the literal, then fill in the

    single ``%`` placeholder from the argument list and collapse ``%%``.
    """
    out: list = []
    escapes = {"n": "\n", "t": "\t", "r": "\r"}
    index = 0
    while index < len(literal):
        char = literal[index]
        if char == "\\" and index + 1 < len(literal):
            out.append(escapes.get(literal[index + 1], literal[index + 1]))
            index += 2
        else:
            out.append(char)
            index += 1
    unescaped = "".join(out)
    return re.sub(r"(?<!%)%(?!%)", timestamp, unescaped).replace("%%", "%")


def test_signal_script_writes_a_json_record(tmp_path):
    target = tmp_path / "signal.json"
    script = signals.build_signal_script(
        target,
        signal_id="abc123",
        label="nightly",
        actions=["log", "notify"],
        message="done \"now\"",
        output_path="C:/out/frame.png",
    )

    assert str(target) in script
    assert "abc123" in script
    assert '\\"log\\", \\"notify\\"' in script
    assert '\\"completed\\": true' in script
    # A quote inside a value is JSON-escaped first and MAXScript-escaped second.
    assert 'done \\\\\\\"now\\\\\\\"' in script


def test_signal_script_escapes_every_quote_inside_the_format_string(tmp_path):
    """A bare quote would end the MAXScript literal and break the callback.

    This is the check that the generated script is syntactically usable: every
    quote inside the format string must be escaped, and the only literal
    percent signs are the ``localTime`` placeholder.
    """
    script = signals.build_signal_script(
        tmp_path / "signal.json",
        signal_id="id",
        label='lab"el',
        actions=["log", "notify", "save_output"],
        message='50% done "now"',
        output_path='C:/out "v2"/frame.png',
    )
    literal = _format_literal(script)

    assert '"' not in literal.replace('\\"', "")
    # One single "%" (the localTime placeholder); every other one is doubled.
    assert literal.replace("%%", "").count("%") == 1
    # A JSON quote is MAXScript-escaped once; a quote inside a value is
    # JSON-escaped first, so it carries both escapes.
    assert '\\"lab\\\\\\"el\\"' in literal
    assert '50%% done \\\\\\"now\\\\\\"' in literal
    assert '\\"actions\\": [\\"log\\", \\"notify\\", \\"save_output\\"]' in literal


def test_signal_script_keeps_the_json_parseable_after_maxscript_unescaping(tmp_path):
    """Un-escaping the literal the way MAXScript would must yield valid JSON."""
    script = signals.build_signal_script(
        tmp_path / "signal.json",
        signal_id="abc123",
        label="nightly render",
        actions=["log", "notify"],
        message="50% done",
        output_path="C:/out/frame.png",
    )
    literal = _format_literal(script)
    # MAXScript substitutes a lone "%" from the argument list and collapses
    # "%%" to a literal percent: replay that in the same order.
    unescaped = literal.replace('\\"', '"').replace("\\n", "\n")
    payload = re.sub(r"(?<!%)%(?!%)", "2026-01-01 00:00:00", unescaped).replace("%%", "%")

    record = json.loads(payload)
    assert record["completed"] is True
    assert record["actions"] == ["log", "notify"]
    assert record["message"] == "50% done"
    assert record["label"] == "nightly render"
    assert record["time"] == "2026-01-01 00:00:00"
    assert record["output_path"] == "C:/out/frame.png"


def test_signal_script_survives_windows_paths(monkeypatch, tmp_path):
    """Windows backslashes must reach the file as valid JSON.

    3ds Max only runs on Windows, so output paths carry backslashes. Escaping
    for MAXScript alone leaves single backslashes in the file, where the JSON
    parser rejects them -- and the wait loop then reports a timeout for a
    render that already finished.
    """
    signal_path = tmp_path / "signal.json"
    windows_paths = [
        "C:" + BS + "renders" + BS + "frame_0001.png",
        "D:" + BS + "shows" + BS + "demo" + BS + "v2" + BS + "final.exr",
        BS + BS + "server" + BS + "share" + BS + "beauty.png",
    ]
    for output_path in windows_paths:
        script = signals.build_signal_script(
            signal_path,
            signal_id="abc123",
            label="nightly",
            actions=["log", "save_output"],
            message="50% done",
            output_path=output_path,
        )
        literal = _format_literal(script)
        written = _as_maxscript_writes_it(literal)

        record = json.loads(written)
        assert record["output_path"] == output_path
        assert record["signal_id"] == "abc123"
        assert record["message"] == "50% done"
        assert record["actions"] == ["log", "save_output"]


def test_signal_script_survives_a_path_with_a_json_escape_sequence(tmp_path):
    r"""A path segment like
ew is a legal JSON escape, so it would corrupt
    the record silently instead of failing to parse."""
    script = signals.build_signal_script(
        tmp_path / "signal.json",
        signal_id="abc",
        label="nightly",
        actions=["log"],
        message="done",
        output_path="C:" + BS + "new" + BS + "table" + BS + "beauty.png",
    )
    written = _as_maxscript_writes_it(_format_literal(script))

    record = json.loads(written)
    assert record["output_path"] == "C:" + BS + "new" + BS + "table" + BS + "beauty.png"


def test_wait_for_signal_parses_a_record_written_with_windows_paths(monkeypatch, tmp_path):
    """The wait loop must accept the file the generated script produces."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))
    signal_file = tmp_path / "signal.json"
    script = signals.build_signal_script(
        signal_file,
        signal_id="abc123",
        label="nightly",
        actions=["log"],
        message="50% done",
        output_path="C:" + BS + "renders" + BS + "frame_0001.png",
    )
    written = _as_maxscript_writes_it(_format_literal(script))

    def render_then_wait(interval):
        signal_file.write_text(written, encoding="utf-8")

    result = signals.render_automations(
        runtime,
        actions=["log"],
        wait=True,
        timeout_sec=1.0,
        signal_file=str(signal_file),
        sleep=render_then_wait,
    )

    assert result["success"] is True
    assert result["data"]["status"] == "completed"
    assert result["data"]["record"]["output_path"] == "C:" + BS + "renders" + BS + "frame_0001.png"


def test_render_automations_reports_a_corrupt_signal_as_invalid_not_timeout(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))
    signal_file = tmp_path / "signal.json"

    def render_then_wait(interval):
        signal_file.write_text("{not json", encoding="utf-8")

    result = signals.render_automations(
        runtime,
        actions=["log"],
        wait=True,
        timeout_sec=0.05,
        signal_file=str(signal_file),
        sleep=render_then_wait,
    )

    assert result["success"] is False
    assert result["data"]["status"] == "invalid_signal"


def test_wait_is_opt_in(monkeypatch, tmp_path):
    """Waiting blocks the calling thread, so the tool arms by default."""
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))

    armed = _load_action("action_render_automations.py").main(
        actions=["log"], signal_file=str(tmp_path / "signal.json")
    )

    assert armed["success"] is True
    assert armed["data"]["armed"] is True
    assert "completed" not in armed["data"]


def test_wait_warns_that_it_blocks_the_calling_thread(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=True, timeout_sec=0.05, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert any("blocks the calling thread" in warning for warning in result["data"]["warnings"])


def test_callback_ids_are_wrapped_as_host_names(monkeypatch, tmp_path):
    """MAXScript takes the callback id as a name literal, not a bare string."""
    callbacks = _Callbacks()
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(callbacks=callbacks))
    runtime.name = lambda value: _Name(value)
    action = _load_action("action_render_automations.py")

    armed = action.main(actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json"))
    signal_id = armed["data"]["signal_id"]

    assert [str(key).lstrip("#") for key in callbacks.added] == [signal_id]
    assert all(isinstance(key, _Name) and key.value == signal_id for key in callbacks.scripts)

    disarmed = action.main(disarm=True)
    assert disarmed["success"] is True
    assert [str(key).lstrip("#") for key in callbacks.removed] == [signal_id]
    assert callbacks.scripts == {}


class _Name:
    """Stand-in for a pymxs name literal (``rt.name('postRender')``)."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, _Name):
            return other.value == self.value
        return str(other).lstrip("#") == self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return "#{}".format(self.value)


def test_render_automations_rejects_actions_passed_as_a_bare_string(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))

    result = _load_action("action_render_automations.py").main(
        actions="log", wait=False, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert "must be a list of strings" in result["message"]


# ---------------------------------------------------------------------------
# vray_ipr
# ---------------------------------------------------------------------------


def test_vray_ipr_start_is_verified_against_the_host_state(monkeypatch):
    renderer = _Renderer(ipr_running=False)
    _install_fake_pymxs(monkeypatch, _Runtime(renderer=renderer))

    result = _load_action("action_vray_ipr.py").main("start")

    assert result["success"] is True
    assert result["data"]["running"] is True
    assert result["data"]["contract"] == "renderer:startIPR"
    assert renderer.started == ["start"]

    stopped = _load_action("action_vray_ipr.py").main("stop")
    assert stopped["success"] is True
    assert stopped["data"]["running"] is False
    assert renderer.stopped == ["stop"]


def test_vray_ipr_reports_a_host_that_ignores_the_start(monkeypatch):
    """A start the host did not apply must fail, not report a running preview."""

    class _StubbornRenderer(_Renderer):
        def startIPR(self) -> None:  # noqa: N802 - mirrors pymxs naming.
            self.started.append("ignored")

    renderer = _StubbornRenderer(ipr_running=False)
    _install_fake_pymxs(monkeypatch, _Runtime(renderer=renderer))

    result = _load_action("action_vray_ipr.py").main("start")

    assert result["success"] is False
    assert "did not change the preview state" in result["message"]
    assert result["data"]["expected_running"] is True
    assert result["data"]["actual_running"] is False


def test_vray_ipr_reports_an_unverifiable_host(monkeypatch):
    class _NoStateRenderer:
        def __init__(self) -> None:
            self.started: List[str] = []

        def startIPR(self) -> None:  # noqa: N802 - mirrors pymxs naming.
            self.started.append("start")

    renderer = _NoStateRenderer()
    _install_fake_pymxs(monkeypatch, _Runtime(renderer=renderer))

    result = _load_action("action_vray_ipr.py").main("start")

    assert result["success"] is True
    assert result["data"]["running"] is None
    assert any("unverified" in warning for warning in result["data"]["warnings"])

    status = _load_action("action_vray_ipr.py").main("status")
    assert status["success"] is True
    assert any("no V-Ray IPR state contract" in warning for warning in status["data"]["warnings"])


def test_vray_ipr_reports_a_host_with_no_contract(monkeypatch):
    class _BareRenderer:
        pass

    _install_fake_pymxs(monkeypatch, _Runtime(renderer=_BareRenderer()))

    result = _load_action("action_vray_ipr.py").main("start")

    assert result["success"] is False
    assert "No V-Ray IPR start contract" in result["message"]
    assert result["data"]["probed"]


def test_vray_ipr_rejects_unsupported_actions(monkeypatch):
    _install_fake_pymxs(monkeypatch, _Runtime(renderer=_Renderer(ipr_running=True)))

    result = _load_action("action_vray_ipr.py").main("pause")

    assert result["success"] is False
    assert "Unsupported V-Ray IPR action" in result["message"]


def test_vray_ipr_refresh_keeps_the_preview_running(monkeypatch):
    renderer = _Renderer(ipr_running=True)
    _install_fake_pymxs(monkeypatch, _Runtime(renderer=renderer))

    result = _load_action("action_vray_ipr.py").main("refresh")

    assert result["success"] is True
    assert renderer.refreshed == ["refresh"]
    assert result["data"]["running"] is True


# ---------------------------------------------------------------------------
# Remaining failure paths
# ---------------------------------------------------------------------------


class _DroppingCallbacks(_Callbacks):
    """A host that takes the registration and then reports nothing for it."""

    def addScript(self, symbol: Any, script: str, id: Optional[str] = None) -> Any:  # noqa: N802, A002
        self.added.append(str(id))
        return True

    def getScript(self, id: str) -> Any:  # noqa: N802, A002
        return None


class _ReadOnlyCallbacks:
    """A host with a readable table but no way to register."""

    def __init__(self) -> None:
        self.scripts: Dict[str, str] = {}


def test_render_automations_reports_a_dropped_registration(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_DroppingCallbacks()))

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert "does not report the callback back" in result["message"]
    assert signals.armed_signals() == {}


def test_render_automations_reports_a_host_with_no_registration_method(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_ReadOnlyCallbacks()))

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert "no callback registration method" in result["message"]


def test_render_automations_removes_a_stale_signal_file(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))
    signal_file = tmp_path / "signal.json"
    signal_file.write_text('{"completed": true}', encoding="utf-8")

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=False, signal_file=str(signal_file)
    )

    assert result["success"] is True
    assert not signal_file.exists()
    assert any("stale signal file" in warning for warning in result["data"]["warnings"])


def test_render_automations_stays_armed_when_the_host_keeps_the_callback(monkeypatch, tmp_path):
    """A completed render whose callback stayed registered is still armed."""
    callbacks = _Callbacks()
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(callbacks=callbacks))
    signal_file = tmp_path / "signal.json"
    record = {"completed": True, "signal_id": "abc", "time": "now"}

    def render_then_wait(interval):
        signal_file.write_text(json.dumps(record), encoding="utf-8")

    # The host re-registers the callback right after removing it: the removal
    # cannot be confirmed, so the signal stays armed instead of being forgotten.
    original_remove = callbacks.removeScripts

    def remove_then_keep(id=None):
        original_remove(id)
        callbacks._scripts[id if id is not None else "abc"] = "kept"

    callbacks.removeScripts = remove_then_keep

    result = signals.render_automations(
        runtime,
        actions=["log"],
        wait=True,
        timeout_sec=1.0,
        signal_file=str(signal_file),
        label="nightly",
        sleep=render_then_wait,
    )

    assert result["success"] is True
    assert result["data"]["status"] == "completed"
    assert result["data"]["cleanup"]["removed"] is False
    assert result["data"]["armed"] is True
    assert "nightly" in signals.armed_signals()


def test_render_automations_keeps_the_record_when_removal_fails(monkeypatch, tmp_path):
    """A callback the host kept must stay in the registry.

    Forgetting it first would leave a live callback that no later call can
    find: disarm would report "no armed automation" while the host still fires
    the callback on the next render.
    """
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks(raise_on_remove=True)))
    action = _load_action("action_render_automations.py")
    armed = action.main(actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json"), label="nightly")
    signal_id = armed["data"]["signal_id"]

    result = action.main(disarm=True, label="nightly")

    assert result["success"] is False
    assert result["data"]["failed"] == [{"signal_id": signal_id, "error": "Callback removal failed: callback system is locked"}]
    assert "nightly" in result["data"]["still_armed"]
    assert signals.armed_signals().get("nightly", {}).get("signal_id") == signal_id


def test_render_automations_refuses_to_rearm_over_a_stuck_signal(monkeypatch, tmp_path):
    """Re-arming over a callback the host kept would double-fire on the same file."""
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks(raise_on_remove=True)))
    action = _load_action("action_render_automations.py")
    signal_file = tmp_path / "signal.json"
    first = action.main(actions=["log"], wait=False, signal_file=str(signal_file), label="nightly")

    second = action.main(actions=["log"], wait=False, signal_file=str(signal_file), label="nightly")

    assert second["success"] is False
    assert "still registered" in second["message"]
    assert first["data"]["signal_id"] in second["message"]
    # The original record is intact, so a later disarm can still reach it.
    assert signals.armed_signals().get("nightly", {}).get("signal_id") == first["data"]["signal_id"]


def test_render_automations_reports_completed_and_disarmed_on_success(monkeypatch, tmp_path):
    """A completed render whose callback stayed registered is still armed."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))
    signal_file = tmp_path / "signal.json"
    record = {"completed": True, "signal_id": "x", "time": "now"}

    def render_then_wait(interval):
        signal_file.write_text(json.dumps(record), encoding="utf-8")

    result = signals.render_automations(
        runtime,
        actions=["log"],
        wait=True,
        timeout_sec=1.0,
        signal_file=str(signal_file),
        label="nightly",
        sleep=render_then_wait,
    )

    assert result["success"] is True
    assert result["data"]["cleanup"]["removed"] is True
    assert result["data"]["armed"] is False


def test_render_automations_rejects_a_bad_timeout(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks()))

    result = _load_action("action_render_automations.py").main(
        actions=["log"], wait=False, timeout_sec="soon", signal_file=str(tmp_path / "signal.json")
    )

    assert result["success"] is False
    assert "timeout_sec" in result["message"]


def test_render_automations_reports_a_failed_removal(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(callbacks=_Callbacks(raise_on_remove=True)))
    action = _load_action("action_render_automations.py")
    action.main(actions=["log"], wait=False, signal_file=str(tmp_path / "signal.json"), label="nightly")

    result = action.main(disarm=True, label="nightly")

    assert result["success"] is False
    assert result["data"]["failed"]
    assert "Callback removal failed" in result["data"]["warnings"][0]


def test_vray_ipr_status_reports_a_renderer_without_state(monkeypatch):
    class _BareRenderer:
        pass

    _install_fake_pymxs(monkeypatch, _Runtime(renderer=_BareRenderer()))

    result = _load_action("action_vray_ipr.py").main("status")

    assert result["success"] is True
    assert result["data"]["running"] is None
    assert any("no V-Ray IPR state contract" in warning for warning in result["data"]["warnings"])
