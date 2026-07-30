"""Tests for the bundled 3ds Max scene and object skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scene"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Point:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


class _BoxObject:
    pass


class _TargetCamera:
    pass


class _FakeUnits:
    def SystemType(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return "centimeters"


class _FakeNode:
    def __init__(
        self,
        name: str,
        handle: int,
        *,
        base_object=None,
        hidden: bool = False,
        minimum: tuple[float, float, float] = (0.0, 0.0, 0.0),
        maximum: tuple[float, float, float] = (1.0, 1.0, 1.0),
    ) -> None:
        self.name = name
        self.handle = handle
        self.baseObject = base_object or _BoxObject()
        self.isHidden = hidden
        self.parent = None
        self.min = _Point(*minimum)
        self.max = _Point(*maximum)


class _FakeRuntime:
    def __init__(self) -> None:
        self.hero = _FakeNode("hero_box", 42, minimum=(-1.0, -2.0, -3.0), maximum=(1.0, 2.0, 3.0))
        self.camera = _FakeNode("main_camera", 84, base_object=_TargetCamera())
        self.hidden = _FakeNode("hidden_helper", 99, hidden=True)
        self.duplicate_a = _FakeNode("duplicate_name", 100)
        self.duplicate_b = _FakeNode("duplicate_name", 101)
        self.objects = [self.hero, self.camera, self.hidden, self.duplicate_a, self.duplicate_b]
        self.selection = [self.hero]
        self.maxFileName = "demo_scene.max"
        self.maxFilePath = "/projects/demo/"
        self.units = _FakeUnits()
        self.centered = []
        self.frozen = []
        self.executed = []
        self.saved = []

    def maxVersion(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return (26000, 0)

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def clearSelection(self):  # noqa: N802 - mirrors pymxs runtime naming.
        self.selection = []

    def select(self, nodes):
        if isinstance(nodes, list):
            self.selection = list(nodes)
        else:
            self.selection = [nodes]

    def selectMore(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        if node not in self.selection:
            self.selection.append(node)

    def copy(self, node):
        copied = _FakeNode("{}_raw_copy".format(node.name), max(item.handle for item in self.objects) + 1)
        copied.parent = node.parent
        self.objects.append(copied)
        return copied

    def delete(self, nodes):
        if isinstance(nodes, list):
            for node in list(nodes):
                self.delete(node)
            return None
        if nodes in self.objects:
            self.objects.remove(nodes)
        if nodes in self.selection:
            self.selection.remove(nodes)
        return None

    def group(self, nodes, name="Group"):
        group = _FakeNode(name, max(item.handle for item in self.objects) + 1)
        self.objects.append(group)
        for node in nodes:
            node.parent = group
        return group

    def centerPivot(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        self.centered.append(node.name)

    def freezeTransform(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        self.frozen.append(node.name)

    def execute(self, script):
        self.executed.append(script)
        return True

    def saveMaxFile(self, file_path, quiet=True):  # noqa: N802 - mirrors pymxs runtime naming.
        self.saved.append((file_path, quiet))
        return True


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_scene_read_tools_return_atomic_node_metadata(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    nodes = _load_action("action_list_scene_nodes.py").main(include_hidden=False)
    cameras = _load_action("action_list_cameras.py").main()
    selection = _load_action("action_get_selection.py").main()
    metadata = _load_action("action_get_scene_metadata.py").main()
    bounding_box = _load_action("action_get_bounding_box.py").main(handle=runtime.hero.handle)

    assert nodes["success"] is True
    assert [node["node_name"] for node in nodes["data"]["nodes"]] == [
        "hero_box",
        "main_camera",
        "duplicate_name",
        "duplicate_name",
    ]
    assert cameras["data"]["cameras"][0]["node_name"] == "main_camera"
    assert selection["data"]["nodes"][0]["object_id"] == 42
    assert metadata["data"]["scene_name"] == "demo_scene.max"
    assert metadata["data"]["3dsmax_version"] == "2024"
    assert bounding_box["data"]["min"] == [-1.0, -2.0, -3.0]
    assert bounding_box["data"]["max"] == [1.0, 2.0, 3.0]


def test_node_resolution_reports_missing_and_ambiguous_errors(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    action = _load_action("action_get_bounding_box.py")

    missing = action.main(node_name="not_here")
    ambiguous = action.main(node_name="duplicate_name")

    assert missing["success"] is False
    assert "No matching node" in missing["message"]
    assert ambiguous["success"] is False
    assert "ambiguous" in ambiguous["message"]
    assert len(ambiguous["data"]["matches"]) == 2


def test_scene_mutation_tools_update_fake_runtime(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    set_selection = _load_action("action_set_selection.py").main
    duplicate_nodes = _load_action("action_duplicate_nodes.py").main
    set_visibility = _load_action("action_set_visibility.py").main
    parent_node = _load_action("action_parent_node.py").main
    unparent_node = _load_action("action_unparent_node.py").main
    group_nodes = _load_action("action_group_nodes.py").main
    center_pivots = _load_action("action_center_pivots.py").main
    freeze_transforms = _load_action("action_freeze_transforms.py").main
    delete_nodes = _load_action("action_delete_nodes.py").main

    selected = set_selection(node_names=["main_camera"], replace=True)
    extended = set_selection(handles=[42], replace=False)
    duplicated = duplicate_nodes(node_names=["hero_box"], name_suffix="_dupe")
    hidden = set_visibility(node_names=["hero_box"], visible=False)
    parented = parent_node(child_name="hero_box", parent_name="main_camera")
    unparented = unparent_node(node_name="hero_box")
    grouped = group_nodes(handles=[42, 84], group_name="HeroGroup")
    centered = center_pivots(node_names=["hero_box"])
    frozen = freeze_transforms(node_names=["hero_box"])
    deleted = delete_nodes(node_names=["hidden_helper"])

    assert selected["success"] is True
    assert [node.name for node in runtime.selection] == ["main_camera", "hero_box"]
    assert extended["data"]["count"] == 1
    assert duplicated["data"]["nodes"][0]["node_name"] == "hero_box_dupe"
    assert hidden["data"]["nodes"][0]["visible"] is False
    assert parented["data"]["parent"]["node_name"] == "main_camera"
    assert unparented["data"]["previous_parent"]["node_name"] == "main_camera"
    assert grouped["data"]["group"]["node_name"] == "HeroGroup"
    assert runtime.hero.parent.name == "HeroGroup"
    assert centered["success"] is True and runtime.centered == ["hero_box"]
    assert frozen["success"] is True and runtime.frozen == ["hero_box"]
    assert deleted["success"] is True
    assert all(node.name != "hidden_helper" for node in runtime.objects)


def test_save_scene_uses_native_max_file_api(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    output = tmp_path / "lookdev.max"

    result = _load_action("action_save_scene.py").main(file_path=str(output))

    assert result["success"] is True
    assert runtime.saved == [(str(output.resolve()), True)]


def test_scene_skill_runs_through_adapter_executor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_set_visibility.py"), {"handles": [runtime.hero.handle], "visible": False}
    )

    assert result["success"] is True
    assert runtime.hero.isHidden is True
