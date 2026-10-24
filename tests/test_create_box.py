"""Native parameter and readback contract for box creation."""

from __future__ import annotations

import struct
import sys
import types
from pathlib import Path

import pytest

from dcc_mcp_3dsmax._executor import run_skill_script

ACTION = Path(__file__).resolve().parents[1] / "src/dcc_mcp_3dsmax/skills/3dsmax-modeling/action_create_box.py"


class _Runtime:
    def __init__(self, *, ignored_length=False, unreadable_length=False, fail_create=False):
        self.box = None
        self.ignored_length = ignored_length
        self.unreadable_length = unreadable_length
        self.fail_create = fail_create

    def Box(self, *, width, height, length):
        if self.fail_create:
            raise RuntimeError("Box creation failed")
        self.box = types.SimpleNamespace(name="Box001", handle=42)
        for key, value in (("width", width), ("height", height), ("length", length)):
            setattr(self.box, key, struct.unpack("f", struct.pack("f", value))[0])
        if self.ignored_length:
            self.box.length = 25.0
        if self.unreadable_length:
            del self.box.length
        return self.box

    @staticmethod
    def Point3(x, y, z):
        return (x, y, z)


def _run(monkeypatch, runtime, **args):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return run_skill_script(str(ACTION), args)


@pytest.mark.parametrize("dimensions", [(200, 200, 200), (12.25, 4.5, 0.125), (0.1, 1e7 + 0.1, 1e-8)])
def test_box_maps_depth_to_native_length_and_returns_native_values(monkeypatch, dimensions):
    runtime = _Runtime()
    width, height, depth = dimensions
    result = _run(monkeypatch, runtime, width=width, height=height, depth=depth, name="test_box", position=[1, 2, 3])

    assert result["success"] is True
    assert result["data"] == {
        "node_name": "test_box",
        "object_id": 42,
        "width": runtime.box.width,
        "height": runtime.box.height,
        "depth": runtime.box.length,
        "position": [1, 2, 3],
    }
    assert runtime.box.pos == (1, 2, 3)


def test_box_reports_ignored_native_length_as_failure(monkeypatch):
    runtime = _Runtime(ignored_length=True)
    result = _run(monkeypatch, runtime, depth=200)

    assert result["success"] is False
    assert result["data"]["object_id"] == 42
    assert result["data"]["depth"] == 25
    assert result["data"]["requested"]["depth"] == 200
    assert runtime.box is not None


def test_box_reports_unreadable_dimensions_with_created_node_identity(monkeypatch):
    result = _run(monkeypatch, _Runtime(unreadable_length=True))

    assert result["success"] is False
    assert result["data"]["object_id"] == 42
    assert "read" in result["message"].lower()


@pytest.mark.parametrize("dimension", ["width", "height", "depth"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "10cm"])
def test_box_rejects_invalid_dimensions_before_creation(monkeypatch, dimension, value):
    runtime = _Runtime()
    result = _run(monkeypatch, runtime, **{dimension: value})

    assert result["success"] is False
    assert dimension in result["message"]
    assert runtime.box is None


def test_box_propagates_native_creation_failure(monkeypatch):
    runtime = _Runtime(fail_create=True)
    result = _run(monkeypatch, runtime)

    assert result["status"] == "error"
    assert result["message"] == "Box creation failed"
    assert runtime.box is None
