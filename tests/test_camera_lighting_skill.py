"""Tests for the bundled 3ds Max camera and lighting skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-camera-lighting"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Viewport:
    def __init__(self) -> None:
        self.camera = None

    def setCamera(self, camera):  # noqa: N802 - mirrors pymxs viewport naming.
        self.camera = camera


class _FakeNode:
    def __init__(self, name: str, handle: int, class_name: str) -> None:
        self.name = name
        self.handle = handle
        self.className = class_name
        self.parent = None
        self.isHidden = False
        self.position = [0.0, 0.0, 0.0]
        self.enabled = True
        self.target = None
        self.target_position = None
        self.focalLength = None
        self.multiplier = None
        self.color = None
        self.castShadows = False
        self.is_camera = "Camera" in class_name
        self.is_light = "Light" in class_name or "Spot" in class_name


class _FakeRuntime:
    def __init__(self) -> None:
        self._next_handle = 100
        self.camera = _FakeNode("main_camera", 42, "TargetCamera")
        self.camera.position = [1.0, 2.0, 3.0]
        self.camera.focalLength = 35
        self.light = _FakeNode("key_light", 84, "OmniLight")
        self.light.multiplier = 1.0
        self.light.color = [255, 240, 220]
        self.mesh = _FakeNode("hero_mesh", 21, "EditablePoly")
        self.objects = [self.camera, self.light, self.mesh]
        self.viewport = _Viewport()
        self.activeCamera = None
        self.renderCamera = None

    def _make_node(self, name: str, class_name: str):
        node = _FakeNode(name, self._next_handle, class_name)
        self._next_handle += 1
        return node

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return [float(x), float(y), float(z)]

    def Targetcamera(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("camera", "TargetCamera")

    def Targetobject(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("target", "TargetObject")

    def FreeCamera(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("camera", "FreeCamera")

    def PhysicalCamera(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("camera", "PhysicalCamera")

    def OmniLight(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("light", "OmniLight")

    def FreeSpot(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("spot", "FreeSpot")


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_camera_and_light_read_tools_list_key_properties(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    cameras = _load_action("action_list_cameras.py").main()
    lights = _load_action("action_list_lights.py").main()

    assert cameras["data"]["count"] == 1
    assert cameras["data"]["cameras"][0]["node"]["node_name"] == "main_camera"
    assert cameras["data"]["cameras"][0]["focal_length"] == 35.0
    assert lights["data"]["count"] == 1
    assert lights["data"]["lights"][0]["color"] == [255, 240, 220]


def test_camera_workflow_creates_and_sets_active_camera(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    created = run_skill_script(
        str(SKILL_DIR / "action_create_camera.py"),
        {
            "name": "review_camera",
            "camera_type": "physical",
            "position": [10, 20, 30],
            "target_position": [0, 0, 0],
            "focal_length": 50,
        },
    )
    active = _load_action("action_set_active_camera.py").main(camera_name="review_camera")

    assert created["success"] is True
    assert runtime.getNodeByName("review_camera").position == [10.0, 20.0, 30.0]
    assert active["data"]["changed_camera_count"] == 1
    assert runtime.renderCamera.name == "review_camera"
    assert runtime.viewport.camera.name == "review_camera"


def test_target_camera_creates_and_connects_native_target(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    created = _load_action("action_create_camera.py").main(
        name="shot_camera",
        camera_type="target",
        position=[5, -8, 4],
        target_position=[0, 0, 1],
    )

    camera = runtime.getNodeByName("shot_camera")
    assert created["success"] is True
    assert created["data"]["changed_node_count"] == 2
    assert camera.target is runtime.getNodeByName("shot_camera.Target")
    assert camera.target.position == [0.0, 0.0, 1.0]


def test_active_camera_uses_runtime_class_for_pymxs_wrapper(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.camera.className = "MXSWrapperBase"
    runtime.camera.is_camera = False
    runtime.classOf = lambda node: "TargetCamera" if node is runtime.camera else node.className

    active = _load_action("action_set_active_camera.py").main(camera_name="main_camera")

    assert active["success"] is True
    assert runtime.renderCamera is runtime.camera


def test_light_workflow_creates_updates_and_builds_three_point_rig(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    created = _load_action("action_create_light.py").main(
        name="fill_light",
        light_type="spot",
        position=[0, -10, 10],
        intensity=0.5,
        color=[128, 160, 255],
        shadows=True,
    )
    updated = _load_action("action_set_light_properties.py").main(
        light_name="fill_light", enabled=False, intensity=0.25
    )
    rig = _load_action("action_create_three_point_light_rig.py").main(
        name_prefix="Shot", target_position=[0, 0, 0], distance=50
    )

    assert created["data"]["light"]["intensity"] == 0.5
    assert runtime.getNodeByName("fill_light").castShadows is True
    assert updated["data"]["changed_light_count"] == 1
    assert runtime.getNodeByName("fill_light").enabled is False
    assert rig["data"]["changed_node_count"] == 3
    assert runtime.getNodeByName("Shot_key") is not None
    assert runtime.getNodeByName("Shot_fill") is not None
    assert runtime.getNodeByName("Shot_rim") is not None


def test_camera_lighting_tools_report_missing_and_wrong_target_errors(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    missing_camera = _load_action("action_set_active_camera.py").main(camera_name="missing")
    wrong_camera = _load_action("action_set_active_camera.py").main(camera_name="hero_mesh")
    wrong_light = _load_action("action_set_light_properties.py").main(light_name="hero_mesh", intensity=2)

    assert missing_camera["success"] is False
    assert "resolve" in missing_camera["message"]
    assert wrong_camera["success"] is False
    assert "not a camera" in wrong_camera["message"]
    assert wrong_light["success"] is False
    assert "not a light" in wrong_light["message"]
