"""Contract tests for the bundled 3ds Max modeling skill."""

from __future__ import annotations

import copy
import math
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
    def __init__(self, *, name: str = "Shape001", handle: int = 42) -> None:
        self.name = name
        self.handle = handle
        self.splines = []
        self.modifiers = []


class _Quat:
    def __init__(self, x, y, z, w):
        self.x, self.y, self.z, self.w = x, y, z, w


class _Axis:
    def __init__(self, ignore_rotation=False):
        self.position = _Point3(0, 0, 0)
        self.scale = _Point3(1, 1, 1)
        self._rotation = _Quat(math.sqrt(0.5), 0, 0, math.sqrt(0.5))
        self.ignore_rotation = ignore_rotation

    @property
    def rotation(self):
        return self._rotation

    @rotation.setter
    def rotation(self, value):
        if not self.ignore_rotation:
            self._rotation = value


class _Lathe:
    def __init__(self, ignore_rotation=False) -> None:
        self.degrees = 360.0
        self.segs = 16
        self.weldCore = True
        self.flipNormals = False
        self._axis = _Axis(ignore_rotation)

    @property
    def axis(self):
        return copy.copy(self._axis)


class _FakeMaxOps:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def getNodeByHandle(self, handle):  # noqa: N802 - mirrors the native maxOps API.
        if self.runtime.handle_lookup_raises:
            raise RuntimeError("native handle lookup failure")
        if self.runtime.shape is not None and self.runtime.shape.handle == handle:
            return self.runtime.shape
        same_name_node = self.runtime.same_name_node
        if same_name_node is not None and same_name_node.handle == handle:
            return same_name_node
        return None


class _FakeRuntime:
    def __init__(
        self,
        *,
        tamper_modifier: bool = False,
        fail_add_modifier: bool = False,
        delete_is_noop: bool = False,
        handle_lookup_raises: bool = False,
        max_ops_missing: bool = False,
        max_ops_access_raises: bool = False,
        same_name_survivor: bool = False,
        tamper_axis: bool = False,
    ) -> None:
        self.shape = None
        self.same_name_node = _SplineShape(handle=7) if same_name_survivor else None
        self.updated = False
        self.tamper_modifier = tamper_modifier
        self.fail_add_modifier = fail_add_modifier
        self.delete_is_noop = delete_is_noop
        self.handle_lookup_raises = handle_lookup_raises
        self.max_ops_access_raises = max_ops_access_raises
        self._max_ops = None if max_ops_missing else _FakeMaxOps(self)
        self.deleted = []
        self.tamper_axis = tamper_axis

    @property
    def maxOps(self):  # noqa: N802 - mirrors the native runtime interface owner.
        if self.max_ops_access_raises:
            raise RuntimeError("native maxOps access failure")
        return self._max_ops

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

    def Lathe(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Lathe(self.tamper_axis)

    Quat = staticmethod(_Quat)

    @staticmethod
    def Matrix3(value):  # noqa: N802 - native identity matrix.
        assert value == 1
        return types.SimpleNamespace(position=_Point3(0, 0, 0), rotation=_Quat(0, 0, 0, 1), scale=_Point3(1, 1, 1))

    @staticmethod
    def setProperty(modifier, path, value):  # noqa: N802 - native nested setter.
        if path != "axis":
            raise RuntimeError("Unknown property: " + path)
        for field in ("position", "rotation", "scale"):
            setattr(modifier._axis, field, getattr(value, field))

    @staticmethod
    def getProperty(modifier, path):  # noqa: N802 - native nested getter.
        assert path == "axis"
        return copy.copy(modifier._axis)

    @staticmethod
    def nodeGetBoundingBox(shape, matrix):  # noqa: N802 - native geometry surface.
        modifier = shape.modifiers[0]
        vertices = []
        for point in shape.splines[0]:
            for index in range(modifier.segs + 1):
                angle = math.radians(modifier.degrees * index / modifier.segs)
                c, s = math.cos(angle), math.sin(angle)
                if abs(modifier.axis.rotation.w) == 1:
                    vertices.append((point.x * c, point.x * s, point.z))
                else:
                    vertices.append((point.x * c + point.z * s, 0, -point.x * s + point.z * c))
        return (
            _Point3(*(min(v[i] for v in vertices) for i in range(3))),
            _Point3(*(max(v[i] for v in vertices) for i in range(3))),
        )

    def addModifier(self, shape, modifier):  # noqa: N802 - mirrors pymxs runtime naming.
        if self.fail_add_modifier:
            raise RuntimeError("native modifier failure")
        shape.modifiers.append(modifier)
        if self.tamper_modifier:
            modifier.segs = 8

    def delete(self, node):
        self.deleted.append(node)
        if node is self.shape and not self.delete_is_noop:
            self.shape = None

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        if self.shape is not None and self.shape.name == name:
            return self.shape
        if self.same_name_node is not None and self.same_name_node.name == name:
            return self.same_name_node
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
        "axis": "z",
        "bounds": {"min": [-12.0, -12.0, 0.0], "max": [12.0, 12.0, 20.0], "size": [24.0, 24.0, 20.0]},
        "profile_point_count": 4,
        "degrees": 270.0,
        "segments": 24,
        "weld_core": False,
        "flip_normals": True,
    }


@pytest.mark.parametrize("tamper_axis", [False, True])
def test_lathe_profile_revolves_xz_radius_height_about_z(monkeypatch, tamper_axis):
    runtime = _FakeRuntime(tamper_axis=tamper_axis)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {"profile_points": [[112, 0], [220, 0], [220, 34], [112, 34]], "segments": 128},
    )
    if tamper_axis:
        assert not result["success"] and result["data"]["rolled_back"]
    else:
        assert result["success"]
        lo, hi = runtime.nodeGetBoundingBox(runtime.shape, 1)
        assert [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z] == pytest.approx([440, 440, 34])
        assert result["data"]["axis"] == "z"
        assert result["data"]["bounds"]["size"] == pytest.approx([440, 440, 34])


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


def test_lathe_profile_does_not_claim_rollback_when_native_delete_is_a_noop(monkeypatch):
    """A non-raising delete call is not proof that the created node is absent."""
    runtime = _FakeRuntime(tamper_modifier=True, delete_is_noop=True)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {"profile_points": [[0.0, 0.0], [4.0, 0.0], [0.0, 8.0]], "segments": 24},
    )

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "readback_native_lathe"
    assert result["data"]["rolled_back"] is False
    assert runtime.shape is not None
    assert runtime.deleted == [runtime.shape]


def test_lathe_profile_rollback_uses_exact_handle_and_preserves_same_name_node(monkeypatch):
    """Rollback removes only the created node even when another node has its name."""
    runtime = _FakeRuntime(tamper_modifier=True, same_name_survivor=True)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {"profile_points": [[0.0, 0.0], [4.0, 0.0], [0.0, 8.0]], "segments": 24},
    )
    created_node = runtime.deleted[0]

    assert result["success"] is False
    assert result["data"]["rolled_back"] is True
    assert runtime.deleted == [created_node]
    assert created_node.handle == 42
    assert runtime.same_name_node is not None
    assert runtime.same_name_node.handle == 7
    assert runtime.getNodeByName("Shape001") is runtime.same_name_node


def test_lathe_profile_does_not_claim_rollback_when_handle_readback_raises(monkeypatch):
    """Rollback remains unverified when the exact-handle absence query fails."""
    runtime = _FakeRuntime(tamper_modifier=True, handle_lookup_raises=True)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {"profile_points": [[0.0, 0.0], [4.0, 0.0], [0.0, 8.0]], "segments": 24},
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False
    assert runtime.shape is None


@pytest.mark.parametrize(
    "runtime_kwargs",
    [
        {"max_ops_missing": True},
        {"max_ops_access_raises": True},
    ],
)
def test_lathe_profile_does_not_claim_rollback_without_max_ops_readback(monkeypatch, runtime_kwargs):
    """Missing or inaccessible maxOps cannot prove that the exact node is absent."""
    runtime = _FakeRuntime(tamper_modifier=True, **runtime_kwargs)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(SKILL_DIR / "action_lathe_profile.py"),
        {"profile_points": [[0.0, 0.0], [4.0, 0.0], [0.0, 8.0]], "segments": 24},
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False
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
