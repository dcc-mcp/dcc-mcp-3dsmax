"""Tests for the bundled 3ds Max lookdev skill."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

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


class _Runtime:
    def __init__(self) -> None:
        self.objects = []
        self._next_handle = 10
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


def _install_fake_pymxs(monkeypatch):
    runtime = _Runtime()
    monkeypatch.setitem(
        sys.modules,
        "pymxs",
        types.SimpleNamespace(runtime=runtime),
    )
    return runtime


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
