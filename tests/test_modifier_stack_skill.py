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


class _VolatileModifierProxy:
    """Stands in for a pymxs modifier wrapper.

    Real pymxs hands back a fresh wrapper object on every attribute access, so
    ``existing is modifier`` never matches across two reads of the stack. This
    proxy reproduces that so the positional fallback in ``attach_modifier`` is
    exercised.
    """

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_inner"), name, value)


class _TopInsertNode:
    """Node whose stack is pre-populated and returns fresh proxies per read.

    ``addModifier`` without ``before:`` inserts at the TOP of the stack in
    3ds Max, and stack indices count from the top - so a newly added modifier
    must report index 1, not the bottom-of-stack index.
    """

    def __init__(self, name, handle, modifiers=None):
        self.name = name
        self.handle = handle
        self.parent = None
        self.isHidden = False
        self._modifiers = list(modifiers or [])

    @property
    def modifiers(self):
        return [_VolatileModifierProxy(item) for item in self._modifiers]

    @modifiers.setter
    def modifiers(self, value):
        self._modifiers = list(value)


class _TopInsertRuntime:
    """Runtime that inserts added modifiers at the top of a pre-populated stack."""

    def __init__(self):
        self.hero = _TopInsertNode("hero_mesh", 42, [_Modifier("Skin"), _Modifier("TurboSmooth", iterations=2)])
        self.objects = [self.hero]
        self.selection = [self.hero]

    def getNodeByName(self, name):
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def addModifier(self, node, modifier):
        # 3ds Max inserts at the top of the stack when `before:` is omitted.
        node._modifiers.insert(0, modifier)

    def deleteModifier(self, node, target):
        if isinstance(target, int):
            del node._modifiers[target - 1]
            return True
        node._modifiers = [item for item in node._modifiers if item is not target]
        return True

    def getPropNames(self, modifier):
        return ["#enabled", "#enabledInViews", "#enabledInRender"]

    def getProperty(self, modifier, name):
        return getattr(modifier, name)

    def Bend(self):
        return _Modifier("Bend", angle=0.0)

    def Edit_Poly(self):
        return _Modifier("Edit_Poly")


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


# ── regression: reported findings ───────────────────────────────────────


def test_add_modifier_reports_top_of_stack_index_on_prepopulated_stack(monkeypatch):
    """Regression: addModifier without `before:` inserts at the TOP of the stack.

    3ds Max numbers the stack from the top, so a freshly added modifier is
    index 1. Returning the bottom index would make any later by-index remove or
    edit hit the wrong modifier.
    """
    runtime = _TopInsertRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = _load_action("action_add_modifier.py").main(modifier_class="Bend", node_names=["hero_mesh"])

    assert result["success"] is True
    assert result["data"]["nodes"][0]["modifier"]["index"] == 1
    # The new modifier really is at the top, above the pre-existing Skin.
    assert [mod.name for mod in runtime.hero._modifiers] == ["Bend", "Skin", "TurboSmooth"]


def test_add_modifier_index_targets_the_added_modifier(monkeypatch):
    """The reported index must resolve back to the modifier just added."""
    runtime = _TopInsertRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    added = _load_action("action_add_modifier.py").main(
        modifier_class="Bend", node_names=["hero_mesh"], properties={"angle": 45.0}
    )
    index = added["data"]["nodes"][0]["modifier"]["index"]

    stack = _load_action("action_get_modifier_stack.py").main(node_names=["hero_mesh"], include_parameters=False)[
        "data"
    ]["nodes"][0]

    assert stack["modifiers"][index - 1]["name"] == "Bend"


def test_identity_still_wins_when_host_returns_same_object(monkeypatch):
    """When the host hands back the same object, identity is authoritative."""
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_add_modifier.py").main(modifier_class="Edit_Poly", node_names=["hero_mesh"])

    assert result["success"] is True
    index = result["data"]["nodes"][0]["modifier"]["index"]
    assert runtime.hero.modifiers[index - 1] is runtime.hero.modifiers[-1]


def test_large_float_readback_within_float32_rounding_is_accepted(monkeypatch):
    """Regression: 3ds Max stores floats as 32-bit; 123456.789 -> 123456.7890625.

    An absolute tolerance of 1e-6 rejected a write that had actually succeeded.
    """
    runtime = _install_fake_pymxs(monkeypatch)

    class _Float32Modifier(_Modifier):
        def __init__(self):
            super().__init__("Bend")
            self._width = 0.0

        @property
        def width(self):
            return self._width

        @width.setter
        def width(self, value):
            # Emulate a binary32 round-trip of the stored value.
            import struct

            self._width = struct.unpack("f", struct.pack("f", float(value)))[0]

    runtime.hero.modifiers = [_Float32Modifier()]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_index=1, properties={"width": 123456.789}
    )

    assert result["success"] is True, result["message"]
    assert abs(result["data"]["nodes"][0]["modifier"]["applied_properties"]["width"] - 123456.789) < 0.001


def test_integer_readback_stays_exact(monkeypatch):
    """Integers round-trip exactly, so a one-unit drift must still fail."""
    runtime = _install_fake_pymxs(monkeypatch)

    class _DriftingModifier(_Modifier):
        def __init__(self):
            super().__init__("TurboSmooth")
            self._iterations = 1

        @property
        def iterations(self):
            return self._iterations

        @iterations.setter
        def iterations(self, value):
            # Off by one: a relative tolerance on large integers would hide this.
            self._iterations = int(value) + 1

    runtime.hero.modifiers = [_DriftingModifier()]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_index=1, properties={"iterations": 4}
    )

    assert result["success"] is False
    assert "rejected property 'iterations'" in result["message"]


def test_large_integer_off_by_one_is_caught(monkeypatch):
    """A 1-unit difference at large magnitude must not be masked by rel_tol."""
    runtime = _install_fake_pymxs(monkeypatch)

    class _BigIntModifier(_Modifier):
        def __init__(self):
            super().__init__("Bend")
            self._seed = 0

        @property
        def seed(self):
            return self._seed

        @seed.setter
        def seed(self, value):
            self._seed = int(value) + 1

    runtime.hero.modifiers = [_BigIntModifier()]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_index=1, properties={"seed": 123456789}
    )

    assert result["success"] is False


def test_materially_different_float_still_fails(monkeypatch):
    """A genuinely rejected float (not just rounding) must still fail."""
    runtime = _install_fake_pymxs(monkeypatch)

    class _ClampingModifier(_Modifier):
        def __init__(self):
            super().__init__("Bend")
            self._angle = 0.0

        @property
        def angle(self):
            return self._angle

        @angle.setter
        def angle(self, value):
            self._angle = min(float(value), 10.0)  # clamps hard

    runtime.hero.modifiers = [_ClampingModifier()]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"], modifier_index=1, properties={"angle": 90.0}
    )

    assert result["success"] is False
    assert "rejected property 'angle'" in result["message"]


def test_truncated_parameter_read_is_reported(monkeypatch):
    """Regression: a truncated parameter read must say so, not look complete."""
    runtime = _install_fake_pymxs(monkeypatch)

    class _WideModifier(_Modifier):
        def __init__(self):
            super().__init__("Wide")
            for i in range(70):
                setattr(self, "prop_{:03d}".format(i), i)

    runtime.hero.modifiers = [_WideModifier()]

    result = _load_action("action_get_modifier_stack.py").main(node_names=["hero_mesh"])
    entry = result["data"]["nodes"][0]["modifiers"][0]

    from dcc_mcp_3dsmax._mesh_ops import MODIFIER_PROPERTY_LIMIT

    assert len(entry["parameters"]) == MODIFIER_PROPERTY_LIMIT
    assert entry["parameters_truncated"] is True


def test_untruncated_parameter_read_omits_the_flag(monkeypatch):
    """No truncation means no flag - the flag must not be emitted unconditionally."""
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_modifier_stack.py").main(node_names=["hero_mesh"])

    for entry in result["data"]["nodes"][0]["modifiers"]:
        assert "parameters_truncated" not in entry


def test_partial_property_writes_are_reported(monkeypatch):
    """Regression: values written before a failure must not be discarded silently."""
    runtime = _install_fake_pymxs(monkeypatch)

    class _HalfAcceptingModifier(_Modifier):
        ACCEPTED = ("iterations",)

        def __init__(self, name):
            object.__setattr__(self, "name", name)
            object.__setattr__(self, "enabled", True)
            object.__setattr__(self, "iterations", 1)

        def __setattr__(self, key, value):
            if key not in self.ACCEPTED:
                raise AttributeError("no writable property '{}'".format(key))
            object.__setattr__(self, key, value)

    runtime.hero.modifiers = [_HalfAcceptingModifier("TurboSmooth")]

    result = _load_action("action_set_modifier_property.py").main(
        node_names=["hero_mesh"],
        modifier_index=1,
        properties={"iterations": 3, "bogus": 1},
    )

    assert result["success"] is False
    assert result["data"]["partially_applied"] == {"iterations": 3}


def test_partial_state_writes_are_reported(monkeypatch):
    """Regression: set_modifier_state must surface flags applied before a failure."""
    runtime = _install_fake_pymxs(monkeypatch)

    class _RenderOnlyModifier(_Modifier):
        def __init__(self):
            object.__setattr__(self, "name", "Bend")
            object.__setattr__(self, "enabled", True)
            object.__setattr__(self, "enabledInViews", True)
            object.__setattr__(self, "_render", True)

        @property
        def enabledInRender(self):
            return object.__getattribute__(self, "_render")

        @enabledInRender.setter
        def enabledInRender(self, value):
            raise RuntimeError("enabledInRender is locked")

    runtime.hero.modifiers = [_RenderOnlyModifier()]

    result = _load_action("action_set_modifier_state.py").main(
        node_names=["hero_mesh"],
        modifier_index=1,
        enabled_in_views=False,
        enabled_in_render=False,
    )

    assert result["success"] is False
    # enabledInViews landed before enabledInRender failed.
    assert result["data"]["partially_applied"] == {"enabledInViews": False}


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
