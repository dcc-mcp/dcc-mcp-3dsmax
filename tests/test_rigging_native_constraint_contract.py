"""Native constraint readback regressions for typed rigging tools."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class _Node:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.parent = None
        self.isHidden = False
        self.position = SimpleNamespace(controller=None)
        self.rotation = SimpleNamespace(controller=None)
        self.scale = [1.0, 1.0, 1.0]
        self.constraints = []


class _NativePositionConstraint:
    """Mirror the list API without accepting synthetic target/weight fields."""

    __slots__ = ("relative", "_targets")

    def __init__(self) -> None:
        self.relative = False
        self._targets = []

    def appendTarget(self, target, weight):  # noqa: N802 - mirrors pymxs.
        self._targets.append((target, float(weight)))

    def getNumTargets(self):  # noqa: N802 - mirrors pymxs.
        return len(self._targets)

    def getNode(self, index):  # noqa: N802 - mirrors pymxs.
        return self._targets[index - 1][0]

    def getWeight(self, index):  # noqa: N802 - mirrors pymxs.
        return self._targets[index - 1][1]


class _LinkParams:
    def __init__(self, *, writable: bool = True) -> None:
        self._writable = writable
        self._position = [4.0, 5.0, 6.0]
        self._rotation = [0.1, 0.2, 0.3, 0.9]
        self._scale = [2.0, 3.0, 4.0]

    @property
    def Position(self):  # noqa: N802 - mirrors pymxs.
        return self._position

    @Position.setter
    def Position(self, value):  # noqa: N802 - mirrors pymxs.
        if self._writable:
            self._position = list(value)

    @property
    def Rotation(self):  # noqa: N802 - mirrors pymxs.
        return self._rotation

    @Rotation.setter
    def Rotation(self, value):  # noqa: N802 - mirrors pymxs.
        if self._writable:
            self._rotation = list(value)

    @property
    def Scale(self):  # noqa: N802 - mirrors pymxs.
        return self._scale

    @Scale.setter
    def Scale(self, value):  # noqa: N802 - mirrors pymxs.
        if self._writable:
            self._scale = list(value)


class _HostQuaternion:
    x = 0.0
    y = 0.0
    z = 0.0
    w = 1.0

    def __init__(self) -> None:
        self.indexed = False

    def __getitem__(self, _index):
        self.indexed = True
        raise RuntimeError("pymxs quaternion does not expose indexed get")


class _NativeLinkConstraint:
    def __init__(self, driven, *, writable_offset: bool = True, wrap_target: bool = False) -> None:
        self._driven = driven
        self.link_params = _LinkParams(writable=writable_offset)
        self._targets = []
        self._wrap_target = wrap_target

    def addTarget(self, target, frame):  # noqa: N802 - mirrors pymxs.
        assert getattr(self._driven, "controller", None) is self
        self._targets.append((target, int(frame)))

    def getNumTargets(self):  # noqa: N802 - mirrors pymxs.
        return len(self._targets)

    def getNode(self, index):  # noqa: N802 - mirrors pymxs.
        target = self._targets[index - 1][0]
        if self._wrap_target:
            return _Node(target.name, target.handle)
        return target


class _Runtime:
    def __init__(self, *, writable_parent_offset: bool = True, wrap_parent_target: bool = False) -> None:
        self.objects = [_Node("driven", 1), _Node("driver", 2)]
        self.constraint_calls = 0
        self.currentTime = 0
        self._writable_parent_offset = writable_parent_offset
        self._wrap_parent_target = wrap_parent_target

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs.
        return next((node for node in self.objects if node.name == name), None)

    def Position_Constraint(self):  # noqa: N802 - mirrors pymxs.
        self.constraint_calls += 1
        return _NativePositionConstraint()

    def Link_Constraint(self):  # noqa: N802 - mirrors pymxs.
        self.constraint_calls += 1
        return _NativeLinkConstraint(
            self.objects[0],
            writable_offset=self._writable_parent_offset,
            wrap_target=self._wrap_parent_target,
        )

    def Point3(self, *values):  # noqa: N802 - mirrors pymxs.
        return [float(value) for value in values]

    def Quat(self, *values):  # noqa: N802 - mirrors pymxs.
        return [float(value) for value in values]


def test_constraint_readback_uses_native_target_list_without_synthetic_fields():
    from dcc_mcp_3dsmax._rig_constraint_contract import create_constraint

    runtime = _Runtime()
    result = create_constraint(
        runtime,
        constrained_name="driven",
        target_name="driver",
        constraint_type="point",
        weight=75,
        maintain_offset=True,
    )

    assert result["success"] is True, result
    assert runtime.objects[0].position.controller.getNode(1) is runtime.objects[1]
    assert runtime.objects[0].position.controller.getWeight(1) == 75.0


def test_constraint_rejects_non_finite_weight_before_native_construction():
    from dcc_mcp_3dsmax._rig_constraint_contract import create_constraint

    runtime = _Runtime()
    result = create_constraint(
        runtime,
        constrained_name="driven",
        target_name="driver",
        constraint_type="point",
        weight=float("nan"),
    )

    assert result["success"] is False
    assert runtime.constraint_calls == 0
    assert runtime.objects[0].position.controller is None


def test_parent_constraint_applies_and_reads_back_maintain_offset():
    from dcc_mcp_3dsmax._rig_constraint_contract import create_constraint

    preserved_runtime = _Runtime()
    preserved = create_constraint(
        preserved_runtime,
        constrained_name="driven",
        target_name="driver",
        constraint_type="parent",
        maintain_offset=True,
    )
    preserved_params = preserved_runtime.objects[0].controller.link_params

    reset_runtime = _Runtime()
    reset = create_constraint(
        reset_runtime,
        constrained_name="driven",
        target_name="driver",
        constraint_type="parent",
        maintain_offset=False,
    )
    reset_params = reset_runtime.objects[0].controller.link_params

    assert preserved["success"] is True, preserved
    assert preserved_params.Position == [4.0, 5.0, 6.0]
    assert preserved_params.Rotation == [0.1, 0.2, 0.3, 0.9]
    assert preserved_params.Scale == [2.0, 3.0, 4.0]
    assert reset["success"] is True, reset
    assert reset_params.Position == [0.0, 0.0, 0.0]
    assert reset_params.Rotation == [0.0, 0.0, 0.0, 1.0]
    assert reset_params.Scale == [1.0, 1.0, 1.0]


def test_parent_constraint_fails_closed_when_offset_cannot_be_applied():
    from dcc_mcp_3dsmax._rig_constraint_contract import create_constraint

    runtime = _Runtime(writable_parent_offset=False)
    result = create_constraint(
        runtime,
        constrained_name="driven",
        target_name="driver",
        constraint_type="parent",
        maintain_offset=False,
    )

    assert result["success"] is False, result
    assert result["data"]["changed_target_count"] == 0
    assert not hasattr(runtime.objects[0], "controller")


def test_parent_offset_readback_uses_host_quaternion_components():
    from dcc_mcp_3dsmax._rig_constraint_contract import _numeric_components

    value = _HostQuaternion()
    assert _numeric_components(value, 4) == (0.0, 0.0, 0.0, 1.0)
    assert value.indexed is False


def test_parent_target_readback_matches_fresh_host_wrapper_by_handle():
    from dcc_mcp_3dsmax._rig_constraint_contract import create_constraint

    runtime = _Runtime(wrap_parent_target=True)
    result = create_constraint(
        runtime,
        constrained_name="driven",
        target_name="driver",
        constraint_type="parent",
        maintain_offset=True,
    )

    assert result["success"] is True, result
