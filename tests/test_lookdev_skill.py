"""Tests for the bundled 3ds Max lookdev skill."""

from __future__ import annotations

import importlib.util
import os
import struct
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-lookdev"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Node:
    def __init__(self, name: str, handle: int, class_name: str) -> None:
        self.name = name
        self.handle = handle
        self.className = class_name
        self.parent = None
        self.isHidden = False
        self.position = [0.0, 0.0, 0.0]
        self.target_position = None
        self.multiplier = None
        self.color = None
        self.castShadows = False
        self.enabled = True


class _MaxOps:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def getNodeByHandle(self, handle):  # noqa: N802 - mirrors the native maxOps API.
        self.runtime.looked_up_handles.append(handle)
        if self.runtime.handle_lookup_raises:
            raise RuntimeError("native handle lookup failure")
        return next((node for node in self.runtime.objects if node.handle == handle), None)


class _Runtime:
    def __init__(self) -> None:
        self.objects = []
        self._next_handle = 10
        self.delete_is_noop = False
        self.handle_lookup_raises = False
        self.deleted_handles = []
        self.looked_up_handles = []
        self.maxOps = _MaxOps(self)
        self.environmentMap = None
        self.environmentMapOn = False
        self.environmentMapAmount = 0.0
        self.environmentMapAngle = 0.0
        self.ColorPipelineMgr = _ColorPipelineManager()
        self.ticksPerFrame = 160
        self._keys = []
        self._controller = None

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return [float(x), float(y), float(z)]

    def OmniLight(self):  # noqa: N802 - mirrors pymxs runtime naming.
        node = _Node("light", self._next_handle, "OmniLight")
        self._next_handle += 1
        self.objects.append(node)
        return node

    def delete(self, node):
        self.deleted_handles.append(node.handle)
        if not self.delete_is_noop:
            self.objects.remove(node)

    def BitmapTexture(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return types.SimpleNamespace(filename="", coords=types.SimpleNamespace(U_Offset=0.0))

    def Name(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
        if value == "OCIO_EnvVar":
            self.ColorPipelineMgr.OCIOConfigPath = os.environ["OCIO"]
        return value

    def getPropertyController(self, _owner, _name):  # noqa: N802
        return self._controller

    def Bezier_Float(self):  # noqa: N802
        return self._keys

    def setPropertyController(self, _owner, _name, controller):  # noqa: N802
        self._controller = controller
        return True

    def addNewKey(self, controller, frame):  # noqa: N802
        key = types.SimpleNamespace(time=frame)
        controller.append(key)
        return key

    def numKeys(self, controller):  # noqa: N802
        return len(controller)

    def getKeyTime(self, controller, index):  # noqa: N802
        return controller[index - 1].time * self.ticksPerFrame

    def getKey(self, controller, index):  # noqa: N802
        return controller[index - 1]

    def setInTangentType(self, key, tangent):  # noqa: N802
        key.in_tangent = tangent

    def setOutTangentType(self, key, tangent):  # noqa: N802
        key.out_tangent = tangent


class _ColorPipelineManager:
    def __init__(self) -> None:
        self.Mode = "Gamma"
        self.OCIOConfigPath = ""
        self.RenderingColorSpace = "scene-linear Rec.709-sRGB"
        self.DataColorSpace = "raw"
        self.Status = "Normal"
        self.Locked = True

    def ReInitialize(self):  # noqa: N802 - mirrors MAXScript interface naming.
        return True


class _ArnoldRenderer:
    pass


class _MaxColor:
    def __init__(self, red, green, blue):
        self.r = red
        self.g = green
        self.b = blue

    def __len__(self):
        return 3

    def __getitem__(self, index):
        return (self.r, self.g, self.b)[index]


class _StrictArnoldNode(_Node):
    @property
    def color(self):
        return self._native_color

    @color.setter
    def color(self, value):
        if value is not None and not isinstance(value, _MaxColor):
            raise TypeError("Arnold color requires a host-native Color")
        self._native_color = value


class _Float32ArnoldNode(_StrictArnoldNode):
    @property
    def intensity(self):
        return self._intensity

    @intensity.setter
    def intensity(self, value):
        self._intensity = struct.unpack("f", struct.pack("f", float(value)))[0]


class _BadColorNode(_StrictArnoldNode):
    @property
    def color(self):
        return _MaxColor(0, 0, 0)

    @color.setter
    def color(self, value):
        self._native_color = value


def _install_fake_pymxs(monkeypatch):
    runtime = _Runtime()
    monkeypatch.setitem(
        sys.modules,
        "pymxs",
        types.SimpleNamespace(runtime=runtime),
    )
    return runtime


def _configure_autoregistered_arnold_failure(runtime):
    runtime.renderers = types.SimpleNamespace(current=_ArnoldRenderer())
    runtime.color = lambda red, green, blue: _MaxColor(red, green, blue)
    created_count = 0

    def arnold_light():
        nonlocal created_count
        node_type = _BadColorNode if created_count == 1 else _StrictArnoldNode
        node = node_type("light", runtime._next_handle, "Arnold_Light")
        runtime._next_handle += 1
        created_count += 1
        node.cast_shadows = False
        runtime.objects.append(node)
        return node

    runtime.Arnold_Light = arnold_light
    runtime.classOf = lambda value: value.className


def test_setup_hdr_lighting_sets_environment_and_rig(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(
        hdri_path=str(hdri_path), name_prefix="Shot", target_position=[0, 0, 0]
    )

    assert result["success"] is True
    assert result["data"]["hdri_path"] == str(hdri_path)
    assert result["data"]["bitmap"]["path"] == str(hdri_path)
    assert runtime.environmentMapOn is True
    assert runtime.environmentMap.coords.U_Offset == 0.0
    assert [node.name for node in runtime.objects] == ["Shot_key", "Shot_fill", "Shot_rim"]


def test_setup_hdr_lighting_fails_closed_before_mutation_when_arnold_lights_are_unavailable(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.renderers = types.SimpleNamespace(current=_ArnoldRenderer())
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    assert result["success"] is False
    assert result["data"]["renderer_family"] == "arnold"
    assert result["data"]["failure_reason"] == "compatible_light_factory_unavailable"
    assert runtime.environmentMap is None
    assert runtime.objects == []


def test_setup_hdr_lighting_uses_and_verifies_available_arnold_light_capability(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.renderers = types.SimpleNamespace(current=_ArnoldRenderer())

    def arnold_light():
        node = _Node("light", runtime._next_handle, "Arnold_Light")
        runtime._next_handle += 1
        node.cast_shadows = False
        return node

    runtime.Arnold_Light = arnold_light
    runtime.classOf = lambda value: value.className
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(
        hdri_path=str(hdri_path), name_prefix="ArnoldReview", target_position=[0, 0, 0]
    )

    assert result["success"] is True
    assert result["data"]["renderer_family"] == "arnold"
    assert result["data"]["light_compatibility"] == "verified"
    assert [node.className for node in runtime.objects] == ["Arnold_Light"] * 3
    assert [node.intensity for node in runtime.objects] == [1.0, 0.35, 0.65]
    assert all(node.cast_shadows is True for node in runtime.objects)


def test_setup_hdr_lighting_converts_and_reads_back_native_arnold_colors(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.renderers = types.SimpleNamespace(current=_ArnoldRenderer())
    runtime.color = lambda red, green, blue: _MaxColor(red, green, blue)

    def arnold_light():
        node = _StrictArnoldNode("light", runtime._next_handle, "Arnold_Light")
        runtime._next_handle += 1
        node.cast_shadows = False
        return node

    runtime.Arnold_Light = arnold_light
    runtime.classOf = lambda value: value.className
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    assert result["success"] is True
    assert [row["color"] for row in result["data"]["lights"]] == [
        [255, 244, 230],
        [190, 210, 255],
        [255, 255, 255],
    ]
    assert all(isinstance(node.color, _MaxColor) for node in runtime.objects)


def test_setup_hdr_lighting_rolls_back_when_native_arnold_color_cannot_be_applied(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.renderers = types.SimpleNamespace(current=_ArnoldRenderer())

    def arnold_light():
        node = _StrictArnoldNode("light", runtime._next_handle, "Arnold_Light")
        runtime._next_handle += 1
        return node

    runtime.Arnold_Light = arnold_light
    runtime.classOf = lambda value: value.className
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "renderer_light_readback_failed"
    assert result["data"]["rig"]["data"]["changed_node_count"] == 0
    assert runtime.objects == []
    assert runtime.environmentMap is None


def test_setup_hdr_lighting_reports_incomplete_rollback_after_silent_delete_noop(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.delete_is_noop = True
    _configure_autoregistered_arnold_failure(runtime)
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    rollback = result["data"]["rig"]["data"]
    assert result["success"] is False
    assert rollback["rolled_back"] is False
    assert rollback["changed_node_count"] == 2
    assert rollback["rollback_incomplete_handles"] == [10, 11]
    assert [row["node"]["object_id"] for row in rollback["lights"]] == [10, 11]
    assert [node.handle for node in runtime.objects] == [10, 11]
    assert runtime.deleted_handles == [10, 11]
    assert runtime.looked_up_handles == [10, 11]


def test_setup_hdr_lighting_reports_unverified_rollback_when_handle_query_raises(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.handle_lookup_raises = True
    _configure_autoregistered_arnold_failure(runtime)
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    rollback = result["data"]["rig"]["data"]
    assert result["success"] is False
    assert rollback["rolled_back"] is False
    assert rollback["changed_node_count"] == 2
    assert rollback["rollback_incomplete_handles"] == [10, 11]
    assert [row["node"]["object_id"] for row in rollback["lights"]] == [10, 11]
    assert runtime.objects == []
    assert runtime.deleted_handles == [10, 11]
    assert runtime.looked_up_handles == [10, 11]


def test_setup_hdr_lighting_verifies_failing_and_earlier_owned_nodes_are_absent(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    _configure_autoregistered_arnold_failure(runtime)
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    rollback = result["data"]["rig"]["data"]
    assert result["success"] is False
    assert rollback["rolled_back"] is True
    assert rollback["changed_node_count"] == 0
    assert rollback["lights"] == []
    assert rollback["rollback_incomplete_handles"] == []
    assert runtime.objects == []
    assert runtime.deleted_handles == [10, 11]
    assert runtime.looked_up_handles == [10, 11]


def test_setup_hdr_lighting_rollback_preserves_preexisting_same_name_light(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.renderers = types.SimpleNamespace(current=_ArnoldRenderer())
    runtime.color = lambda red, green, blue: _MaxColor(red, green, blue)
    runtime._next_handle = 101
    original = _Node("Review_key", 1, "Arnold_Light")
    runtime.objects.append(original)
    _configure_autoregistered_arnold_failure(runtime)
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    assert result["success"] is False
    rollback = result["data"]["rig"]["data"]
    assert rollback["rolled_back"] is True
    assert rollback["changed_node_count"] == 0
    assert rollback["rollback_incomplete_handles"] == []
    assert runtime.objects == [original]
    assert original.handle == 1
    assert runtime.deleted_handles == [101, 102]
    assert runtime.looked_up_handles == [101, 102]


def test_setup_hdr_lighting_accepts_host_float_precision_readback(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.renderers = types.SimpleNamespace(current=_ArnoldRenderer())
    runtime.color = lambda red, green, blue: _MaxColor(red, green, blue)
    runtime.Name = lambda value: value
    runtime.isProperty = lambda _node, name: (
        name
        in {
            "position",
            "on",
            "intensity",
            "color",
            "cast_shadows",
        }
    )

    def arnold_light():
        node = _Float32ArnoldNode("light", runtime._next_handle, "Arnold_Light")
        runtime._next_handle += 1
        node.cast_shadows = False
        return node

    runtime.Arnold_Light = arnold_light
    runtime.classOf = lambda value: value.className
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")

    result = _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    assert result["success"] is True
    assert [row["intensity"] for row in result["data"]["lights"]] == pytest.approx([1.0, 0.35, 0.65])


def test_set_hdri_rotation_updates_native_uv_offset(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")
    _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    result = _load_action("action_set_hdri_rotation.py").main(rotation=180)

    assert result["success"] is True
    assert result["data"]["u_offset"] == 0.5
    assert runtime.environmentMap.coords.U_Offset == 0.5


def test_set_hdri_rotation_inserts_linear_unwrapped_key(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    hdri_path = tmp_path / "studio.hdr"
    hdri_path.write_text("hdr", encoding="utf-8")
    _load_action("action_setup_hdr_lighting.py").main(hdri_path=str(hdri_path))

    result = _load_action("action_set_hdri_rotation.py").main(rotation=360, frame=359)

    assert result["success"] is True
    assert result["data"]["u_offset"] == 1.0
    assert result["data"]["interpolation"] == "linear"
    assert runtime._keys[0].in_tangent == "linear"
    assert runtime._keys[0].out_tangent == "linear"


def test_set_color_management_applies_custom_ocio_and_reports_readback(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch)
    config = tmp_path / "studio.ocio"
    config.write_text("ocio_profile_version: 2", encoding="utf-8")

    result = _load_action("action_set_color_management.py").main(ocio_config_path=str(config))

    assert result["success"] is True
    assert result["data"]["mode"] == "OCIO_EnvVar"
    assert result["data"]["rendering_color_space"] == "ACEScg"
    assert result["data"]["locked"] is True
