"""Tests for the bundled 3ds Max display and custom property skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-display"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Layer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.nodes = []
        self.hidden = False
        self.frozen = False

    def addNode(self, node):  # noqa: N802 - mirrors pymxs layer naming.
        if node not in self.nodes:
            self.nodes.append(node)


class _LayerManager:
    def __init__(self, runtime: "_FakeRuntime") -> None:
        self.runtime = runtime

    @property
    def count(self) -> int:
        return len(self.runtime.layers)

    def getLayer(self, index):  # noqa: N802 - mirrors pymxs layer naming.
        return list(self.runtime.layers.values())[index]

    def getLayerFromName(self, name):  # noqa: N802 - mirrors pymxs layer naming.
        return self.runtime.layers.get(name)

    def newLayerFromName(self, name):  # noqa: N802 - mirrors pymxs layer naming.
        layer = _Layer(name)
        self.runtime.layers[name] = layer
        return layer

    def deleteLayerByName(self, name):  # noqa: N802 - mirrors pymxs layer naming.
        self.runtime.layers.pop(name, None)


class _FakeNode:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.parent = None
        self.isHidden = False
        self.isFrozen = False
        self.wireColor = [1, 2, 3]
        self.objectColor = [4, 5, 6]
        self.displayMode = "normal"
        self.layer = None
        self.user_properties = {"asset_type": "prop"}


class _FakeRuntime:
    def __init__(self) -> None:
        self.hero = _FakeNode("hero_mesh", 42)
        self.camera = _FakeNode("camera_main", 84)
        self.objects = [self.hero, self.camera]
        self.selection = [self.hero]
        self.layers = {"Default": _Layer("Default")}
        self.LayerManager = _LayerManager(self)
        self.layers["Default"].addNode(self.hero)
        self.hero.layer = "Default"

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_layer_tools_create_assign_list_and_delete(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    created = _load_action("action_create_layer.py").main("Characters")
    assigned = _load_action("action_assign_nodes_to_layer.py").main(layer_name="Characters", node_names=["hero_mesh"])
    listed = _load_action("action_list_layers.py").main(include_nodes=True)

    assert created["success"] is True
    assert assigned["data"]["changed_node_count"] == 1
    assert runtime.hero.layer == "Characters"
    assert any(layer["name"] == "Characters" and layer["node_count"] == 1 for layer in listed["data"]["layers"])

    deleted = _load_action("action_delete_layer.py").main("Characters")

    assert deleted["data"]["changed_layer_count"] == 1
    assert runtime.hero.layer is None
    assert "Characters" not in runtime.layers


def test_display_state_tools_update_nodes_through_executor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    updated = run_skill_script(
        str(SKILL_DIR / "action_set_node_display_state.py"),
        {
            "node_names": ["hero_mesh"],
            "hidden": True,
            "frozen": True,
            "wire_color": [10, 20, 30],
            "object_color": [40, 50, 60],
            "display_mode": "wireframe",
        },
    )
    state = _load_action("action_list_node_display_state.py").main(node_names=["hero_mesh"])

    assert updated["success"] is True
    assert runtime.hero.isHidden is True
    assert runtime.hero.isFrozen is True
    assert state["data"]["nodes"][0]["wire_color"] == [10, 20, 30]
    assert state["data"]["nodes"][0]["object_color"] == [40, 50, 60]
    assert state["data"]["nodes"][0]["display_mode"] == "wireframe"


def test_custom_property_tools_list_get_set_and_delete(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    listed = _load_action("action_list_custom_properties.py").main(node_names=["hero_mesh"])
    got = _load_action("action_get_custom_property.py").main(property_name="asset_type", node_names=["hero_mesh"])
    set_prop = _load_action("action_set_custom_property.py").main(
        property_name="lod", value=2, node_names=["hero_mesh"]
    )
    deleted = _load_action("action_delete_custom_property.py").main(
        property_name="asset_type", node_names=["hero_mesh"]
    )

    assert listed["data"]["nodes"][0]["properties"]["asset_type"] == "prop"
    assert got["data"]["properties"][0]["value"] == "prop"
    assert set_prop["data"]["changed_property_count"] == 1
    assert runtime.hero.user_properties["lod"] == 2
    assert deleted["data"]["changed_property_count"] == 1
    assert "asset_type" not in runtime.hero.user_properties


def test_display_tools_report_explicit_target_errors(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    no_target = _load_action("action_set_node_display_state.py").main(hidden=True)
    missing_prop = _load_action("action_get_custom_property.py").main(property_name="missing", node_names=["hero_mesh"])
    missing_layer = _load_action("action_delete_layer.py").main("NotThere")

    assert no_target["success"] is False
    assert "required" in no_target["message"]
    assert missing_prop["success"] is False
    assert missing_layer["success"] is False
