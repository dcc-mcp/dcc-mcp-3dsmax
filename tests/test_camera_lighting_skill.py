"""Tests for the bundled 3ds Max camera and lighting skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

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
        self.constructed = []
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
        # 3ds Max reports the scene's render aperture width (mm) through the
        # documented getRendApertureWidth() global; 36 mm is the host default,
        # but the value is user-configurable (see the F2 aperture tests below).
        self.render_aperture_width = 36.0

    def getRendApertureWidth(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self.render_aperture_width

    def _make_node(self, name: str, class_name: str):
        node = _FakeNode(name, self._next_handle, class_name)
        self._next_handle += 1
        self.constructed.append(node)
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


def test_requested_lens_rejects_cached_unsupported_aliases(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.isProperty = lambda node, name: False
    result = _load_action("action_create_camera.py").main(name="unsupported_lens", focal_length=65)
    assert result["success"] is False
    assert result["data"]["field"] == "focal_length"
    assert result["data"]["actual"] is None
    assert result["data"]["supported"] is False


def test_requested_lens_uses_native_getter_not_cached_alias(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.isProperty = lambda node, name: name == "focal_length_mm"
    runtime.getProperty = lambda node, name: 40.0
    result = _load_action("action_create_camera.py").main(name="ignored_lens", camera_type="physical", focal_length=65)
    assert result["success"] is False
    assert result["data"]["actual"] == 40
    assert result["data"]["requested"] == 65


def test_physical_camera_verified_native_focal_length(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.isProperty = lambda node, name: name == "focal_length_mm"
    runtime.getProperty = lambda node, name: getattr(node, name)
    result = _load_action("action_create_camera.py").main(name="verified_lens", camera_type="physical", focal_length=65)
    assert result["success"] is True
    assert result["data"]["camera"]["focal_length"] == 65
    listed = _load_action("action_list_cameras.py").main()
    assert next(c for c in listed["data"]["cameras"] if c["node"]["node_name"] == "verified_lens")["focal_length"] == 65


def test_physical_camera_uses_native_physical_constructor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.Physical = lambda: runtime._make_node("camera", "Physical")
    runtime.PhysicalCamera = None
    result = _load_action("action_create_camera.py").main(name="native_physical", camera_type="physical")
    assert result["success"] is True
    assert runtime.getNodeByName("native_physical").className == "Physical"


def test_failed_requested_lens_rolls_back_only_owned_camera_and_target(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    deleted = []
    runtime.isProperty = lambda node, name: False
    runtime.delete = deleted.append
    runtime.maxOps = types.SimpleNamespace(getNodeByHandle=lambda handle: None)
    result = _load_action("action_create_camera.py").main(
        name="unsupported_lens", focal_length=65, target_position=[0, 0, 0]
    )
    assert result["success"] is False
    assert result["data"]["rolled_back"] is True
    assert result["data"]["changed_node_count"] == 0
    assert {node.name for node in deleted} == {"unsupported_lens", "unsupported_lens.Target"}
    assert all(node in runtime.objects for node in (runtime.camera, runtime.light, runtime.mesh))


def test_invalid_focal_length_is_rejected_before_constructing_nodes(monkeypatch):
    """A non-numeric lens must fail closed without leaking camera/target nodes."""
    runtime = _install_fake_pymxs(monkeypatch)
    from dcc_mcp_3dsmax._camera_light_utils import create_camera

    result = create_camera(
        runtime,
        name="bad_lens",
        focal_length="65mm",
        target_position=[0, 0, 0],
    )

    assert result["success"] is False
    assert result["data"]["field"] == "focal_length"
    # Validation happens before any constructor, so nothing can leak.
    assert runtime.constructed == []
    assert runtime.getNodeByName("bad_lens") is None


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


def test_standard_camera_focal_length_maps_to_native_fov(monkeypatch):
    """A target/free camera controls its lens through fov, not the mm aliases.

    Requesting focal_length on the default target camera used to fail native
    readback and delete the freshly created camera. Now the millimetre value is
    converted to a horizontal field of view and verified through the host.
    """
    import math

    runtime = _install_fake_pymxs(monkeypatch)
    # Model a native standard camera: only `fov` is a real property; the mm
    # aliases do not exist, so writing them directly would fail readback.
    runtime.isProperty = lambda node, name: name == "fov"
    runtime.getProperty = lambda node, name: getattr(node, name, None)

    result = _load_action("action_create_camera.py").main(
        name="standard_cam", camera_type="target", focal_length=65
    )

    assert result["success"] is True, result
    # The millimetre value the caller asked for is reported back, not the fov.
    assert result["data"]["camera"]["focal_length"] == 65
    camera = runtime.getNodeByName("standard_cam")
    expected_fov = math.degrees(2.0 * math.atan(36.0 / (2.0 * 65)))
    assert camera.fov == pytest.approx(expected_fov)
    # list_cameras must agree with create_camera for the same node: the mm value
    # is derivable from the native fov, not lost because standard cameras lack
    # the mm aliases a Physical camera exposes.
    listed = _load_action("action_list_cameras.py").main()
    row = next(c for c in listed["data"]["cameras"] if c["node"]["node_name"] == "standard_cam")
    assert row["focal_length"] == pytest.approx(65, abs=1e-6)


def test_physical_camera_still_verifies_millimetre_focal_length(monkeypatch):
    """The Physical camera path still uses and verifies the mm lens property."""
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.isProperty = lambda node, name: name == "focal_length_mm"
    runtime.getProperty = lambda node, name: getattr(node, name)

    result = _load_action("action_create_camera.py").main(
        name="physical_cam", camera_type="physical", focal_length=50
    )

    assert result["success"] is True, result
    assert result["data"]["camera"]["focal_length"] == 50
    assert runtime.getNodeByName("physical_cam").focal_length_mm == 50


# ── F2: aperture-aware mm <-> FOV conversion ──────────────────────────────


def test_standard_camera_fov_uses_live_render_aperture(monkeypatch):
    """A non-default aperture must change the mm -> fov conversion (F2).

    The old code hard-coded a 36 mm sensor width. With a 24 mm render aperture
    the resulting fov (and the lens shown in the Max UI) was wrong even though
    the tool reported success with the requested millimetres.
    """
    import math

    runtime = _install_fake_pymxs(monkeypatch)
    runtime.render_aperture_width = 24.0
    runtime.isProperty = lambda node, name: name == "fov"
    runtime.getProperty = lambda node, name: getattr(node, name, None)

    result = _load_action("action_create_camera.py").main(
        name="narrow_gate", camera_type="target", focal_length=65
    )

    assert result["success"] is True, result
    assert result["data"]["camera"]["focal_length"] == 65
    camera = runtime.getNodeByName("narrow_gate")
    expected_fov = math.degrees(2.0 * math.atan(24.0 / (2.0 * 65)))
    assert camera.fov == pytest.approx(expected_fov)
    assert camera.fov != pytest.approx(math.degrees(2.0 * math.atan(36.0 / (2.0 * 65))))
    # Both directions use the same live aperture: list_cameras reports 65 mm.
    listed = _load_action("action_list_cameras.py").main()
    row = next(c for c in listed["data"]["cameras"] if c["node"]["node_name"] == "narrow_gate")
    assert row["focal_length"] == pytest.approx(65, abs=1e-6)


def test_list_cameras_focal_length_uses_live_render_aperture(monkeypatch):
    """The fov -> mm direction must use the live aperture, not a fixed 36 (F2)."""
    import math

    runtime = _install_fake_pymxs(monkeypatch)
    runtime.render_aperture_width = 24.0
    cam = runtime.camera
    cam.focalLength = None
    fov = math.degrees(2.0 * math.atan(24.0 / (2.0 * 65)))
    cam.fov = fov

    listed = _load_action("action_list_cameras.py").main()
    row = next(c for c in listed["data"]["cameras"] if c["node"]["node_name"] == "main_camera")

    assert row["focal_length"] == pytest.approx(65, abs=1e-6)
    # A hard-coded 36 mm aperture would have over-reported the lens here.
    assert row["focal_length"] != pytest.approx(36.0 / (2.0 * math.tan(math.radians(fov) / 2.0)), abs=1e-6)


def test_standard_camera_fails_closed_without_aperture_capability(monkeypatch):
    """A host that cannot report its aperture must not fake a 36 mm lens (F2)."""
    runtime = _install_fake_pymxs(monkeypatch)
    monkeypatch.setattr(runtime, "getRendApertureWidth", None)

    result = _load_action("action_create_camera.py").main(
        name="no_gate", camera_type="target", focal_length=65
    )

    assert result["success"] is False
    assert result["data"]["field"] == "focal_length"
    # Validation happens before construction: no node leaks and no guessed fov.
    assert runtime.constructed == []


@pytest.mark.parametrize("bad", [None, 0.0, -1.0, "wide", float("inf"), float("nan")])
def test_standard_camera_fails_closed_on_invalid_aperture(monkeypatch, bad):
    """A non-positive / non-finite / non-numeric aperture fails closed (F2)."""
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.render_aperture_width = bad

    result = _load_action("action_create_camera.py").main(
        name="bad_gate", camera_type="target", focal_length=65
    )

    assert result["success"] is False
    assert result["data"]["field"] == "focal_length"
    assert runtime.constructed == []


def test_default_aperture_matches_36mm_formula(monkeypatch):
    """With the host default 36 mm aperture, the documented formula is unchanged."""
    import math

    runtime = _install_fake_pymxs(monkeypatch)
    assert runtime.getRendApertureWidth() == 36.0
    runtime.isProperty = lambda node, name: name == "fov"
    runtime.getProperty = lambda node, name: getattr(node, name, None)

    result = _load_action("action_create_camera.py").main(
        name="default_gate", camera_type="target", focal_length=65
    )

    assert result["success"] is True, result
    camera = runtime.getNodeByName("default_gate")
    assert camera.fov == pytest.approx(math.degrees(2.0 * math.atan(36.0 / (2.0 * 65))))


def test_physical_camera_does_not_require_aperture_capability(monkeypatch):
    """A Physical camera uses the mm alias and must not need the aperture API (F2)."""
    runtime = _install_fake_pymxs(monkeypatch)
    monkeypatch.setattr(runtime, "getRendApertureWidth", None)
    runtime.isProperty = lambda node, name: name == "focal_length_mm"
    runtime.getProperty = lambda node, name: getattr(node, name)

    result = _load_action("action_create_camera.py").main(
        name="physical_no_gate", camera_type="physical", focal_length=50
    )

    assert result["success"] is True, result
    assert result["data"]["camera"]["focal_length"] == 50
    assert runtime.getNodeByName("physical_no_gate").focal_length_mm == 50
