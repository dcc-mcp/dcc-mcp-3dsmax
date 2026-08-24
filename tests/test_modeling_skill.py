"""Contract tests for the bundled 3ds Max modeling skill."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import yaml

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-modeling"


class _Point3:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class _SplineShape:
    def __init__(self) -> None:
        self.name = "Shape001"
        self.handle = 42
        self.splines = []
        self.modifiers = []


class _Lathe:
    def __init__(self) -> None:
        self.degrees = 360.0
        self.segs = 16
        self.weldCore = True
        self.flipNormals = False


class _FakeRuntime:
    def __init__(self, *, tamper_modifier: bool = False, fail_add_modifier: bool = False) -> None:
        self.shape = None
        self.updated = False
        self.tamper_modifier = tamper_modifier
        self.fail_add_modifier = fail_add_modifier
        self.deleted = []

    @staticmethod
    def Name(value):  # noqa: N802 - mirrors pymxs runtime naming.
        return value

    @staticmethod
    def Point3(x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Point3(x, y, z)

    def SplineShape(self):  # noqa: N802 - mirrors pymxs runtime naming.
        self.shape = _SplineShape()
        return self.shape

    @staticmethod
    def addNewSpline(shape):  # noqa: N802 - mirrors pymxs runtime naming.
        shape.splines.append([])

    @staticmethod
    def addKnot(shape, spline_index, knot_type, segment_type, point):  # noqa: N802 - mirrors pymxs naming.
        assert knot_type == "corner"
        assert segment_type == "line"
        shape.splines[spline_index - 1].append(point)

    def updateShape(self, shape):  # noqa: N802 - mirrors pymxs runtime naming.
        assert shape is self.shape
        self.updated = True

    @staticmethod
    def Lathe():  # noqa: N802 - mirrors pymxs runtime naming.
        return _Lathe()

    def addModifier(self, shape, modifier):  # noqa: N802 - mirrors pymxs runtime naming.
        if self.fail_add_modifier:
            raise RuntimeError("native modifier failure")
        shape.modifiers.append(modifier)
        if self.tamper_modifier:
            modifier.segs = 8

    def delete(self, node):
        self.deleted.append(node)
        if node is self.shape:
            self.shape = None

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        if self.shape is not None and self.shape.name == name:
            return self.shape
        return None

    @staticmethod
    def numKnots(shape, spline_index):  # noqa: N802 - mirrors pymxs runtime naming.
        return len(shape.splines[spline_index - 1])


def test_lathe_profile_runs_through_executor_and_reads_back_native_state(monkeypatch):
    """The public executor path creates and verifies a bounded native Lathe stack."""
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {
            "profile_points": [[0.0, 0.0], [12.0, 0.0], [8.0, 20.0], [0.0, 20.0]],
            "name": "lathed_vase",
            "degrees": 270.0,
            "segments": 24,
            "weld_core": False,
            "flip_normals": True,
        },
    )

    assert result["success"] is True
    assert runtime.updated is True
    assert [[point.x, point.y, point.z] for point in runtime.shape.splines[0]] == [
        [0.0, 0.0, 0.0],
        [12.0, 0.0, 0.0],
        [8.0, 0.0, 20.0],
        [0.0, 0.0, 20.0],
    ]
    assert result["data"] == {
        "node_name": "lathed_vase",
        "object_id": 42,
        "profile_plane": "xz",
        "profile_point_count": 4,
        "degrees": 270.0,
        "segments": 24,
        "weld_core": False,
        "flip_normals": True,
    }


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"profile_points": [[1.0, 0.0]]}, "profile_points"),
        ({"profile_points": [[1.0, 0.0]] * 257}, "profile_points"),
        ({"profile_points": [[float("nan"), 0.0], [1.0, 1.0]]}, "profile_points"),
        ({"profile_points": [[-1.0, 0.0], [1.0, 1.0]]}, "profile_points"),
        ({"degrees": 0.0}, "degrees"),
        ({"degrees": 361.0}, "degrees"),
        ({"segments": 2}, "segments"),
        ({"segments": 257}, "segments"),
        ({"segments": True}, "segments"),
        ({"weld_core": "no"}, "weld_core"),
        ({"name": ""}, "name"),
    ],
)
def test_lathe_profile_rejects_unbounded_inputs_before_scene_mutation(monkeypatch, overrides, field):
    """Direct executor calls fail closed before constructing a native scene node."""
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    params = {"profile_points": [[0.0, 0.0], [1.0, 2.0]]}
    params.update(overrides)
    result = run_skill_script(str(SKILL_DIR / "action_lathe_profile.py"), params)

    assert result["success"] is False
    assert result["status"] == "error"
    assert field in result["message"]
    assert runtime.shape is None


def test_lathe_profile_rolls_back_when_native_modifier_readback_differs(monkeypatch):
    """A host-coerced modifier cannot be reported as a successful creation."""
    runtime = _FakeRuntime(tamper_modifier=True)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {"profile_points": [[0.0, 0.0], [4.0, 0.0], [0.0, 8.0]], "segments": 24},
    )

    assert result["success"] is False
    assert result["status"] == "error"
    assert "readback" in result["message"]
    assert result["data"]["failure_stage"] == "readback_native_lathe"
    assert len(runtime.deleted) == 1
    assert runtime.shape is None


def test_lathe_profile_rolls_back_when_native_creation_raises(monkeypatch):
    """A native API failure after spline creation removes the partial node."""
    runtime = _FakeRuntime(fail_add_modifier=True)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {"profile_points": [[0.0, 0.0], [4.0, 0.0], [0.0, 8.0]]},
    )

    assert result["success"] is False
    assert result["status"] == "error"
    assert result["data"]["failure_stage"] == "create_native_lathe"
    assert result["data"]["rolled_back"] is True
    assert len(runtime.deleted) == 1
    assert runtime.shape is None


def test_lathe_profile_declares_bounded_main_thread_contract():
    """The exported tool schema rejects unbounded calls before host dispatch."""
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    tool = next(item for item in tools if item["name"] == "lathe_profile")

    schema = tool["input_schema"]
    points = schema["properties"]["profile_points"]
    assert tool["source_file"] == "action_lathe_profile.py"
    assert tool["execution"] == "sync"
    assert tool["affinity"] == "main"
    assert tool["enforce_thread_affinity"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["profile_points"]
    assert points["minItems"] == 2
    assert points["maxItems"] == 256
    assert points["items"]["minItems"] == points["items"]["maxItems"] == 2
    assert schema["properties"]["degrees"]["exclusiveMinimum"] == 0
    assert schema["properties"]["degrees"]["maximum"] == 360
    assert schema["properties"]["segments"]["minimum"] == 3
    assert schema["properties"]["segments"]["maximum"] == 256
