"""Tests for the bundled 3ds Max lookdev skill."""

from __future__ import annotations

import importlib.util
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

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return [float(x), float(y), float(z)]

    def OmniLight(self):  # noqa: N802 - mirrors pymxs runtime naming.
        node = _Node("light", self._next_handle, "OmniLight")
        self._next_handle += 1
        self.objects.append(node)
        return node


def _install_fake_pymxs(monkeypatch):
    runtime = _Runtime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
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
    assert [node.name for node in runtime.objects] == ["Shot_key", "Shot_fill", "Shot_rim"]
