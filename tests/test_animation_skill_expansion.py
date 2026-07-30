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
        self.animationRange = types.SimpleNamespace(start=1, end=24)
        self.frameRate = 30.0
        self.set_keys = []

    def Interval(self, start, end):  # noqa: N802 - mirrors pymxs runtime naming.
        return types.SimpleNamespace(start=start, end=end)

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
    assert runtime.animationRange.start == 10
    assert runtime.animationRange.end == 20
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


def test_transform_keys_use_pymxs_animation_context_instead_of_runtime_setkey(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    events = []

    class _Context:
        def __init__(self, label):
            self.label = label

        def __enter__(self):
            events.append(("enter", self.label))

        def __exit__(self, *_args):
            events.append(("exit", self.label))

    pymxs = sys.modules["pymxs"]
    pymxs.animate = lambda enabled: _Context(("animate", enabled))
    pymxs.attime = lambda frame: _Context(("attime", frame))
    runtime.setKey = types.SimpleNamespace(commitBuffer=lambda: None)
    runtime.Point3 = lambda *values: list(values)
    runtime.EulerAngles = lambda *values: list(values)

    direct = _load_action("action_set_keyframe.py").main(
        node_name="hero_mesh", time=1, property="position", value=[4, 5, 6]
    )
    result = _load_action("action_bake_transform_animation.py").main(
        node_names=["hero_mesh"], start_frame=1, end_frame=1, step=1
    )

    assert direct["success"] is True, direct
    assert result["success"] is True, result
    assert ("enter", ("animate", True)) in events
    assert ("enter", ("attime", 1)) in events


def test_interpolation_and_delete_update_native_controller_keys(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    keys = [types.SimpleNamespace(time=0.0), types.SimpleNamespace(time=360.0)]
    components = {name: types.SimpleNamespace(keys=list(keys), properties={}) for name in ("x", "y", "z")}
    rotation = types.SimpleNamespace(keys=[], properties=components)
    runtime.hero.controller = types.SimpleNamespace(properties={"rotation": rotation})
    runtime.hero.keyframes = [
        {"frame": frame, "property": "rotation", "value": [0, 0, frame], "interpolation": None}
        for frame in (0.0, 360.0)
    ]
    runtime.Name = str
    runtime.ticksPerFrame = 160
    runtime.getPropertyController = lambda controller, name: controller.properties.get(str(name))
    runtime.getPropNames = lambda controller: list(controller.properties)
    runtime.numKeys = lambda controller: len(controller.keys)
    runtime.getKeyTime = lambda controller, index: controller.keys[index - 1].time * runtime.ticksPerFrame
    runtime.getKey = lambda controller, index: controller.keys[index - 1]
    runtime.setInTangentType = lambda key, tangent: setattr(key, "in_tangent", tangent)
    runtime.setOutTangentType = lambda key, tangent: setattr(key, "out_tangent", tangent)
    runtime.deleteKey = lambda controller, index: controller.keys.pop(index - 1)

    interpolated = _load_action("action_set_key_interpolation.py").main(
        node_names=["hero_mesh"], interpolation="linear"
    )
    listed = _load_action("action_list_keyframes.py").main(node_names=["hero_mesh"])
    deleted = _load_action("action_delete_keyframes.py").main(
        node_names=["hero_mesh"], properties=["rotation"]
    )

    assert interpolated["data"]["changes"][0]["native_changed_key_count"] == 6
    assert listed["data"]["nodes"][0]["count"] == 2
    assert all(key.in_tangent == key.out_tangent == "linear" for component in components.values() for key in keys)
    assert deleted["data"]["changes"][0]["native_changed_key_count"] == 6
    assert all(not component.keys for component in components.values())
