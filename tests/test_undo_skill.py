"""Offline tests for the 3ds Max undo/redo skill.

Follows the bundled-skill testing convention: ``pymxs`` is faked with plain
Python objects, so every path here runs without a 3ds Max host.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _undo_utils  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-undo"
SKILLS_DIR = SKILL_DIR.parent


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_undo_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Point3:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class _Node:
    def __init__(self, name: str, handle: int, position=None, modifiers=None, material=None) -> None:
        self.name = name
        self.handle = handle
        self.parent = None
        self.position = position or _Point3()
        self.modifiers = list(modifiers or [])
        self.material = material

    def move(self, x, y, z) -> None:
        self.position = _Point3(x, y, z)


class _Hold:
    """Stands in for ``rt.theHold``."""

    def __init__(self) -> None:
        self.begin_calls = 0
        self.accept_calls = []
        self.cancel_calls = 0
        self.holding = False
        self.begin_fails = False

    def Begin(self):
        if self.begin_fails:
            raise RuntimeError("cannot begin a hold here")
        self.begin_calls += 1
        self.holding = True

    def Accept(self, label):
        self.accept_calls.append(str(label))
        self.holding = False

    def Cancel(self):
        self.cancel_calls += 1
        self.holding = False

    def Holding(self):
        return self.holding


class _HistoryRuntime:
    """Runtime with a real, reversible operation history.

    Every mutation is pushed onto ``history``; ``max undo`` pops it and applies
    the inverse, so undo and redo behave like the host stack rather than like a
    single no-op flag.
    """

    def __init__(self, *, with_execute: bool = True, with_hold: bool = False, undo_raises: bool = False) -> None:
        self.hero = _Node("hero_mesh", 42)
        self.helper = _Node("helper_mesh", 43)
        self.objects = [self.hero, self.helper]
        self.selection = [self.hero]
        self.currentTime = 0
        self.history = []
        self.redo_stack = []
        self.executed = []
        self.undo_raises = undo_raises
        self.theHold = _Hold() if with_hold else None
        if not with_execute:
            self.execute = None

    # ── host command channel ──
    def execute(self, script: str):
        self.executed.append(script)
        if script == "max undo":
            if self.undo_raises:
                raise RuntimeError("undo is not available in this context")
            self._undo()
        elif script == "max redo":
            self._redo()
        return True

    def _undo(self) -> None:
        if not self.history:
            return  # an empty stack is a silent no-op in 3ds Max
        redo, undo = self.history.pop()
        undo()
        self.redo_stack.append((redo, undo))

    def _redo(self) -> None:
        if not self.redo_stack:
            return  # an empty redo stack is also a silent no-op
        redo, undo = self.redo_stack.pop()
        redo()
        self.history.append((redo, undo))

    # ── mutating operations an agent would have performed ──
    def record(self, redo, undo) -> None:
        self.history.append((redo, undo))
        self.redo_stack.clear()

    def record_move(self, node, x, y, z) -> None:
        previous = (node.position.x, node.position.y, node.position.z)

        def redo():
            node.move(x, y, z)

        def undo():
            node.move(*previous)

        redo()
        self.record(redo, undo)

    def add_node(self, node) -> None:
        def redo():
            self.objects.append(node)

        def undo():
            self.objects.remove(node)

        redo()
        self.record(redo, undo)


def _install_fake_pymxs(monkeypatch, **kwargs):
    runtime = _HistoryRuntime(**kwargs)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


# ── scene fingerprint ───────────────────────────────────────────────────


def test_fingerprint_changes_when_a_node_moves(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    before = _undo_utils.scene_fingerprint(runtime)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)
    after = _undo_utils.scene_fingerprint(runtime)

    assert before["digest"] != after["digest"]
    assert after["node_count"] == 2


def test_fingerprint_is_stable_without_a_scene_change(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    assert _undo_utils.scene_fingerprint(runtime)["digest"] == _undo_utils.scene_fingerprint(runtime)["digest"]


def test_fingerprint_covers_selection_and_time(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    base = _undo_utils.scene_fingerprint(runtime)["digest"]

    runtime.selection = [runtime.helper]
    assert _undo_utils.scene_fingerprint(runtime)["digest"] != base

    runtime.selection = [runtime.hero]
    runtime.currentTime = 30
    assert _undo_utils.scene_fingerprint(runtime)["digest"] != base


def test_fingerprint_flags_truncation_on_large_scenes(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.objects.append(_Node("third_mesh", 44))

    fingerprint = _undo_utils.scene_fingerprint(runtime, node_limit=2)

    assert fingerprint["truncated"] is True
    assert fingerprint["sampled_nodes"] == 2
    assert fingerprint["node_count"] == 3


def test_fingerprint_survives_unreadable_nodes(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    class _BrokenNode:
        name = "broken"

        @property
        def position(self):
            raise RuntimeError("position is unavailable")

    runtime.objects.append(_BrokenNode())

    fingerprint = _undo_utils.scene_fingerprint(runtime)
    assert fingerprint["node_count"] == 3
    assert fingerprint["unreadable_fields"] >= 1


# ── undo_last ───────────────────────────────────────────────────────────


def test_undo_last_reverses_one_operation(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is True
    assert result["data"]["applied"] == 1
    assert result["data"]["completed"] is True
    assert runtime.hero.position.x == 0.0
    assert result["data"]["channel"] == "maxscript:max undo"


def test_undo_last_applies_several_steps(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)
    extra = _Node("extra_mesh", 44)
    runtime.add_node(extra)

    result = _load_action("action_undo_last.py").main(count=2)

    assert result["success"] is True
    assert result["data"]["applied"] == 2
    assert result["data"]["completed"] is True
    assert runtime.hero.position.x == 0.0
    assert extra not in runtime.objects


def test_undo_last_fails_when_the_stack_is_empty(monkeypatch):
    """An undo the host silently ignores must not be reported as success."""
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is False
    assert "no undo step changed the scene" in result["message"]
    assert result["data"]["applied"] == 0
    assert result["data"]["steps"][0]["applied"] is False


def test_undo_last_allow_no_op_reports_a_warning_instead_of_failing(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_undo_last.py").main(allow_no_op=True)

    assert result["success"] is True
    assert any("no undo step changed the scene" in item for item in result["data"]["warnings"])
    assert result["data"]["applied"] == 0


def _install_truncated_runtime(monkeypatch, **kwargs):
    """Runtime whose scene is one node larger than the fingerprint sample."""
    runtime = _install_fake_pymxs(monkeypatch, **kwargs)
    runtime.objects.extend(
        _Node("filler_{}".format(index), 1000 + index)
        for index in range(_undo_utils.MAX_FINGERPRINT_NODES + 1)
    )
    return runtime


def test_undo_last_reports_truncation_when_no_step_was_applied(monkeypatch):
    """A truncated fingerprint must be flagged even on the empty-stack failure.

    Otherwise a real undo that only moved nodes outside the sample is reported
    as a bare "history stack is empty", hiding that verification was partial.
    """
    # Nothing is recorded, so the history stack is empty and nothing is applied.
    _install_truncated_runtime(monkeypatch)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is False
    assert result["data"]["applied"] == 0
    assert result["data"]["fingerprint"]["truncated"] is True
    assert any("best-effort" in item for item in result["data"]["warnings"])


def test_undo_last_reports_truncation_on_the_allow_no_op_path(monkeypatch):
    """The same caveat has to survive the allow_no_op success path."""
    _install_truncated_runtime(monkeypatch)

    result = _load_action("action_undo_last.py").main(allow_no_op=True)

    assert result["success"] is True
    warnings = result["data"]["warnings"]
    assert any("no undo step changed the scene" in item for item in warnings)
    assert any("best-effort" in item for item in warnings)


def test_undo_last_reports_truncation_when_the_host_rejects_the_step(monkeypatch):
    """A host rejection is also only partially verified on a truncated scene."""
    _install_truncated_runtime(monkeypatch, undo_raises=True)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is False
    assert "the host rejected undo step 1" in result["message"]
    assert any("best-effort" in item for item in result["data"]["warnings"])


def test_undo_last_reports_truncation_on_a_successful_step(monkeypatch):
    """Large scenes stay flagged on the success path too - unchanged behaviour."""
    runtime = _install_truncated_runtime(monkeypatch)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is True
    assert any("best-effort" in item for item in result["data"]["warnings"])


def test_undo_last_reports_truncation_when_only_the_before_capture_was_truncated(monkeypatch):
    """A step that crosses the sample limit must still be flagged.

    The before-capture holds 4001 nodes (truncated) and the after-capture holds
    4000 (complete), so the *final* fingerprint reports truncated = false. Only
    tracking truncation per capture catches that the step was partly unverified.
    """
    runtime = _install_fake_pymxs(monkeypatch)
    limit = _undo_utils.MAX_FINGERPRINT_NODES
    # 4000 nodes in total before the recorded operation, 4001 after it.
    runtime.objects.extend(_Node("filler_{}".format(index), 1000 + index) for index in range(limit - 2))
    extra = _Node("extra_mesh", 99)
    runtime.add_node(extra)
    assert len(runtime.objects) == limit + 1

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is True
    assert result["data"]["applied"] == 1
    assert len(runtime.objects) == limit
    # The final fingerprint is complete, so a final-only check would miss this.
    assert result["data"]["fingerprint"]["truncated"] is False
    assert any("best-effort" in item for item in result["data"]["warnings"])
    assert any("first {} nodes".format(limit) in item for item in result["data"]["warnings"])


def test_undo_last_warns_on_a_partial_batch(monkeypatch):
    """Asking for three steps with two available must be explicit, not silent."""
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)
    runtime.record_move(runtime.helper, 0.0, 5.0, 0.0)

    result = _load_action("action_undo_last.py").main(count=3)

    assert result["success"] is True
    assert result["data"]["requested"] == 3
    assert result["data"]["applied"] == 2
    assert result["data"]["completed"] is False
    assert any("only 2 of 3" in item for item in result["data"]["warnings"])


def test_undo_last_stops_at_the_first_no_op_step(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main(count=5)

    # One real step, one no-op, then stop - the host must not be poked again.
    assert len(runtime.executed) == 2
    assert len(result["data"]["steps"]) == 2
    assert result["data"]["applied"] == 1


def test_undo_last_reports_a_host_rejection(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, undo_raises=True)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is False
    assert "the host rejected undo step 1" in result["message"]
    assert runtime.hero.position.x == 10.0


def test_undo_last_rejects_a_bad_count(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    for bad in (0, -1, 101, "2", 1.5, True):
        result = _load_action("action_undo_last.py").main(count=bad)
        assert result["success"] is False, bad
        assert "count must be" in result["message"], bad


def test_undo_last_errors_without_any_host_entry_point(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, with_execute=False)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is False
    assert "exposes no undo entry point" in result["message"]


def test_undo_last_falls_back_to_the_hold_manager(monkeypatch):
    """Hosts without `execute` still undo through the SDK hold manager."""

    class _HoldOnlyRuntime(_HistoryRuntime):
        def __init__(self) -> None:
            super().__init__(with_execute=False)
            self.hold = _Hold()
            self.theHold = self.hold

        def restore(self):  # pragma: no cover - replaced below
            raise AssertionError("wrong entry point")

    runtime = _HoldOnlyRuntime()
    runtime.theHold.Restore = runtime._undo
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main()

    assert result["success"] is True
    assert result["data"]["channel"] == "theHold.Restore"
    assert runtime.hero.position.x == 0.0


def test_undo_prefers_the_documented_command_over_the_hold_manager(monkeypatch):
    """Only one channel may fire per call - never both at the same position."""
    runtime = _install_fake_pymxs(monkeypatch, with_hold=True)
    runtime.theHold.Restore = runtime._undo
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main()

    assert result["data"]["channel"] == "maxscript:max undo"
    assert runtime.executed == ["max undo"]
    assert runtime.theHold.cancel_calls == 0


def test_undo_last_records_fingerprints_per_step(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)

    result = _load_action("action_undo_last.py").main()

    step = result["data"]["steps"][0]
    assert step["step"] == 1
    assert step["applied"] is True
    assert step["fingerprint_before"] != step["fingerprint_after"]


# ── redo_last ───────────────────────────────────────────────────────────


def test_redo_last_replays_an_undone_operation(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)
    _load_action("action_undo_last.py").main()

    result = _load_action("action_redo_last.py").main()

    assert result["success"] is True
    assert result["data"]["applied"] == 1
    assert runtime.hero.position.x == 10.0
    assert result["data"]["channel"] == "maxscript:max redo"


def test_redo_last_fails_when_the_redo_stack_is_empty(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_redo_last.py").main()

    assert result["success"] is False
    assert "no redo step changed the scene" in result["message"]


def test_redo_last_falls_back_to_the_hold_manager(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, with_hold=True)
    runtime.theHold.Redo = runtime._redo
    runtime.record_move(runtime.hero, 10.0, 0.0, 0.0)
    # Undo through the command channel, then redo through the hold manager.
    monkeypatch.setattr(runtime, "execute", lambda script: runtime._undo() if script == "max undo" else None)
    _load_action("action_undo_last.py").main()
    monkeypatch.setattr(runtime, "execute", None, raising=False)

    result = _load_action("action_redo_last.py").main()

    assert result["success"] is True
    assert result["data"]["channel"] == "theHold.Redo"
    assert runtime.hero.position.x == 10.0


# ── get_undo_status ─────────────────────────────────────────────────────


def test_get_undo_status_reports_channels_and_fingerprint(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, with_hold=True)
    runtime.theHold.Restore = runtime._undo
    runtime.theHold.Redo = runtime._redo

    result = _load_action("action_get_undo_status.py").main()

    assert result["success"] is True
    assert result["data"]["undo_supported"] is True
    # The documented command is listed first; the hold manager is the fallback.
    assert result["data"]["undo_channels"] == ["maxscript:max undo", "theHold.Restore"]
    assert result["data"]["redo_channels"] == ["maxscript:max redo", "theHold.Redo"]
    assert result["data"]["redo_supported"] is True
    assert result["data"]["single_step_grouping_supported"] is True
    assert result["data"]["fingerprint"]["node_count"] == 2
    assert result["data"]["undo_tool"] == "3dsmax-undo__undo_last"


def test_get_undo_status_is_read_only(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    _load_action("action_get_undo_status.py").main()

    assert runtime.executed == []


def test_get_undo_status_reports_a_host_without_undo(monkeypatch):
    _install_fake_pymxs(monkeypatch, with_execute=False)

    result = _load_action("action_get_undo_status.py").main()

    assert result["success"] is True
    assert result["data"]["undo_supported"] is False
    assert result["data"]["redo_supported"] is False
    assert result["data"]["single_step_grouping_supported"] is False
    assert "no undo entry point" in result["message"]


def test_get_undo_status_publishes_the_granularity_vocabulary(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_undo_status.py").main()

    vocabulary = result["data"]["granularity_vocabulary"]
    assert set(vocabulary) == set(_undo_utils.VALID_GRANULARITIES)
    for value in vocabulary.values():
        assert value.strip()


# ── undo_step: single-hold wrapper for future atomic batches ────────────


def test_undo_step_opens_and_accepts_one_hold(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, with_hold=True)

    with _undo_utils.undo_step(runtime, "scene_patch") as state:
        assert state["engaged"] is True
        runtime.record_move(runtime.hero, 1.0, 2.0, 3.0)

    assert runtime.theHold.begin_calls == 1
    assert runtime.theHold.accept_calls == ["scene_patch"]
    assert runtime.theHold.cancel_calls == 0


def test_undo_step_cancels_on_an_exception(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, with_hold=True)

    try:
        with _undo_utils.undo_step(runtime, "scene_patch"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    else:  # pragma: no cover - the exception must propagate
        raise AssertionError("undo_step swallowed the exception")

    assert runtime.theHold.cancel_calls == 1
    assert runtime.theHold.accept_calls == []


def test_undo_step_reports_when_the_host_cannot_group(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    with _undo_utils.undo_step(runtime) as state:
        assert state["engaged"] is False

    assert "Begin/Accept/Cancel" in state["reason"]


def test_undo_step_refuses_a_nested_hold(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, with_hold=True)

    with _undo_utils.undo_step(runtime) as outer:
        assert outer["engaged"] is True
        with _undo_utils.undo_step(runtime) as inner:
            assert inner["engaged"] is False
            assert "already open" in inner["reason"]

    assert runtime.theHold.accept_calls == ["dcc-mcp edit"]


def test_undo_step_reports_a_failing_begin(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, with_hold=True)
    runtime.theHold.begin_fails = True

    with _undo_utils.undo_step(runtime) as state:
        assert state["engaged"] is False

    assert "theHold.Begin() failed" in state["reason"]


# ── tools.yaml contract ─────────────────────────────────────────────────


def test_undo_tools_declare_required_metadata():
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    assert set(by_name) == {"undo_last", "redo_last", "get_undo_status"}
    for name, tool in by_name.items():
        assert tool["affinity"] == "main", name
        assert tool["enforce_thread_affinity"] is True, name
        assert (SKILL_DIR / tool["source_file"]).is_file(), name
        for key in ("side_effects", "produces", "risk", "intent", "annotations", "undo"):
            assert key in tool, (name, key)
        assert isinstance(tool["undo"]["supported"], bool), name
        assert tool["undo"]["granularity"] in _undo_utils.VALID_GRANULARITIES, name
        assert tool["undo"]["notes"].strip(), name

    assert by_name["get_undo_status"]["read_only"] is True
    assert by_name["undo_last"]["read_only"] is False


def test_every_destructive_tool_declares_undo_semantics():
    """Requirement: destructive tools must state whether they can be reversed."""
    checked = 0
    for skill_dir in sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir()):
        tools = yaml.safe_load((skill_dir / "tools.yaml").read_text(encoding="utf-8"))["tools"]
        for tool in tools:
            if tool.get("destructive") is not True:
                continue
            checked += 1
            undo = tool.get("undo")
            assert isinstance(undo, dict), tool["name"]
            assert isinstance(undo.get("supported"), bool), tool["name"]
            assert undo.get("granularity") in _undo_utils.VALID_GRANULARITIES, tool["name"]
            assert isinstance(undo.get("notes"), str) and undo["notes"].strip(), tool["name"]
            if undo["granularity"] == _undo_utils.GRANULARITY_NONE:
                assert undo["supported"] is False, tool["name"]

    assert checked >= 14, checked


def test_undo_metadata_matches_known_granularities():
    """Every tool with an undo block - destructive or not - is in the undo doc."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "UNDO.md").read_text(encoding="utf-8")
    checked = 0
    for skill_dir in sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir()):
        tools = yaml.safe_load((skill_dir / "tools.yaml").read_text(encoding="utf-8"))["tools"]
        for tool in tools:
            if not isinstance(tool.get("undo"), dict):
                continue
            checked += 1
            exported = "{}__{}".format(skill_dir.name, tool["name"])
            assert exported in doc, exported
    assert checked >= 29, checked


def _declared_undo_blocks():
    """Yield ``(skill_name, tool_name, tool, undo)`` for every declared block."""
    for skill_dir in sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir()):
        tools = yaml.safe_load((skill_dir / "tools.yaml").read_text(encoding="utf-8"))["tools"]
        for tool in tools:
            undo = tool.get("undo")
            if isinstance(undo, dict):
                yield skill_dir.name, tool["name"], tool, undo


def test_every_declared_undo_block_uses_the_vocabulary():
    """A non-destructive tool may declare undo metadata, but only on these terms."""
    checked = 0
    for skill_name, tool_name, _tool, undo in _declared_undo_blocks():
        checked += 1
        label = "{}__{}".format(skill_name, tool_name)
        assert isinstance(undo.get("supported"), bool), label
        assert undo.get("granularity") in _undo_utils.VALID_GRANULARITIES, label
        assert isinstance(undo.get("notes"), str) and undo["notes"].strip(), label
        if undo["granularity"] == _undo_utils.GRANULARITY_NONE:
            assert undo["supported"] is False, label
        else:
            assert undo["supported"] is True, label

    assert checked >= 29, checked


# The undo contract (docs/UNDO.md) requires an `undo` block for every destructive
# tool and for every multi-node write path. A multi-node write path is a tool
# that writes scene nodes and takes a plural node selection, i.e. an array-valued
# `node_names` / `handles` property - one call can change N nodes, so the agent
# cannot infer the undo count from the tool list. Single-node and settings-level
# writes may declare one, but are not required to.
UNDO_SCOPE_SKILLS = ("3dsmax-scene",)

PLURAL_NODE_PROPERTIES = ("node_names", "handles", "nodes")


def _is_multi_node_write_path(tool):
    """True when one call can write more than one scene node."""
    if tool.get("read_only") is True:
        return False
    side_effects = tool.get("side_effects") or {}
    if not any(bool(side_effects.get(key)) for key in ("creates", "modifies", "deletes")):
        return False
    if "scene_nodes" not in (side_effects.get("targets") or []):
        return False
    properties = (tool.get("input_schema") or {}).get("properties") or {}
    return any(
        isinstance(properties.get(key), dict) and properties.get(key).get("type") == "array"
        for key in PLURAL_NODE_PROPERTIES
    )


# Pinned so a granularity cannot change silently; the rule in
# test_multi_node_write_paths_declare_undo_semantics decides which tools are in
# scope, this map only fixes what each one declares.
EXPECTED_WRITE_GRANULARITY = {
    "set_object_property": _undo_utils.GRANULARITY_SINGLE_CALL,
    "create_object": _undo_utils.GRANULARITY_SINGLE_CALL,
    "set_selection": _undo_utils.GRANULARITY_NONE,
    "batch_rename_objects": _undo_utils.GRANULARITY_BATCH_CALL,
    "transform_object": _undo_utils.GRANULARITY_BATCH_CALL,
    "clone_objects": _undo_utils.GRANULARITY_BATCH_CALL,
    "merge_file": _undo_utils.GRANULARITY_BATCH_CALL,
    "duplicate_nodes": _undo_utils.GRANULARITY_BATCH_CALL,
    "group_nodes": _undo_utils.GRANULARITY_BATCH_CALL,
    "set_visibility": _undo_utils.GRANULARITY_BATCH_CALL,
    "center_pivots": _undo_utils.GRANULARITY_BATCH_CALL,
    "freeze_transforms": _undo_utils.GRANULARITY_BATCH_CALL,
}


def test_multi_node_write_paths_declare_undo_semantics():
    """Every multi-node write path in scope declares undo metadata.

    This is the rule the undo doc states, derived from the declarations rather
    than from a hand-maintained list, so a new multi-node writer cannot slip
    through unannotated.
    """
    checked = 0
    for skill_name in UNDO_SCOPE_SKILLS:
        tools = yaml.safe_load((SKILLS_DIR / skill_name / "tools.yaml").read_text(encoding="utf-8"))["tools"]
        for tool in tools:
            if not _is_multi_node_write_path(tool):
                continue
            checked += 1
            label = "{}__{}".format(skill_name, tool["name"])
            undo = tool.get("undo")
            assert isinstance(undo, dict), label
            assert undo.get("granularity") in _undo_utils.VALID_GRANULARITIES, label
            assert isinstance(undo.get("notes"), str) and undo["notes"].strip(), label

    # Guards against the rule silently matching nothing.
    assert checked >= 9, checked


def test_write_tools_declare_their_expected_granularity():
    """Non-destructive write paths state how many undo entries one call leaves."""
    tools = yaml.safe_load((SKILLS_DIR / "3dsmax-scene" / "tools.yaml").read_text(encoding="utf-8"))
    by_name = {tool["name"]: tool for tool in tools["tools"]}

    for name, expected in EXPECTED_WRITE_GRANULARITY.items():
        tool = by_name[name]
        # Declaring undo coverage does not reclassify the tool as destructive.
        assert tool["destructive"] is False, name
        undo = tool.get("undo")
        assert isinstance(undo, dict), name
        assert undo["granularity"] == expected, name
        assert undo["supported"] is (expected != _undo_utils.GRANULARITY_NONE), name
        assert undo["notes"].strip(), name


def test_batch_call_is_published_in_the_granularity_vocabulary(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_undo_status.py").main()

    vocabulary = result["data"]["granularity_vocabulary"]
    assert _undo_utils.GRANULARITY_BATCH_CALL in vocabulary
    assert "undo once" in vocabulary[_undo_utils.GRANULARITY_BATCH_CALL]
