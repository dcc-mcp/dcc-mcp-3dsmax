"""Tests for the bundled 3ds Max selection set, group, and layer property tools."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

ROOT = Path(__file__).resolve().parents[1]
SCENE_DIR = ROOT / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scene"
DISPLAY_DIR = ROOT / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-display"


def _load_action(skill_dir: Path, script_name: str):
    path = skill_dir / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_org_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scene(script_name: str):
    return _load_action(SCENE_DIR, script_name)


def _display(script_name: str):
    return _load_action(DISPLAY_DIR, script_name)


# ── Fakes ──────────────────────────────────────────────────────────────


class _FakeNode:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.parent = None
        self.children = []
        self.isHidden = False
        self.isFrozen = False
        self.wireColor = [0, 0, 0]
        self.objectColor = [0, 0, 0]
        self.displayMode = "normal"
        self.groupOpen = False
        self.layer = None
        self.user_properties = {}


class _FakeLayer:
    """Fake display layer carrying the properties 3ds Max layers expose.

    Defaults are class attributes so a subclass can shadow one with a property
    that refuses or drops writes.
    """

    on = True
    isHidden = False
    isFrozen = False
    renderable = True
    castShadows = True
    receiveShadows = True
    motionBlur = "none"
    primaryVisibility = True
    secondaryVisibility = True
    visibleInReflections = True
    visibleInRefractions = True
    boxMode = False
    backCull = False
    allEdges = False
    ignoreExtents = False
    showTrajectory = False
    showFrozenInGray = True
    xray = False
    displayByLayer = False
    inheritVisibility = False
    wireColor = [255, 255, 255]

    def __init__(self, name: str) -> None:
        self.name = name
        self.nodes = []


class _SilentSelectionSets(dict):
    """Selection set container that accepts writes and drops them."""

    def __setitem__(self, key, value):  # noqa: D105 - mirrors a host that ignores the write.
        return None


class _LegacySelectionSets:
    """Manager-style container reachable only through the MAXScript accessors."""

    def __init__(self) -> None:
        self._sets = {}

    def __getitem__(self, key):
        return self._sets[key]

    def __setitem__(self, key, value):
        self._sets[key] = list(value)

    def _names(self):
        return list(self._sets)


class _FakeRuntime:
    def __init__(self, *, selection_sets=None) -> None:
        self.hero = _FakeNode("hero_mesh", 42)
        self.prop = _FakeNode("prop_mesh", 84)
        self.group_head = _FakeNode("hero_group", 126)
        self.objects = [self.hero, self.prop, self.group_head]
        self.selection = [self.hero]
        self.selectionSets = {} if selection_sets is None else selection_sets
        self.layers = {"Default": _FakeLayer("Default")}
        self.LayerManager = None
        self.ungrouped = []
        self.detached = []

    # ── node lookups ──
    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    # ── selection ──
    def clearSelection(self):  # noqa: N802 - mirrors pymxs runtime naming.
        self.selection = []

    def select(self, nodes):
        self.selection = list(nodes) if isinstance(nodes, (list, tuple)) else [nodes]

    def selectMore(self, nodes):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in nodes if isinstance(nodes, (list, tuple)) else [nodes]:
            if node not in self.selection:
                self.selection.append(node)

    # ── groups ──
    def ungroup(self, node):
        self.ungrouped.append(node.name)
        for child in list(getattr(node, "children", [])):
            child.parent = None
        node.children = []
        self.objects.remove(node)

    def setGroupOpen(self, node, open):  # noqa: N802 - mirrors pymxs runtime naming.
        node.groupOpen = bool(open)

    def isOpenGroupHead(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        return bool(getattr(node, "groupOpen", False))

    def attachToGroup(self, node, group):  # noqa: N802 - mirrors pymxs runtime naming.
        node.parent = group
        if node not in group.children:
            group.children.append(node)

    def detachFromGroup(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        self.detached.append(node.name)
        parent = getattr(node, "parent", None)
        if parent is not None and node in parent.children:
            parent.children.remove(node)
        node.parent = None


def _install_fake_pymxs(monkeypatch, runtime=None):
    runtime = runtime or _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


# ── Named selection sets ───────────────────────────────────────────────


def test_selection_set_tools_create_list_select_and_delete(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    created = _scene("action_create_selection_set.py").main("Heroes", node_names=["hero_mesh", "prop_mesh"])
    listed = _scene("action_list_selection_sets.py").main(include_nodes=True)
    selected = _scene("action_select_selection_set.py").main("Heroes")
    deleted = _scene("action_delete_selection_set.py").main("Heroes")

    assert created["success"] is True
    assert created["data"]["verified"] is True
    assert [entry["name"] for entry in listed["data"]["selection_sets"]] == ["Heroes"]
    assert listed["data"]["selection_sets"][0]["node_count"] == 2
    assert {node["node_name"] for node in listed["data"]["selection_sets"][0]["nodes"]} == {"hero_mesh", "prop_mesh"}
    assert [node.name for node in runtime.selection] == ["hero_mesh", "prop_mesh"]
    assert selected["data"]["verified"] is True
    assert deleted["data"]["changed_count"] == 1
    assert runtime.selectionSets == {}


def test_create_selection_set_from_current_selection(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    created = _scene("action_create_selection_set.py").main("Selected", use_selection=True)

    assert created["success"] is True
    assert [node.name for node in runtime.selectionSets["Selected"]] == ["hero_mesh"]


def test_create_selection_set_refuses_to_overwrite_without_replace_existing(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    _scene("action_create_selection_set.py").main("Heroes", node_names=["hero_mesh"])

    again = _scene("action_create_selection_set.py").main("Heroes", node_names=["prop_mesh"])

    assert again["success"] is False
    assert "replace_existing" in again["message"]
    assert [node.name for node in runtime.selectionSets["Heroes"]] == ["hero_mesh"]


def test_replace_selection_set_swaps_members(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    _scene("action_create_selection_set.py").main("Heroes", node_names=["hero_mesh"])

    replaced = _scene("action_replace_selection_set.py").main("Heroes", node_names=["prop_mesh"])

    assert replaced["success"] is True
    assert [node.name for node in runtime.selectionSets["Heroes"]] == ["prop_mesh"]


def test_replace_selection_set_reports_missing_set(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    missing = _scene("action_replace_selection_set.py").main("Nope", node_names=["hero_mesh"])

    assert missing["success"] is False
    assert "was not found" in missing["message"]


def test_select_selection_set_can_add_to_the_current_selection(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    _scene("action_create_selection_set.py").main("Props", node_names=["prop_mesh"])

    added = _scene("action_select_selection_set.py").main("Props", add=True)

    assert added["success"] is True
    assert [node.name for node in runtime.selection] == ["hero_mesh", "prop_mesh"]


def test_selection_set_tools_work_through_the_manager_api(monkeypatch):
    container = _LegacySelectionSets()
    runtime = _FakeRuntime(selection_sets=container)

    def get_named_sel_set_count():
        return len(container._names())

    def get_named_sel_set_name(index):
        return container._names()[index - 1]

    def delete_item(target, index):
        del container._sets[container._names()[index - 1]]

    runtime.getNamedSelSetCount = get_named_sel_set_count
    runtime.getNamedSelSetName = get_named_sel_set_name
    runtime.deleteItem = delete_item
    _install_fake_pymxs(monkeypatch, runtime)

    created = _scene("action_create_selection_set.py").main("Heroes", node_names=["hero_mesh"])
    listed = _scene("action_list_selection_sets.py").main()
    deleted = _scene("action_delete_selection_set.py").main("Heroes")

    assert created["success"] is True
    assert listed["data"]["count"] == 1
    assert deleted["success"] is True
    assert container._names() == []


def test_selection_set_write_is_reported_when_the_host_drops_it(monkeypatch):
    """A host that accepts the write without storing it must not report success."""
    _install_fake_pymxs(monkeypatch, _FakeRuntime(selection_sets=_SilentSelectionSets()))

    created = _scene("action_create_selection_set.py").main("Heroes", node_names=["hero_mesh"])

    assert created["success"] is False
    assert "Could not confirm" in created["message"]
    assert created["data"]["errors"]
    assert created["data"]["errors"][0]["message"] == "Selection set Heroes was not created"


def test_delete_selection_set_fails_when_the_host_keeps_it(monkeypatch):
    class _Sticky(dict):
        def __delitem__(self, key):  # noqa: D105 - mirrors a host that ignores the delete.
            return None

    runtime = _install_fake_pymxs(monkeypatch, _FakeRuntime(selection_sets=_Sticky()))
    runtime.selectionSets["Heroes"] = [runtime.hero]

    deleted = _scene("action_delete_selection_set.py").main("Heroes")

    assert deleted["success"] is False
    assert "still exists" in deleted["message"]


def test_selection_set_tools_report_missing_targets(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    no_nodes = _scene("action_create_selection_set.py").main("Empty")
    missing_set = _scene("action_select_selection_set.py").main("Nope")
    missing_delete = _scene("action_delete_selection_set.py").main("Nope")

    assert no_nodes["success"] is False
    assert "required" in no_nodes["message"]
    assert missing_set["success"] is False
    assert "was not found" in missing_set["message"]
    assert missing_delete["success"] is False


def test_list_selection_sets_reports_hosts_without_support(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    del runtime.selectionSets

    listed = _scene("action_list_selection_sets.py").main()

    assert listed["success"] is False
    assert "does not expose" in listed["message"]


# ── Groups ─────────────────────────────────────────────────────────────


def _make_group(runtime):
    runtime.hero.parent = runtime.group_head
    runtime.group_head.children.append(runtime.hero)
    return runtime.group_head


def test_group_tools_open_close_attach_detach_and_ungroup(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    _make_group(runtime)

    opened = _scene("action_set_group_open.py").main(open=True, group_name="hero_group")
    assert opened["success"] is True
    assert opened["data"]["verified"] is True
    assert runtime.group_head.groupOpen is True

    closed = _scene("action_set_group_open.py").main(open=False, handle=126)
    assert closed["data"]["open"] is False
    assert runtime.group_head.groupOpen is False

    detached = _scene("action_detach_from_group.py").main(node_names=["hero_mesh"])
    assert detached["data"]["applied"] == ["hero_mesh"]
    assert runtime.hero.parent is None

    attached = _scene("action_attach_to_group.py").main(group_name="hero_group", node_names=["hero_mesh"])
    assert attached["data"]["applied"] == ["hero_mesh"]
    assert runtime.hero.parent is runtime.group_head

    ungrouped = _scene("action_ungroup_nodes.py").main(node_names=["hero_group"])
    assert ungrouped["success"] is True
    assert runtime.group_head not in runtime.objects
    assert runtime.hero.parent is None


def test_ungroup_reports_group_that_survives(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    _make_group(runtime)
    runtime.ungroup = lambda node: None  # a host that accepts the call and does nothing

    result = _scene("action_ungroup_nodes.py").main(node_names=["hero_group"])

    assert result["success"] is False
    assert result["data"]["errors"]
    assert result["data"]["errors"][0]["target"] == "hero_group"


def test_set_group_open_fails_when_the_host_keeps_the_old_state(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.setGroupOpen = lambda node, open: None

    result = _scene("action_set_group_open.py").main(open=True, group_name="hero_group")

    assert result["success"] is False
    assert "open=" in result["message"]


def test_set_group_open_warns_when_it_cannot_be_verified(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.isOpenGroupHead = None  # a host that exposes neither the verifier call

    class _OpaqueGroup(_FakeNode):
        """Group head that takes the write but never reports its open state."""

        @property
        def groupOpen(self):  # noqa: D102 - mirrors a host with no readable open state.
            return None

        @groupOpen.setter
        def groupOpen(self, value):
            return None

    runtime.objects[2] = runtime.group_head = _OpaqueGroup("hero_group", 126)

    result = _scene("action_set_group_open.py").main(open=True, group_name="hero_group")

    assert result["success"] is True
    assert result["data"]["verified"] is False
    assert result["data"]["warnings"]


def test_attach_to_group_fails_when_the_host_ignores_it(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.attachToGroup = lambda node, group: None

    result = _scene("action_attach_to_group.py").main(group_name="hero_group", node_names=["hero_mesh"])

    assert result["success"] is False
    assert result["data"]["errors"][0]["target"] == "hero_mesh"


def test_detach_from_group_fails_when_the_parent_survives(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    _make_group(runtime)
    runtime.detachFromGroup = lambda node: None

    result = _scene("action_detach_from_group.py").main(node_names=["hero_mesh"])

    assert result["success"] is False
    assert "still reports" in result["data"]["errors"][0]["message"]


def test_group_tools_report_unresolved_nodes(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    missing_group = _scene("action_set_group_open.py").main(open=True)
    missing_node = _scene("action_detach_from_group.py").main(node_names=["ghost"])
    no_members = _scene("action_attach_to_group.py").main(group_name="hero_group")

    assert missing_group["success"] is False
    assert "required" in missing_group["message"]
    assert missing_node["success"] is False
    assert no_members["success"] is False


# ── Layer properties ───────────────────────────────────────────────────


def test_set_layer_properties_applies_and_reads_back(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    updated = _display("action_set_layer_properties.py").main(
        "Default",
        {
            "hidden": True,
            "frozen": True,
            "renderable": False,
            "box_mode": True,
            "cast_shadows": False,
            "motion_blur": "object",
            "color": [10, 20, 30],
        },
    )
    listed = _display("action_list_layers.py").main(include_properties=True)

    assert updated["success"] is True
    assert updated["data"]["applied_property_count"] == 7
    assert updated["data"]["errors"] == []
    layer = listed["data"]["layers"][0]
    assert layer["properties"]["hidden"] is True
    assert layer["properties"]["frozen"] is True
    assert layer["properties"]["renderable"] is False
    assert layer["properties"]["box_mode"] is True
    assert layer["properties"]["cast_shadows"] is False
    assert layer["properties"]["motion_blur"] == "object"
    assert layer["properties"]["color"] == [10, 20, 30]


def test_set_layer_properties_rejects_unknown_names(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _display("action_set_layer_properties.py").main("Default", {"radius": 5})

    assert result["success"] is False
    assert "Unknown layer properties" in result["message"]
    assert result["data"]["unknown"] == ["radius"]


def test_set_layer_properties_fail_when_the_host_refuses_a_value(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    class _RefusingLayer(_FakeLayer):
        @property
        def renderable(self):  # noqa: D102 - mirrors a host read-back that never matches.
            return True

        @renderable.setter
        def renderable(self, value):
            raise RuntimeError("renderable is read-only")

    runtime.layers["Default"] = _RefusingLayer("Default")

    result = _display("action_set_layer_properties.py").main("Default", {"renderable": False})

    assert result["success"] is False
    assert result["data"]["errors"]
    assert result["data"]["errors"][0]["property"] == "renderable"


def test_set_layer_properties_reports_a_host_that_silently_ignores_a_write(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    class _SilentLayer(_FakeLayer):
        def __setattr__(self, name, value):
            if name == "boxMode":
                return
            object.__setattr__(self, name, value)

    runtime.layers["Default"] = _SilentLayer("Default")

    result = _display("action_set_layer_properties.py").main("Default", {"box_mode": True})

    assert result["success"] is False
    assert result["data"]["errors"][0]["property"] == "box_mode"
    assert result["data"]["errors"][0]["actual"] is False


def test_set_layer_properties_reports_missing_layer(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    missing = _display("action_set_layer_properties.py").main("Nope", {"hidden": True})
    empty = _display("action_set_layer_properties.py").main("Default", {})

    assert missing["success"] is False
    assert "was not found" in missing["message"]
    assert empty["success"] is False


# ── Verified writes on node display state ──────────────────────────────


def test_node_display_state_write_fail_when_the_host_refuses_it(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    class _Refusing(_FakeNode):
        @property
        def isHidden(self):  # noqa: D102 - mirrors a host that never stores the write.
            return False

        @isHidden.setter
        def isHidden(self, value):
            return None

    runtime.objects[0] = runtime.hero = _Refusing("hero_mesh", 42)

    result = _display("action_set_node_display_state.py").main(node_names=["hero_mesh"], hidden=True)

    assert result["success"] is False
    assert result["data"]["errors"][0]["property"] == "hidden"


def test_selection_write_is_verified(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    selected = _scene("action_set_selection.py").main(node_names=["hero_mesh", "prop_mesh"])

    assert selected["success"] is True
    assert selected["data"]["verified"] is True
    assert [node.name for node in runtime.selection] == ["hero_mesh", "prop_mesh"]

    runtime.select = lambda nodes: None  # a host that accepts the call and changes nothing
    rejected = _scene("action_set_selection.py").main(node_names=["hero_mesh", "prop_mesh"])

    assert rejected["success"] is False
    assert rejected["data"]["errors"]


def test_group_tools_require_explicit_targets(monkeypatch):
    """Mutating group tools never fall back to the whole scene."""
    runtime = _install_fake_pymxs(monkeypatch)
    _make_group(runtime)

    ungroup = _scene("action_ungroup_nodes.py").main()
    detach = _scene("action_detach_from_group.py").main()
    attach = _scene("action_attach_to_group.py").main(group_name="hero_group")
    open_group = _scene("action_set_group_open.py").main(open=True)

    assert ungroup["success"] is False
    assert "required" in ungroup["message"]
    assert detach["success"] is False
    assert attach["success"] is False
    assert open_group["success"] is False
    assert runtime.group_head in runtime.objects
