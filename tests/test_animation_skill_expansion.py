"""Tests for expanded bundled 3ds Max animation tools."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-animation"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _AnimatedValue:
    def __init__(self, value):
        self.value = value
        self.controller = object()


class _FakeNode:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.isHidden = False
        self.parent = None
        self.position = _AnimatedValue([1.0, 2.0, 3.0])
        self.rotation = _AnimatedValue([0.0, 0.0, 0.0])
        self.scale = _AnimatedValue([1.0, 1.0, 1.0])
        self.keyframes = []


class _FakeRuntime:
    def __init__(self) -> None:
        self.hero = _FakeNode("hero_mesh", 42)
        self.objects = [self.hero]
        self.selection = [self.hero]
        self.currentTime = 1.0
        self.sliderTime = 1.0
        self.animationRangeStart = 1
        self.animationRangeEnd = 24
        self.frameRate = 30.0
        self.set_keys = []

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def setKey(self, node, frame, property_name, value):  # noqa: N802 - mirrors pymxs runtime naming.
        self.set_keys.append((node.name, frame, property_name, value))


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_animation_read_tools_return_time_controllers_and_empty_keys(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    time_settings = _load_action("action_get_time_settings.py").main()
    controllers = _load_action("action_get_animation_controllers.py").main(node_names=["hero_mesh"])
    keyframes = _load_action("action_list_keyframes.py").main(node_names=["hero_mesh"])

    assert time_settings["success"] is True
    assert time_settings["data"]["settings"]["frame_rate"] == 30.0
    assert controllers["data"]["nodes"][0]["controllers"]["position"] == "object"
    assert keyframes["data"]["nodes"][0]["count"] == 0


def test_timeline_mutations_update_runtime(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    current = _load_action("action_set_current_time.py").main(12)
    bad_timeline = _load_action("action_set_timeline_settings.py").main(start_frame=20, end_frame=10)
    timeline = _load_action("action_set_timeline_settings.py").main(start_frame=10, end_frame=20, frame_rate=24)

    assert current["success"] is True
    assert runtime.currentTime == 12.0
    assert bad_timeline["success"] is False
    assert timeline["success"] is True
    assert runtime.animationRangeStart == 10
    assert runtime.animationRangeEnd == 20
    assert runtime.frameRate == 24.0


def test_keyframe_workflow_and_curve_exchange_through_executor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    set_key = run_skill_script(
        str(SKILL_DIR / "action_set_transform_keyframe.py"),
        {"node_names": ["hero_mesh"], "frame": 5, "property": "position", "value": [10, 0, 0]},
    )
    interpolated = _load_action("action_set_key_interpolation.py").main(
        node_names=["hero_mesh"],
        frames=[5],
        interpolation="linear",
    )
    exported = _load_action("action_export_animation_curves.py").main(node_names=["hero_mesh"])
    deleted = _load_action("action_delete_keyframes.py").main(node_names=["hero_mesh"], frames=[5])

    assert set_key["success"] is True
    assert runtime.set_keys[0][0] == "hero_mesh"
    assert interpolated["data"]["changed_key_count"] == 1
    assert exported["data"]["curve_data"]["version"] == 1
    assert deleted["data"]["changed_key_count"] == 1
    assert runtime.hero.keyframes == []

    imported = _load_action("action_import_animation_curves.py").main(exported["data"]["curve_data"])

    assert imported["success"] is True
    assert imported["data"]["changed_key_count"] == 1
    assert runtime.hero.keyframes[0]["interpolation"] == "linear"


def test_bake_transform_animation_and_selection_errors(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    baked = _load_action("action_bake_transform_animation.py").main(
        node_names=["hero_mesh"], start_frame=1, end_frame=3, step=1
    )
    missing_keys = _load_action("action_delete_keyframes.py").main(node_names=["hero_mesh"], frames=[99])
    runtime.selection = []
    no_selection = _load_action("action_list_keyframes.py").main(use_selection=True)

    assert baked["success"] is True
    assert baked["data"]["changed_key_count"] == 9
    assert len(runtime.set_keys) == 9
    assert missing_keys["success"] is False
    assert missing_keys["data"]["changed_key_count"] == 0
    assert no_selection["success"] is False
    assert "selection is empty" in no_selection["message"]
