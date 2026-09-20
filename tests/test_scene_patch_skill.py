"""Offline tests for the atomic scene patch tool.

``pymxs`` is faked with plain Python objects, so the preflight, the single-undo
-step grouping, and every rollback path run without a 3ds Max host. The fake
runtime keeps a real hold manager and a real inverse-operation stack, which is
what makes the atomicity claims testable: a cancelled hold undoes the edits that
already ran.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _undo_utils  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scene"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_patch_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Fake pymxs runtime ─────────────────────────────────────────────────


class _Point3:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class _Node:
    """Node that records an inverse operation for every attribute write.

    The inverse is registered with the runtime's hold while one is open, which
    is what turns "a cancelled hold undid the edits that already ran" into an
    observable assertion rather than a claim.
    """

    def __init__(self, name: str, handle: int, runtime=None) -> None:
        object.__setattr__(self, "_runtime", runtime)
        object.__setattr__(self, "reject_radius", False)
        self.name = name
        self.handle = handle
        self.parent = None
        self.pos = _Point3()
        self.isHidden = False
        self.wirecolor = _Point3(0.2, 0.2, 0.2)
        self.radius = 10.0
        self.renderable = True

    def __setattr__(self, key, value):
        # A property the host refuses to accept, used to drive the rollback path.
        if key == "radius" and object.__getattribute__(self, "reject_radius"):
            raise RuntimeError("the host rejected this radius")
        previous = getattr(self, key, None)
        super().__setattr__(key, value)
        runtime = object.__getattribute__(self, "_runtime")
        if runtime is not None:
            runtime.record_undo(lambda: super(_Node, self).__setattr__(key, previous))


class _Hold:
    """Stands in for ``rt.theHold``, with a real inverse-operation stack."""

    def __init__(self) -> None:
        self.begin_calls = 0
        self.accept_calls: list = []
        self.cancel_calls = 0
        self.holding = False
        self.pending: list = []

    def Begin(self) -> None:
        self.begin_calls += 1
        self.holding = True
        self.pending = []

    def Accept(self, label) -> None:
        self.accept_calls.append(str(label))
        self.holding = False
        self.pending = []

    def Cancel(self) -> None:
        self.cancel_calls += 1
        self.holding = False
        for undo in reversed(self.pending):
            undo()
        self.pending = []

    def Holding(self):
        return self.holding


class _Runtime:
    def __init__(self, *, with_hold: bool = True) -> None:
        self.theHold = _Hold() if with_hold else None
        self.hero = _Node("hero_mesh", 42, self)
        self.sidekick = _Node("sidekick_mesh", 43, self)
        self.objects = [self.hero, self.sidekick]
        self.executed: list = []

    def Point3(self, x, y, z):
        return _Point3(float(x), float(y), float(z))

    def getNodeByName(self, name: str):
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def execute(self, script: str):
        self.executed.append(script)
        return True

    # ── inverse operations collected while a hold is open ──
    def record_undo(self, undo) -> None:
        if self.theHold is not None and self.theHold.holding:
            self.theHold.pending.append(undo)


def _install_runtime(monkeypatch, **kwargs):
    runtime = _Runtime(**kwargs)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def _patch(**kwargs):
    return _load_action("action_scene_patch.py").main(**kwargs)


# ── request validation ─────────────────────────────────────────────────


def test_empty_edits_are_rejected(monkeypatch):
    _install_runtime(monkeypatch)

    result = _patch(edits=[])

    assert result["success"] is False
    assert "non-empty array" in result["message"]
    assert result["data"]["applied"] == 0


def test_more_than_the_edit_ceiling_is_rejected(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    result = _patch(
        edits=[{"op": "rename", "node_name": "hero_mesh", "name": "n{}".format(i)} for i in range(257)]
    )

    assert result["success"] is False
    assert "at most 256" in result["message"]
    assert runtime.theHold.begin_calls == 0


def test_unsupported_op_is_rejected(monkeypatch):
    _install_runtime(monkeypatch)

    result = _patch(edits=[{"op": "delete_everything", "node_name": "hero_mesh"}])

    assert result["success"] is False
    assert "unsupported op" in result["message"]


def test_an_edit_without_a_target_is_rejected(monkeypatch):
    _install_runtime(monkeypatch)

    result = _patch(edits=[{"op": "rename", "name": "renamed"}])

    assert result["success"] is False
    assert "node_name or handle is required" in result["message"]


def test_a_non_integer_handle_is_rejected(monkeypatch):
    _install_runtime(monkeypatch)

    result = _patch(edits=[{"op": "rename", "handle": True, "name": "renamed"}])

    assert result["success"] is False
    assert "handle must be an integer" in result["message"]


# ── preflight ──────────────────────────────────────────────────────────


def test_a_missing_node_fails_preflight_and_writes_nothing(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    result = _patch(
        edits=[
            {"op": "rename", "node_name": "hero_mesh", "name": "renamed_hero"},
            {"op": "rename", "node_name": "ghost_mesh", "name": "renamed_ghost"},
        ]
    )

    assert result["success"] is False
    assert "1 of 2 edit(s) failed preflight" in result["message"]
    assert result["data"]["applied"] == 0
    # The valid edit must not have been applied before the failure.
    assert runtime.hero.name == "hero_mesh"
    assert runtime.theHold.begin_calls == 0


def test_a_missing_property_fails_preflight(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    result = _patch(
        edits=[{"op": "set_property", "node_name": "hero_mesh", "property": "no_such_prop", "value": 1}]
    )

    assert result["success"] is False
    assert "does not exist" in result["data"]["errors"][0]["message"]
    assert runtime.theHold.begin_calls == 0


def test_a_wrong_value_type_fails_preflight(monkeypatch):
    """A value the property cannot accept is refused before any write."""
    runtime = _install_runtime(monkeypatch)

    result = _patch(
        edits=[{"op": "set_property", "node_name": "hero_mesh", "property": "radius", "value": "not a number"}]
    )

    assert result["success"] is False
    assert "expects a numeric value" in result["data"]["errors"][0]["message"]
    assert runtime.hero.radius == 10.0
    assert runtime.theHold.begin_calls == 0


def test_a_private_property_is_refused(monkeypatch):
    _install_runtime(monkeypatch)

    result = _patch(edits=[{"op": "set_property", "node_name": "hero_mesh", "property": "_secret", "value": 1}])

    assert result["success"] is False
    assert "private property" in result["data"]["errors"][0]["message"]


def test_a_duplicate_target_fails_preflight(monkeypatch):
    """Two edits on one node+property are ambiguous, so the batch is refused."""
    _install_runtime(monkeypatch)

    result = _patch(
        edits=[
            {"op": "set_property", "node_name": "hero_mesh", "property": "radius", "value": 1.0},
            {"op": "set_property", "node_name": "hero_mesh", "property": "radius", "value": 2.0},
        ]
    )

    assert result["success"] is False
    assert "duplicates edit 0" in result["data"]["errors"][0]["message"]


def test_dry_run_reports_the_plan_without_writing(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    result = _patch(
        edits=[{"op": "rename", "node_name": "hero_mesh", "name": "renamed_hero"}],
        dry_run=True,
    )

    assert result["success"] is True
    assert result["data"]["applied"] == 0
    assert result["data"]["dry_run"] is True
    planned = result["data"]["planned"][0]
    assert planned["before"] == "hero_mesh"
    assert planned["after"] == "renamed_hero"
    assert runtime.hero.name == "hero_mesh"
    assert runtime.theHold.begin_calls == 0


# ── atomic apply ───────────────────────────────────────────────────────


def test_a_batch_applies_every_edit_in_one_undo_step(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    result = _patch(
        edits=[
            {"op": "rename", "node_name": "hero_mesh", "name": "renamed_hero"},
            {"op": "set_property", "node_name": "hero_mesh", "property": "radius", "value": 25.0},
            {"op": "set_position", "handle": 43, "position": [1.0, 2.0, 3.0]},
            {"op": "set_visibility", "node_name": "sidekick_mesh", "visible": False},
        ],
        label="my batch",
    )

    assert result["success"] is True
    assert result["data"]["applied"] == 4
    assert runtime.hero.name == "renamed_hero"
    assert runtime.hero.radius == 25.0
    assert (runtime.sidekick.pos.x, runtime.sidekick.pos.y, runtime.sidekick.pos.z) == (1.0, 2.0, 3.0)
    assert runtime.sidekick.isHidden is True

    # One hold, accepted once: the batch is a single native undo entry.
    assert runtime.theHold.begin_calls == 1
    assert runtime.theHold.accept_calls == ["my batch"]
    assert runtime.theHold.cancel_calls == 0
    assert result["data"]["undo"]["grouped"] is True
    assert result["data"]["undo"]["granularity"] == _undo_utils.GRANULARITY_SINGLE_CALL


def test_every_applied_edit_reports_a_verified_readback(monkeypatch):
    _install_runtime(monkeypatch)

    result = _patch(edits=[{"op": "set_property", "node_name": "hero_mesh", "property": "radius", "value": 7.5}])

    edit = result["data"]["edits"][0]
    assert edit["verified"] is True
    assert edit["before"] == 10.0
    assert edit["after"] == 7.5


# ── rollback ───────────────────────────────────────────────────────────


def test_a_failed_write_rolls_the_whole_batch_back(monkeypatch):
    """A host rejection part-way through must leave the scene untouched."""
    runtime = _install_runtime(monkeypatch)
    runtime.sidekick.reject_radius = True
    original_name = runtime.hero.name
    original_radius = runtime.hero.radius

    result = _patch(
        edits=[
            {"op": "rename", "node_name": "hero_mesh", "name": "renamed_hero"},
            {"op": "set_property", "node_name": "hero_mesh", "property": "radius", "value": 99.0},
            {"op": "set_property", "node_name": "sidekick_mesh", "property": "radius", "value": 5.0},
        ]
    )

    assert result["success"] is False
    assert result["data"]["applied"] == 0
    assert result["data"]["rolled_back"] is True
    assert result["data"]["index"] == 2
    assert runtime.theHold.cancel_calls == 1
    assert runtime.theHold.accept_calls == []
    # The edits that already ran were undone by the cancelled hold.
    assert runtime.hero.name == original_name
    assert runtime.hero.radius == original_radius
    assert runtime.sidekick.radius == 10.0


def test_a_write_that_does_not_verify_rolls_the_batch_back(monkeypatch):
    """Silent success is the failure mode this tool must never produce."""

    class _SilentNode(_Node):
        def __setattr__(self, key, value):
            if key == "renderable":
                return  # the host accepted the write and then ignored it
            super().__setattr__(key, value)

    runtime = _install_runtime(monkeypatch)
    runtime.hero.__class__ = _SilentNode

    result = _patch(
        edits=[
            {"op": "set_property", "node_name": "hero_mesh", "property": "renderable", "value": False},
            {"op": "rename", "node_name": "hero_mesh", "name": "renamed_hero"},
        ]
    )

    assert result["success"] is False
    assert "did not take effect" in result["message"]
    assert runtime.hero.name == "hero_mesh"


# ── ungrouped hosts ────────────────────────────────────────────────────


def test_a_host_without_undo_grouping_refuses_by_default(monkeypatch):
    """No hold means no atomicity, so the batch is refused rather than half-applied."""
    runtime = _install_runtime(monkeypatch, with_hold=False)

    result = _patch(edits=[{"op": "rename", "node_name": "hero_mesh", "name": "renamed_hero"}])

    assert result["success"] is False
    assert "cannot group the batch into one undo step" in result["message"]
    assert runtime.hero.name == "hero_mesh"


def test_allow_ungrouped_applies_and_reports_the_missing_grouping(monkeypatch):
    runtime = _install_runtime(monkeypatch, with_hold=False)

    result = _patch(
        edits=[{"op": "rename", "node_name": "hero_mesh", "name": "renamed_hero"}],
        allow_ungrouped=True,
    )

    assert result["success"] is True
    assert result["data"]["undo"]["grouped"] is False
    assert runtime.hero.name == "renamed_hero"
    assert any("not grouped" in item for item in result["data"]["warnings"])


# ── tools.yaml contract ────────────────────────────────────────────────


def test_scene_patch_declares_atomic_undo_metadata():
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    tool = next(item for item in tools if item["name"] == "scene_patch")

    assert tool["affinity"] == "main"
    assert tool["enforce_thread_affinity"] is True
    assert (SKILL_DIR / tool["source_file"]).is_file()
    assert tool["read_only"] is False
    assert tool["destructive"] is True
    assert tool["annotations"]["destructive_hint"] is True

    # The single native undo step is the whole point of the tool: a destructive
    # batch declaring anything else would break the undo contract.
    assert tool["undo"]["supported"] is True
    assert tool["undo"]["granularity"] == _undo_utils.GRANULARITY_SINGLE_CALL
    assert tool["undo"]["notes"].strip()


def test_scene_patch_edit_ceiling_matches_the_module_constant():
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    tool = next(item for item in tools if item["name"] == "scene_patch")

    assert tool["input_schema"]["properties"]["edits"]["maxItems"] == _load_action("action_scene_patch.py").MAX_EDITS
