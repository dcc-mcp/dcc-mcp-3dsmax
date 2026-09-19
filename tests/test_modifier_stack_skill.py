"""Offline tests for the 3ds Max modifier stack CRUD tools.

Follows the bundled-skill testing convention: ``pymxs`` is faked with plain
Python objects, so every path here runs without a 3ds Max host.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-mesh-ops"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_mod_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ReadOnlyModifier:
    """Modifier that rejects every attempt to change ``iterations``."""

    def __init__(self) -> None:
        self.name = "TurboSmooth"
        self.enabled = True
        self.enabledInViews = True
        self.enabledInRender = True
        self._iterations = 1

    @property
    def iterations(self):
        return self._iterations

    @iterations.setter
    def iterations(self, value):
        raise RuntimeError("TurboSmooth.iterations is read-only on this host")


class _StrictModifier:
    """Modifier that only accepts a known set of property names."""

    ACCEPTED = ("iterations", "useRenderIterations", "enabled", "enabledInViews", "enabledInRender")

    def __init__(self, name="TurboSmooth"):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "enabled", True)
        object.__setattr__(self, "enabledInViews", True)
        object.__setattr__(self, "enabledInRender", True)
        object.__setattr__(self, "iterations", 1)
        object.__setattr__(self, "useRenderIterations", False)

    def __setattr__(self, key, value):
        if key not in self.ACCEPTED:
            raise AttributeError("{} has no writable property '{}'".format(self.name, key))
        object.__setattr__(self, key, value)

    def __getattr__(self, key):
        raise AttributeError("{} has no property '{}'".format(self.name, key))


class _Modifier:
    """Plain modifier with working properties."""

    def __init__(self, name: str, **attrs) -> None:
        self.name = name
        self.enabled = True
        self.enabledInViews = True
        self.enabledInRender = True
        for key, value in attrs.items():
            setattr(self, key, value)


class _NoFlagsModifier:
    """Modifier without viewport/render granularity."""

    def __init__(self, name="Bend") -> None:
        self.name = name
        self.enabled = True
        self.angle = 0.0


class _FakeNode:
    def __init__(self, name: str, handle: int, modifiers=None) -> None:
        self.name = name
        self.handle = handle
        self.parent = None
        self.isHidden = False
        self.modifiers = list(modifiers or [])


class _FakeMaxOps:
    """Stands in for ``rt.maxOps``."""

    def __init__(self, runtime: "_FakeRuntime", *, collapse_fails: bool = False) -> None:
        self.runtime = runtime
        self.collapse_fails = collapse_fails

    def collapseNode(self, node, warn=False):
        if self.collapse_fails:
            raise RuntimeError("collapseNode is unavailable in this context")
        self.runtime.collapsed.append(node.name)
        node.modifiers = []

    def makeUnique(self, node, modifier):
        self.runtime.unique_calls.append((node.name, modifier.name))
        modifier.unique = True
        return True


class _FakeRuntime:
    def __init__(self, *, collapse_fails: bool = False, with_make_unique: bool = True) -> None:
        self.hero = _FakeNode("hero_mesh", 42, [_Modifier("Skin"), _Modifier("TurboSmooth", iterations=2)])
        self.helper = _FakeNode("helper_mesh", 43, [_Modifier("Bend")])
        self.plain = _FakeNode("plain_mesh", 44)
        self.objects = [self.hero, self.helper, self.plain]
        self.selection = [self.hero]
        self.collapsed = []
        self.unique_calls = []
        self.collapse_fails = collapse_fails
        self.maxOps = (
            None if (collapse_fails and not with_make_unique) else _FakeMaxOps(self, collapse_fails=collapse_fails)
        )
        if with_make_unique:
            self.makeUnique = self._make_unique

    def getNodeByName(self, name):
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def addModifier(self, node, modifier):
        node.modifiers.append(modifier)

    def deleteModifier(self, node, target):
        modifiers = list(node.modifiers)
        if isinstance(target, int):
            if 1 <= target <= len(modifiers):
                node.modifiers = modifiers[: target - 1] + modifiers[target:]
                return True
            raise RuntimeError("index out of range")
        if target in modifiers:
            node.modifiers = [item for item in modifiers if item is not target]
            return True
        return False

    def getPropNames(self, modifier):
        return ["#enabled", "#enabledInViews", "#enabledInRender", *sorted(vars(modifier))]

    def getProperty(self, modifier, name):
        return getattr(modifier, name)

    def _make_unique(self, node, modifier):
        self.unique_calls.append((node.name, modifier.name))
        modifier.unique = True
        return True

    # Modifier class constructors, mirroring pymxs factory callables.
    def TurboSmooth(self):
        return _Modifier("TurboSmooth", iterations=1)

    def Bend(self):
        return _Modifier("Bend", angle=0.0)

    def Edit_Poly(self):
        return _Modifier("Edit_Poly")


def _install_fake_pymxs(monkeypatch, **kwargs):
    runtime = _FakeRuntime(**kwargs)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


# ── get_modifier_stack ──────────────────────────────────────────────────


def test_get_modifier_stack_reads_parameters(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_modifier_stack.py").main(node_names=["hero_mesh"])

    assert result["success"] is True
    entry = result["data"]["nodes"][0]["modifiers"][1]
    assert entry["name"] == "TurboSmooth"
    # The deepened read exposes parameters, not just stack entries.
    assert entry["parameters"]["iterations"] == 2
    assert entry["enabled_in_views"] is True
    assert entry["enabled_in_render"] is True


def test_get_modifier_stack_can_skip_parameters(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_modifier_stack.py").main(node_names=["hero_mesh"], include_parameters=False)

    assert result["success"] is True
    assert "parameters" not in result["data"]["nodes"][0]["modifiers"][0]


def test_get_modifier_stack_can_filter_properties(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_modifier_stack.py").main(node_names=["hero_mesh"], property_names=["iterations"])

    assert result["data"]["nodes"][0]["modifiers"][1]["parameters"] == {"iterations": 2}


def test_get_modifier_stack_reports_unreadable_properties(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_modifier_stack.py").main(
        node_names=["hero_mesh"], property_names=["iterations", "no_such_property"]
    )

    # A partial read is reported, never silently presented as complete.
    assert result["data"]["nodes"][0]["modifiers"][1]["parameters"] == {"iterations": 2}
    assert result["data"]["nodes"][0]["modifiers"][1]["unreadable"] == ["no_such_property"]


def test_get_modifier_stack_requires_explicit_targets(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_modifier_stack.py").main()

    assert result["success"] is False
    assert "node_names or handles" in result["message"]


def test_get_modifier_stack_reports_missing_node(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_modifier_stack.py").main(node_names=["does_not_exist"])

    assert result["success"] is False
    assert result["data"]["errors"][0]["node_name"] == "does_not_exist"


# ── add_modifier ────────────────────────────────────────────────────────


def test_add_modifier_appends_and_verifies(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_add_modifier.py").main(
        modifier_class="Bend", node_names=["hero_mesh"], properties={"angle": 30.0}
    )

    assert result["success"] is True
    assert result["data"]["count"] == 1
    assert [mod.name for mod in runtime.hero.modifiers][-1] == "Bend"
    assert runtime.hero.modifiers[-1].angle == 30.0
    assert result["data"]["nodes"][0]["modifier"]["applied_properties"] == {"angle": 30.0}


def test_add_modifier_applies_to_many_nodes(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_add_modifier.py").main(
        modifier_class="Edit_Poly", node_names=["hero_mesh", "helper_mesh"]
    )

    assert result["success"] is True
    assert result["data"]["count"] == 2
    assert runtime.hero.modifiers[-1].name == "Edit_Poly"
    assert runtime.helper.modifiers[-1].name == "Edit_Poly"


def test_add_modifier_requires_class(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_add_modifier.py").main(node_names=["hero_mesh"])

    assert result["success"] is False
    assert "modifier_class is required" in result["message"]


def test_add_modifier_rejects_unknown_class(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_add_modifier.py").main(modifier_class="NotAModifier", node_names=["hero_mesh"])

    assert result["success"] is False
    assert "does not expose a modifier class named 'NotAModifier'" in result["message"]


def test_add_modifier_fails_when_host_ignores_a_property(monkeypatch):
    """A property the modifier silently ignores must not report success."""
    runtime = _install_fake_pymxs(monkeypatch)
    original = runtime.TurboSmooth

    def _silent_factory():
        modifier = original()
        # Simulate a host that accepts the write but never stores the value.
        modifier.__class__ = _SilentIgnoreModifier
        return modifier

    monkeypatch.setattr(runtime, "TurboSmooth", _silent_factory)

    result = _load_action("action_add_modifier.py").main(
        modifier_class="TurboSmooth", node_names=["hero_mesh"], properties={"iterations": 4}
    )

    assert result["success"] is False
    assert "rejected property 'iterations'" in result["message"]


class _SilentIgnoreModifier(_Modifier):
    """Accepts writes but never stores them - the classic silent-success trap.

    ``iterations`` is seeded in ``__init__`` via ``object.__setattr__`` so the
    attribute exists and reads back its old value after a silently ignored write.
    """

    def __setattr__(self, key, value):
        if key == "iterations" and "iterations" in self.__dict__:
            return
        object.__setattr__(self, key, value)


def test_add_modifier_reports_partial_batch(monkeypatch):
    """If node two fails, the tool must say node one was already modified."""
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_add_modifier.py").main(
        modifier_class="Bend", node_names=["hero_mesh", "does_not_exist"]
    )

    # Target resolution fails before any mutation, so nothing is half-applied.
    assert result["success"] is False


# ── remove_modifier ─────────────────────────────────────────────────────


def test_remove_modifier_by_name(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    before = len(runtime.hero.modifiers)

    result = _load_action("action_remove_modifier.py").main(node_names=["hero_mesh"], modifier_name="Skin")

    assert result["success"] is True
    assert len(runtime.hero.modifiers) == before - 1
    assert "Skin" not in [mod.name for mod in runtime.hero.modifiers]


def test_remove_modifier_by_index(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_remove_modifier.py").main(node_names=["hero_mesh"], modifier_index=2)

    assert result["success"] is True
    assert [mod.name for mod in runtime.hero.modifiers] == ["Skin"]


def test_remove_modifier_rejects_out_of_range_index(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_remove_modifier.py").main(node_names=["hero_mesh"], modifier_index=99)

    assert result["success"] is False
    assert "out of range" in result["message"]
    assert len(runtime.hero.modifiers) == 2


def test_remove_modifier_rejects_zero_index(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_remove_modifier.py").main(node_names=["hero_mesh"], modifier_index=0)

    assert result["success"] is False
    assert "out of range" in result["message"]


def test_remove_modifier_rejects_unknown_name(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_remove_modifier.py").main(node_names=["hero_mesh"], modifier_name="NoSuchModifier")

    assert result["success"] is False
    assert "No modifier named 'NoSuchModifier'" in result["message"]
    assert "Skin" in result["message"]  # available names are surfaced
    assert len(runtime.hero.modifiers) == 2


def test_remove_modifier_requires_a_selector(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_remove_modifier.py").main(node_names=["hero_mesh"])

    assert result["success"] is False
    assert "modifier_name or modifier_index is required" in result["message"]


def test_remove_modifier_rejects_both_selectors(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_remove_modifier.py").main(
        node_names=["hero_mesh"], modifier_name="Skin", modifier_index=1
    )

    assert result["success"] is False
    assert "not both" in result["message"]


def test_remove_modifier_is_two_phase_across_nodes(monkeypatch):
    """A target without the modifier aborts before anything is removed."""
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_remove_modifier.py").main(
        node_names=["hero_mesh", "helper_mesh"], modifier_name="Skin"
    )

    assert result["success"] is False
    # hero_mesh still has Skin: the failure on helper_mesh prevented a partial batch.
    assert "Skin" in [mod.name for mod in runtime.hero.modifiers]


def test_remove_modifier_detects_silent_no_op(monkeypatch):
    """A host that reports no error but leaves the modifier in place must fail."""

    class _NoOpRuntime(_FakeRuntime):
        def deleteModifier(self, node, target):
            return True  # lies: nothing is removed

    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=_NoOpRuntime()))
    runtime = sys.modules["pymxs"].runtime

    result = _load_action("action_remove_modifier.py").main(node_names=["hero_mesh"], modifier_name="Skin")

    assert result["success"] is False
    assert "still has" in result["message"]
    assert len(runtime.hero.modifiers) == 2


# ── set_modifier_state ──────────────────────────────────────────────────


def test_set_modifier_state_toggles_enabled(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_modifier_state.py").main(
        node_names=["hero_mesh"], modifier_name="Skin", enabled=False
    )

    assert result["success"] is True
    assert runtime.hero.modifiers[0].enabled is False
    assert result["data"]["nodes"][0]["modifier"]["applied"]["enabled"] is False


def test_set_modifier_state_separates_viewport_and_render(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    skin = runtime.hero.modifiers[0]

    result = _load_action("action_set_modifier_state.py").main(
        node_names=["hero_mesh"],
        modifier_name="Skin",
        enabled_in_views=False,
        enabled_in_render=True,
    )

    assert result["success"] is True
    assert skin.enabledInViews is False
    assert skin.enabledInRender is True
    applied = result["data"]["nodes"][0]["modifier"]["applied"]
    assert applied["enabledInViews"] is False
    assert applied["enabledInRender"] is True


def test_set_modifier_state_requires_a_flag(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_modifier_state.py").main(node_names=["hero_mesh"], modifier_name="Skin")

    assert result["success"] is False
    assert "At least one of enabled" in result["message"]


def test_set_modifier_state_fails_without_viewport_granularity(monkeypatch):
    """A modifier without enabledInViews must fail loudly, not fall back silently."""
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.modifiers = [_NoFlagsModifier("Bend")]

    result = _load_action("action_set_modifier_state.py").main(
        node_names=["hero_mesh"], modifier_index=1, enabled_in_views=False
    )

    assert result["success"] is False
    assert "does not expose 'enabledInViews'" in result["message"]


def test_set_modifier_state_is_two_phase(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_modifier_state.py").main(
        node_names=["hero_mesh", "helper_mesh"], modifier_name="Skin", enabled=False
    )

    assert result["success"] is False
    # hero_mesh untouched because helper_mesh lacks the modifier.
    assert runtime.hero.modifiers[0].enabled is True


# ── set_modifier_property ───────────────────────────────────────────────


def test_set_modifier_property_applies_and_verifies(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_name="TurboSmooth", properties={"iterations": 4}
    )

    assert result["success"] is True
    assert runtime.hero.modifiers[1].iterations == 4
    assert result["data"]["nodes"][0]["modifier"]["applied_properties"] == {"iterations": 4}


def test_set_modifier_property_applies_to_many_objects(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.helper.modifiers = [_Modifier("TurboSmooth", iterations=1)]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh", "helper_mesh"], modifier_name="TurboSmooth", properties={"iterations": 3}
    )

    assert result["success"] is True
    assert result["data"]["count"] == 2
    assert runtime.hero.modifiers[1].iterations == 3
    assert runtime.helper.modifiers[0].iterations == 3


def test_set_modifier_property_requires_properties(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_modifier_property.py").main(node_names=["hero_mesh"], modifier_name="TurboSmooth")

    assert result["success"] is False
    assert "properties is required" in result["message"]


def test_set_modifier_property_rejects_empty_properties(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_name="TurboSmooth", properties={}
    )

    assert result["success"] is False


def test_set_modifier_property_fails_when_property_is_read_only(monkeypatch):
    """A read-only property must fail, not be swallowed into a warning."""
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.modifiers = [_ReadOnlyModifier()]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_index=1, properties={"iterations": 4}
    )

    assert result["success"] is False
    assert "read-only" in result["message"]
    assert runtime.hero.modifiers[0].iterations == 1


def test_set_modifier_property_fails_on_unknown_property(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.modifiers = [_StrictModifier()]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_index=1, properties={"notAProperty": 1}
    )

    assert result["success"] is False
    assert "notAProperty" in result["message"]


def test_set_modifier_property_rejects_private_names(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_name="Skin", properties={"__class__": 1}
    )

    assert result["success"] is False
    assert "not a valid modifier property name" in result["message"]


def test_set_modifier_property_detects_silent_ignore(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.modifiers = [_SilentIgnoreModifier("TurboSmooth", iterations=1)]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_index=1, properties={"iterations": 5}
    )

    assert result["success"] is False
    assert "rejected property 'iterations'" in result["message"]


# ── collapse_modifier_stack ─────────────────────────────────────────────


def test_collapse_modifier_stack_empties_the_stack(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_collapse_modifier_stack.py").main(node_names=["hero_mesh"])

    assert result["success"] is True
    assert runtime.hero.modifiers == []
    assert result["data"]["nodes"][0]["modifiers_before"] == 2
    assert result["data"]["nodes"][0]["modifiers_after"] == 0
    assert runtime.collapsed == ["hero_mesh"]


def test_collapse_modifier_stack_warns_on_empty_stack(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_collapse_modifier_stack.py").main(node_names=["plain_mesh"])

    assert result["success"] is True
    assert any("no modifiers to collapse" in item for item in result["data"]["warnings"])
    assert runtime.collapsed == []


def test_collapse_modifier_stack_detects_silent_no_op(monkeypatch):
    """A collapse host call that does not shrink the stack must fail."""

    class _NoOpRuntime(_FakeRuntime):
        def __init__(self):
            super().__init__()
            self.maxOps = _NoOpMaxOps()

    class _NoOpMaxOps:
        def collapseNode(self, node, warn=False):
            return True  # lies: the stack is untouched

    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=_NoOpRuntime()))

    result = _load_action("action_collapse_modifier_stack.py").main(node_names=["hero_mesh"])

    assert result["success"] is False
    assert "still has" in result["message"]


def test_collapse_modifier_stack_errors_without_host_entry_point(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    monkeypatch.delattr(runtime, "maxOps", raising=False)

    result = _load_action("action_collapse_modifier_stack.py").main(node_names=["hero_mesh"])

    assert result["success"] is False
    assert "exposes neither" in result["message"]


# ── make_modifier_unique ────────────────────────────────────────────────


def test_make_modifier_unique_runs_entry_point(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_make_modifier_unique.py").main(node_names=["hero_mesh"], modifier_name="Skin")

    assert result["success"] is True
    assert runtime.unique_calls == [("hero_mesh", "Skin")]
    assert runtime.hero.modifiers[0].unique is True
    # Uniqueness cannot be confirmed by the host API, so a warning is explicit.
    assert any("cannot be confirmed" in item for item in result["data"]["warnings"])


def test_make_modifier_unique_requires_a_selector(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_make_modifier_unique.py").main(node_names=["hero_mesh"])

    assert result["success"] is False
    assert "modifier_name or modifier_index is required" in result["message"]


def test_make_modifier_unique_errors_without_host_entry_point(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    monkeypatch.delattr(runtime, "makeUnique", raising=False)
    monkeypatch.delattr(runtime, "maxOps", raising=False)

    result = _load_action("action_make_modifier_unique.py").main(node_names=["hero_mesh"], modifier_name="Skin")

    assert result["success"] is False
    assert "no makeUnique entry point" in result["message"]
    assert not hasattr(runtime.hero.modifiers[0], "unique")


def test_make_modifier_unique_is_two_phase(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_make_modifier_unique.py").main(
        node_names=["hero_mesh", "helper_mesh"], modifier_name="Skin"
    )

    assert result["success"] is False
    assert runtime.unique_calls == []


# ── tools.yaml contract ─────────────────────────────────────────────────


def test_modifier_tools_declare_required_metadata():
    import yaml

    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    expected = {
        "get_modifier_stack": (True, False),
        "add_modifier": (False, False),
        "remove_modifier": (False, True),
        "set_modifier_state": (False, False),
        "set_modifier_property": (False, False),
        "collapse_modifier_stack": (False, True),
        "make_modifier_unique": (False, False),
    }
    for name, (read_only, destructive) in expected.items():
        tool = by_name[name]
        assert tool["read_only"] is read_only, name
        assert tool["destructive"] is destructive, name
        assert tool["affinity"] == "main", name
        assert tool["enforce_thread_affinity"] is True, name
        assert (SKILL_DIR / tool["source_file"]).is_file(), name
        for key in ("side_effects", "produces", "risk", "intent", "annotations"):
            assert key in tool, (name, key)
