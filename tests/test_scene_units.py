"""Native-name, readback and restoration contracts for empty-scene units."""

import importlib.util
import struct
import sys
import types
from pathlib import Path

import pytest


class NativeName:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return "#" + self.value


class Units:
    def __init__(self, mode="normal"):
        self.values = {"SystemType": NativeName("inches"), "SystemScale": 1.0, "DisplayType": NativeName("generic")}
        self.writes = []
        self.mode = mode
        self.fail_restore = False

    def __getattr__(self, key):
        return self.values[key]

    def __setattr__(self, key, value):
        if key not in ("SystemType", "SystemScale"):
            object.__setattr__(self, key, value)
            return
        self.writes.append((key, value))
        if key == "SystemType":
            assert isinstance(value, NativeName), "native setters require Name values"
            if self.fail_restore and value.value == "inches":
                raise RuntimeError("restore refused")
        if key == "SystemScale" and value == 2.0:
            if self.mode == "ignore":
                return
            if self.mode in ("raise", "broken-restore"):
                self.fail_restore = self.mode == "broken-restore"
                raise RuntimeError("scale write refused")
        if key == "SystemScale" and self.mode == "float32":
            value = struct.unpack("f", struct.pack("f", value))[0]
        self.values[key] = value


@pytest.fixture
def action(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "src/dcc_mcp_3dsmax/skills/3dsmax-scene/action_set_scene_units.py"
    spec = importlib.util.spec_from_file_location("scene_units_test_action", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = types.SimpleNamespace(units=Units(), objects=[], Name=NativeName)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return module.main, runtime


def test_sets_verified_system_units_without_touching_display(action):
    main, runtime = action
    result = main("millimeters")
    assert result["success"] and result["data"]["verified"]
    assert result["data"]["after"] == {"system_unit": "millimeters", "system_scale": 1.0}
    assert str(runtime.units.DisplayType) == "#generic"
    assert not result["data"]["rescaled_objects"]


@pytest.mark.parametrize("scale", [True, 0, -1, float("nan"), float("inf"), "1"])
def test_invalid_scale_does_not_write(action, scale):
    main, runtime = action
    assert not main("millimeters", scale)["success"]
    assert not runtime.units.writes


def test_unknown_unit_does_not_write(action):
    main, runtime = action
    assert not main("invalid")["success"]
    assert not runtime.units.writes


def test_populated_scene_is_refused_without_writes(action):
    main, runtime = action
    runtime.objects.append(object())
    result = main("millimeters")
    assert not result["success"] and result["data"]["failure_reason"] == "scene_not_empty"
    assert not runtime.units.writes


@pytest.mark.parametrize("mode", ["ignore", "raise"])
def test_partial_or_silently_ignored_write_restores_original_units(action, mode):
    main, runtime = action
    runtime.units = Units(mode)
    result = main("millimeters", 2.0)
    assert not result["success"]
    assert result["data"]["rollback_verified"]
    assert str(runtime.units.SystemType) == "#inches" and runtime.units.SystemScale == 1.0


def test_restoration_failure_is_reported(action):
    main, runtime = action
    runtime.units = Units("broken-restore")
    result = main("millimeters", 2.0)
    assert not result["success"] and not result["data"]["rollback_verified"]
    assert result["data"]["restore_errors"] == [{"field": "SystemType", "error": "restore refused"}]
    assert result["data"]["restored"]["system_unit"] == "millimeters"


def test_native_float32_scale_readback_is_accepted(action):
    main, runtime = action
    runtime.units = Units("float32")
    assert main("millimeters", 0.1)["success"]
