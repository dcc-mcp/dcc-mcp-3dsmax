"""Offline contract tests for the spline / curve / boolean skill additions."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MODELING_DIR = ROOT / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-modeling"
MESH_OPS_DIR = ROOT / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-mesh-ops"


# ── Fake pymxs runtime ─────────────────────────────────────────────────


class Point3:
    """Stand-in for ``pymxs.runtime.Point3`` including matrix multiplication."""

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Point3) and (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __mul__(self, other):
        if isinstance(other, Matrix3):
            return other.apply(self)
        return NotImplemented

    def __repr__(self) -> str:  # pragma: no cover - debugging aid.
        return "Point3({}, {}, {})".format(self.x, self.y, self.z)


class Matrix3:
    """A translate-only stand-in for ``pymxs.runtime.Matrix3``."""

    def __init__(self, translation: Point3) -> None:
        self.translation = translation

    def apply(self, point: Point3) -> Point3:
        return Point3(point.x + self.translation.x, point.y + self.translation.y, point.z + self.translation.z)


class Knot:
    def __init__(self, point: Point3, knot_type: str = "corner") -> None:
        self.point = point
        self.in_vec = Point3(0.0, 0.0, 0.0)
        self.out_vec = Point3(0.0, 0.0, 0.0)
        self.type = knot_type


class Spline:
    def __init__(self) -> None:
        self.knots: list = []
        self.closed = False


class Shape:
    """A SplineShape node with a translate-only object transform."""

    _next_handle = 100

    def __init__(self, name: str = "Shape001", translation=(10.0, 0.0, -5.0)) -> None:
        Shape._next_handle += 1
        self.name = name
        self.handle = Shape._next_handle
        self.splines: list = []
        self.modifiers: list = []
        self.user_properties: dict = {}
        self.transform = Matrix3(Point3(*translation))
        self.objectTransform = self.transform

    # ``node_identity`` probes these.
    @property
    def baseObject(self):  # noqa: N802 - mirrors the native node interface.
        return self

    isHidden = False
    parent = None


class Mesh:
    """A plain mesh-like node used as a boolean base or operand."""

    _next_handle = 500

    def __init__(self, name: str) -> None:
        Mesh._next_handle += 1
        self.name = name
        self.handle = Mesh._next_handle
        self.modifiers: list = []
        self.user_properties: dict = {}
        self.isHidden = False
        self.parent = None

    @property
    def baseObject(self):  # noqa: N802 - mirrors the native node interface.
        return self

    transform = None
    objectTransform = None


class ProBooleanSymbol:
    """The ``ProBoolean`` runtime symbol: a constructor and an interface struct.

    3ds Max exposes the same name both as the compound-object constructor and
    as the interface struct that drives it, so the fake models both - the
    adapter has to reach the struct for ``SetBoolOp``/``SetOperandB`` while
    construction goes through calling the symbol.
    """

    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    def __call__(self):
        if self.runtime.no_boolean:
            raise RuntimeError("no ProBoolean constructor")
        return self.runtime._new_boolean()

    def SetBoolOp(self, boolean, code):  # noqa: N802 - mirrors the native method name.
        if self.runtime.boolean_rejects_operation:
            raise RuntimeError("operation rejected by the host")
        boolean.bool_op = 0 if self.runtime.boolean_coerces_op else int(code)
        return None

    def GetBoolOp(self, boolean):  # noqa: N802 - mirrors the native method name.
        if self.runtime.boolean_hides_operation:
            return None
        return boolean.bool_op

    def SetOperandB(self, boolean, node, add_method=0, mat_method=0):  # noqa: N802
        return boolean.add_operand(node)

    def GetOp(self, boolean, index):  # noqa: N802 - mirrors the native method name.
        return boolean.operands[index - 1]


class BooleanObject:
    """A Boolean-like node with an operand list and a readable mode."""

    _next_handle = 900

    def __init__(self) -> None:
        BooleanObject._next_handle += 1
        self.name = "Boolean001"
        self.handle = BooleanObject._next_handle
        self.bool_op = 0
        self.operands: list = []
        self.modifiers: list = []
        self.user_properties: dict = {}
        self.isHidden = False
        self.parent = None
        self.class_name = "ProBoolean"
        self.accepted_adds: list = []
        self.reject_operands = False
        self.reject_operation = False
        self.hide_operation = False
        self.coerce_op = False

    @property
    def baseObject(self):  # noqa: N802 - mirrors the native node interface.
        return self

    transform = None
    objectTransform = None

    @property
    def NumOps(self):  # noqa: N802 - mirrors the native property name.
        return len(self.operands)

    def add_operand(self, node):
        """Register one operand, honouring the reject switch."""
        if self.reject_operands:
            raise RuntimeError("operand rejected by the host")
        self.operands.append(node)
        self.accepted_adds.append(getattr(node, "name", None))

    # Boolean2 exposes its mode as object methods rather than through a
    # namespaced interface struct, so both shapes exist on the fake node.
    def getBoolOp(self):  # noqa: N802 - mirrors the native method name.
        if self.hide_operation:
            return None
        return self.bool_op

    def setBoolOp(self, code):  # noqa: N802 - mirrors the native method name.
        if self.reject_operation:
            raise RuntimeError("operation rejected by the host")
        self.bool_op = 0 if self.coerce_op else int(code)
        return None

    def setOperandB(self, node, add_method=0, mat_method=0):  # noqa: N802 - native naming.
        return self.add_operand(node)

    def SetOp(self, index, node):  # noqa: N802 - mirrors the native method name.
        self.operands[index - 1] = node

    def RemoveOp(self, index):  # noqa: N802 - mirrors the native method name.
        del self.operands[index - 1]

    def ExtractOp(self, index):  # noqa: N802 - mirrors the native method name.
        clone = Mesh("{}_extracted".format(getattr(self.operands[index - 1], "name", "op")))
        return clone


class LoftObject:
    """A Loft-like node that registers cross-sections and surface parameters."""

    _next_handle = 700

    def __init__(self) -> None:
        LoftObject._next_handle += 1
        self.name = "Loft001"
        self.handle = LoftObject._next_handle
        self.shapes: list = []
        self.modifiers: list = []
        self.user_properties: dict = {}
        self.isHidden = False
        self.parent = None
        self.numShapes = 0
        self.shape_steps = 5
        self.path_steps = 5
        self.optimize_shapes = False
        self.optimize_path = False
        self.cap_start = True
        self.cap_end = True
        self.smooth_length = False
        self.smooth_width = False
        self.reject_shapes = False
        self.reject_steps = False
        self.reject_rename = False
        self.accepted: dict = {}

    @property
    def baseObject(self):  # noqa: N802 - mirrors the native node interface.
        return self

    transform = None
    objectTransform = None

    def addShape(self, node, param=None):  # noqa: N802 - mirrors the native method name.
        if self.reject_shapes:
            raise RuntimeError("cross-section rejected by the host")
        self.shapes.append(node)
        self.numShapes = len(self.shapes)

    def createPath(self, node):  # noqa: N802 - mirrors the native method name.
        self.path = node

    def deleteShape(self, index):  # noqa: N802 - mirrors the native method name.
        """Take a registered cross-section back off the loft."""
        if index < 1 or index > len(self.shapes):
            raise RuntimeError("no cross-section at index {}".format(index))
        del self.shapes[index - 1]
        self.numShapes = len(self.shapes)

    def __setattr__(self, name, value):
        if name in ("shape_steps", "path_steps") and getattr(self, "reject_steps", False):
            raise RuntimeError("surface parameter rejected by the host")
        if name == "name" and getattr(self, "reject_rename", False):
            raise RuntimeError("node name rejected by the host")
        object.__setattr__(self, name, value)


class SweepModifier:
    """A Sweep modifier with a verifiable ``Shapes`` section registry."""

    def __init__(self) -> None:
        self.name = "Sweep"
        self.sectionType = 0
        self.Shapes: list = []
        self.enabled = True

    def AddShape(self, node):  # noqa: N802 - mirrors the native method name.
        self.Shapes.append(node)


class FakeRuntime:
    """A pymxs runtime stand-in covering the spline, sweep, loft, boolean APIs."""

    def __init__(self, **options: object) -> None:
        self.nodes: list = []
        self.deleted: list = []
        self.delete_is_noop = bool(options.get("delete_is_noop", False))
        self.no_inverse = bool(options.get("no_inverse", False))
        self.no_sweep = bool(options.get("no_sweep", False))
        self.sweep_rejects_section = bool(options.get("sweep_rejects_section", False))
        self.no_loft = bool(options.get("no_loft", False))
        self.loft_rejects_shapes = bool(options.get("loft_rejects_shapes", False))
        self.loft_rejects_steps = bool(options.get("loft_rejects_steps", False))
        self.loft_returns_object_only = bool(options.get("loft_returns_object_only", False))
        self.no_boolean = bool(options.get("no_boolean", False))
        self.boolean_rejects_operands = bool(options.get("boolean_rejects_operands", False))
        self.boolean_coerces_op = bool(options.get("boolean_coerces_op", False))
        self.boolean_rejects_operation = bool(options.get("boolean_rejects_operation", False))
        self.boolean_hides_operation = bool(options.get("boolean_hides_operation", False))
        self.no_user_props = bool(options.get("no_user_props", False))
        self.user_prop_readback_tampered = bool(options.get("user_prop_readback_tampered", False))
        self._pro_boolean = ProBooleanSymbol(self)
        self.tamper_knot = bool(options.get("tamper_knot", False))
        self.knot_type_source = options.get("knot_type_source", "native")

    # ── node registry ──

    @property
    def objects(self):
        return list(self.nodes)

    def _register(self, node):
        self.nodes.append(node)
        return node

    # ── runtime constants ──

    @staticmethod
    def Name(value):  # noqa: N802 - mirrors pymxs runtime naming.
        return value

    @staticmethod
    def Point3(x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return Point3(x, y, z)

    @staticmethod
    def inverse(matrix):
        return Matrix3(Point3(-matrix.translation.x, -matrix.translation.y, -matrix.translation.z))

    # ── construction ──

    def SplineShape(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._register(Shape())

    def Loft(self):  # noqa: N802 - mirrors pymxs runtime naming.
        if self.no_loft:
            raise RuntimeError("no Loft constructor")
        if self.loft_returns_object_only:
            return object()
        loft = LoftObject()
        loft.reject_shapes = self.loft_rejects_shapes
        loft.reject_steps = self.loft_rejects_steps
        return self._register(loft)

    @property
    def ProBoolean(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._pro_boolean

    def _new_boolean(self, class_name="ProBoolean"):
        boolean = BooleanObject()
        boolean.reject_operands = self.boolean_rejects_operands
        boolean.reject_operation = self.boolean_rejects_operation
        boolean.hide_operation = self.boolean_hides_operation
        boolean.coerce_op = self.boolean_coerces_op
        boolean.class_name = class_name
        return self._register(boolean)

    def Boolean2(self):  # noqa: N802 - mirrors pymxs runtime naming.
        if self.no_boolean:
            raise RuntimeError("no Boolean2 constructor")
        return self._new_boolean(class_name="Boolean2")

    @staticmethod
    def classOf(node):  # noqa: N802 - mirrors pymxs runtime naming.
        return getattr(node, "class_name", "")

    def Sweep(self):  # noqa: N802 - mirrors pymxs runtime naming.
        if self.no_sweep:
            raise RuntimeError("no Sweep constructor")
        return SweepModifier()

    # ── spline primitives ──

    @staticmethod
    def addNewSpline(shape):  # noqa: N802 - mirrors pymxs runtime naming.
        shape.splines.append(Spline())

    @staticmethod
    def deleteSpline(shape, index):  # noqa: N802 - mirrors pymxs runtime naming.
        del shape.splines[index - 1]

    def addKnot(self, shape, spline_index, knot_type, segment_type, point):  # noqa: N802
        knot = Knot(point, str(knot_type))
        shape.splines[spline_index - 1].knots.append(knot)
        if self.tamper_knot and len(shape.splines[spline_index - 1].knots) == 1:
            knot.point = Point3(point.x + 500.0, point.y, point.z)

    @staticmethod
    def numSplines(shape):  # noqa: N802 - mirrors pymxs runtime naming.
        return len(shape.splines)

    @staticmethod
    def numKnots(shape, spline_index):  # noqa: N802 - mirrors pymxs runtime naming.
        return len(shape.splines[spline_index - 1].knots)

    @staticmethod
    def getKnotPoint(shape, spline_index, knot_index):  # noqa: N802 - mirrors pymxs naming.
        return shape.splines[spline_index - 1].knots[knot_index - 1].point

    @staticmethod
    def setKnotPoint(shape, spline_index, knot_index, point):  # noqa: N802 - mirrors naming.
        shape.splines[spline_index - 1].knots[knot_index - 1].point = point

    @staticmethod
    def getInVec(shape, spline_index, knot_index):  # noqa: N802 - mirrors pymxs naming.
        return shape.splines[spline_index - 1].knots[knot_index - 1].in_vec

    @staticmethod
    def setInVec(shape, spline_index, knot_index, value):  # noqa: N802 - mirrors pymxs naming.
        shape.splines[spline_index - 1].knots[knot_index - 1].in_vec = value

    @staticmethod
    def getOutVec(shape, spline_index, knot_index):  # noqa: N802 - mirrors pymxs naming.
        return shape.splines[spline_index - 1].knots[knot_index - 1].out_vec

    @staticmethod
    def setOutVec(shape, spline_index, knot_index, value):  # noqa: N802 - mirrors pymxs naming.
        shape.splines[spline_index - 1].knots[knot_index - 1].out_vec = value

    def getKnotType(self, shape, spline_index, knot_index):  # noqa: N802 - mirrors pymxs naming.
        if self.knot_type_source != "native":
            raise RuntimeError("knot type unavailable")
        return shape.splines[spline_index - 1].knots[knot_index - 1].type

    @staticmethod
    def setKnotType(shape, spline_index, knot_index, value):  # noqa: N802 - mirrors pymxs naming.
        shape.splines[spline_index - 1].knots[knot_index - 1].type = str(value)

    @staticmethod
    def isClosed(shape, spline_index):  # noqa: N802 - mirrors pymxs runtime naming.
        return shape.splines[spline_index - 1].closed

    @staticmethod
    def closeSpline(shape, spline_index):  # noqa: N802 - mirrors pymxs runtime naming.
        shape.splines[spline_index - 1].closed = True

    @staticmethod
    def openSpline(shape, spline_index):  # noqa: N802 - mirrors pymxs runtime naming.
        shape.splines[spline_index - 1].closed = False

    @staticmethod
    def updateShape(shape):  # noqa: N802 - mirrors pymxs runtime naming.
        shape.updated = True

    # ── scene queries ──

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.nodes:
            if str(getattr(node, "name", "")) == str(name):
                return node
        return None

    def delete(self, node):
        self.deleted.append(node)
        if not self.delete_is_noop and node in self.nodes:
            self.nodes.remove(node)

    def setUserPropVal(self, node, key, value):  # noqa: N802 - mirrors pymxs naming.
        if self.no_user_props:
            raise RuntimeError("the user property channel is unavailable")
        props = getattr(node, "user_properties", None)
        if not isinstance(props, dict):
            props = {}
            node.user_properties = props
        props[key] = value

    def getUserPropVal(self, node, key):  # noqa: N802 - mirrors pymxs naming.
        if self.no_user_props:
            raise RuntimeError("the user property channel is unavailable")
        props = getattr(node, "user_properties", None)
        if not isinstance(props, dict):
            return None
        value = props.get(key)
        if self.user_prop_readback_tampered:
            return "tampered"
        return value

    def deleteUserPropVal(self, node, key):  # noqa: N802 - mirrors pymxs naming.
        if self.no_user_props:
            raise RuntimeError("the user property channel is unavailable")
        props = getattr(node, "user_properties", None)
        if not isinstance(props, dict) or key not in props:
            return False
        del props[key]
        return True

    def addModifier(self, node, modifier):  # noqa: N802 - mirrors pymxs runtime naming.
        node.modifiers.insert(0, modifier)

    def deleteModifier(self, node, index):  # noqa: N802 - mirrors pymxs runtime naming.
        del node.modifiers[index - 1]

    @property
    def maxOps(self):  # noqa: N802 - mirrors the native runtime interface owner.
        return _MaxOps(self)


class _MaxOps:
    def __init__(self, runtime: FakeRuntime) -> None:
        self.runtime = runtime

    def getNodeByHandle(self, handle):  # noqa: N802 - mirrors the native maxOps API.
        for node in self.runtime.nodes:
            if int(getattr(node, "handle", -1)) == int(handle):
                return node
        return None


class _ObjectOnlyRuntime(FakeRuntime):
    """A runtime whose ``inverse`` is unavailable, so world mapping must fail."""

    def __init__(self, **options: object) -> None:
        super().__init__(**options)
        self.no_inverse = True

    @staticmethod
    def inverse(matrix):  # noqa: ARG004 - the capability is intentionally missing.
        raise RuntimeError("inverse is unavailable")


def _install(monkeypatch, runtime: FakeRuntime) -> FakeRuntime:
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def _load(directory: Path, script_name: str):
    path = directory / script_name
    spec = importlib.util.spec_from_file_location(
        "{}_{}".format(directory.name, path.stem), str(path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── draw_spline ───────────────────────────────────────────────────────


def test_draw_spline_creates_a_verified_world_space_spline(monkeypatch):
    """A created spline is read back in world space before the call succeeds."""
    runtime = _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_draw_spline.py")

    result = module.main(
        points=[[0.0, 0.0, 0.0], [50.0, 0.0, 40.0], [100.0, 0.0, 0.0]],
        name="guide_spline",
        knot_type="smooth",
        curve_type="curve",
    )

    assert result["success"] is True, result
    assert result["data"]["node"]["node_name"] == "guide_spline"
    assert result["data"]["knot_count"] == 3
    assert [knot["position"] for knot in result["data"]["knots"]] == [
        [0.0, 0.0, 0.0],
        [50.0, 0.0, 40.0],
        [100.0, 0.0, 0.0],
    ]
    # The world points are stored in the node's object space, not world space.
    shape = runtime.nodes[0]
    assert [[round(value, 6) for value in knot.point] for knot in shape.splines[0].knots] == [
        [-10.0, 0.0, 5.0],
        [40.0, 0.0, 45.0],
        [90.0, 0.0, 5.0],
    ]


def test_draw_spline_fails_when_the_readback_position_differs(monkeypatch):
    """A host that moves a knot cannot be reported as a successful draw."""
    runtime = _install(monkeypatch, FakeRuntime(tamper_knot=True))
    module = _load(MODELING_DIR, "action_draw_spline.py")

    result = module.main(points=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], name="tampered")

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "readback_spline"
    assert result["data"]["mismatches"][0]["field"] == "knots[0].position"
    assert result["data"]["rolled_back"] is True
    assert runtime.nodes == []


def test_draw_spline_does_not_claim_rollback_when_delete_is_a_noop(monkeypatch):
    """A non-raising delete is not proof that the created node is gone."""
    runtime = _install(monkeypatch, FakeRuntime(tamper_knot=True, delete_is_noop=True))
    module = _load(MODELING_DIR, "action_draw_spline.py")

    result = module.main(points=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False
    assert len(runtime.deleted) == 1


def test_draw_spline_fails_when_world_mapping_is_unavailable(monkeypatch):
    """Without an inverse transform the points cannot be placed honestly."""
    _install(monkeypatch, _ObjectOnlyRuntime())
    module = _load(MODELING_DIR, "action_draw_spline.py")

    result = module.main(points=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])

    assert result["success"] is False
    assert "inverse" in result["message"]


def test_draw_spline_appends_to_an_existing_spline(monkeypatch):
    """Append mode extends the requested spline without touching the others."""
    _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_draw_spline.py")

    created = module.main(points=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], name="shape_a")
    assert created["success"] is True

    appended = module.main(
        points=[[20.0, 0.0, 0.0], [30.0, 0.0, 0.0]],
        mode="append",
        node_name="shape_a",
    )

    assert appended["success"] is True, appended
    assert appended["data"]["knot_count"] == 4
    assert [knot["position"] for knot in appended["data"]["knots"]] == [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [20.0, 0.0, 0.0],
        [30.0, 0.0, 0.0],
    ]


def test_draw_spline_replace_mode_rewrites_one_spline(monkeypatch):
    """Replace mode swaps the knots of the requested spline only."""
    _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_draw_spline.py")

    module.main(points=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], name="shape_a")
    replaced = module.main(
        points=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]],
        mode="replace",
        node_name="shape_a",
    )

    assert replaced["success"] is True, replaced
    assert replaced["data"]["knot_count"] == 3
    assert [knot["position"] for knot in replaced["data"]["knots"]] == [
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
        [7.0, 8.0, 9.0],
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"points": [[0.0, 0.0, 0.0]]},
        {"points": [[0.0, 0.0, 0.0]] * 257},
        {"points": [[float("nan"), 0.0, 0.0], [1.0, 0.0, 0.0]]},
        {"points": [[0.0, 0.0], [1.0, 0.0, 0.0]]},
        {"mode": "rewire"},
        {"knot_type": "sharp"},
        {"curve_type": "arc"},
        {"name": ""},
        {"spline_index": 0},
    ],
)
def test_draw_spline_rejects_unbounded_inputs_before_scene_mutation(monkeypatch, overrides):
    """Validation failures happen before any node is created."""
    runtime = _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_draw_spline.py")

    params = {"points": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]}
    params.update(overrides)
    result = module.main(**params)

    assert result["success"] is False
    assert runtime.nodes == []


def test_draw_spline_requires_a_target_for_append(monkeypatch):
    """Append mode without a node reference fails instead of creating a shape."""
    runtime = _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_draw_spline.py")

    result = module.main(points=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], mode="append")

    assert result["success"] is False
    assert "node_name" in result["message"]
    assert runtime.nodes == []


# ── inspect_curve / edit_curve ────────────────────────────────────────


def test_inspect_curve_reports_world_knots_and_a_token(monkeypatch):
    """The read is world-space and hands out a token for edit_curve."""
    _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")

    draw.main(points=[[0.0, 0.0, 0.0], [25.0, 0.0, 10.0]], name="curve_a")
    result = inspect.main(node_name="curve_a")

    assert result["success"] is True, result
    assert result["data"]["spline_count"] == 1
    assert result["data"]["splines"][0]["knots"][1]["position"] == [25.0, 0.0, 10.0]
    assert len(result["data"]["token"]) == 32


def test_edit_curve_applies_a_verified_edit(monkeypatch):
    """An edit guarded by the current token lands and is read back."""
    _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")
    edit = _load(MODELING_DIR, "action_edit_curve.py")

    draw.main(points=[[0.0, 0.0, 0.0], [25.0, 0.0, 10.0]], name="curve_a")
    token = inspect.main(node_name="curve_a")["data"]["token"]

    result = edit.main(
        node_name="curve_a",
        token=token,
        knots=[{"index": 2, "position": [40.0, 5.0, 10.0], "knot_type": "bezier"}],
    )

    assert result["success"] is True, result
    assert result["data"]["edited_knot_count"] == 1
    assert result["data"]["knots"][1]["position"] == [40.0, 5.0, 10.0]


def test_edit_curve_rejects_a_stale_token_before_writing(monkeypatch):
    """A changed spline invalidates the token and nothing is written."""
    _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")
    edit = _load(MODELING_DIR, "action_edit_curve.py")

    draw.main(points=[[0.0, 0.0, 0.0], [25.0, 0.0, 10.0]], name="curve_a")
    token = inspect.main(node_name="curve_a")["data"]["token"]
    # Mutating the spline behind the token's back must invalidate it.
    draw.main(
        points=[[0.0, 0.0, 0.0], [99.0, 0.0, 0.0]],
        mode="replace",
        node_name="curve_a",
    )

    result = edit.main(
        node_name="curve_a",
        token=token,
        knots=[{"index": 1, "position": [1.0, 1.0, 1.0]}],
    )

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "stale_token"
    assert result["data"]["expected_token"] == token
    assert result["data"]["current_token"] != token


def test_edit_curve_requires_a_token(monkeypatch):
    """An unguarded edit is refused rather than applied optimistically."""
    _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    edit = _load(MODELING_DIR, "action_edit_curve.py")

    draw.main(points=[[0.0, 0.0, 0.0], [25.0, 0.0, 10.0]], name="curve_a")

    result = edit.main(
        node_name="curve_a",
        token="",
        knots=[{"index": 1, "position": [1.0, 1.0, 1.0]}],
    )

    assert result["success"] is False
    assert "token" in result["message"]


def test_edit_curve_rejects_an_out_of_range_knot_index(monkeypatch):
    """An index the spline does not have fails before any knot is touched."""
    _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")
    edit = _load(MODELING_DIR, "action_edit_curve.py")

    draw.main(points=[[0.0, 0.0, 0.0], [25.0, 0.0, 10.0]], name="curve_a")
    token = inspect.main(node_name="curve_a")["data"]["token"]

    result = edit.main(
        node_name="curve_a",
        token=token,
        knots=[{"index": 9, "position": [1.0, 1.0, 1.0]}],
    )

    assert result["success"] is False
    assert "out of range" in result["message"]


def test_edit_curve_restores_state_when_the_host_rejects_a_write(monkeypatch):
    """A failed edit must not leave a half-written spline behind."""
    runtime = _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")
    edit = _load(MODELING_DIR, "action_edit_curve.py")

    draw.main(points=[[0.0, 0.0, 0.0], [25.0, 0.0, 10.0]], name="curve_a")
    token = inspect.main(node_name="curve_a")["data"]["token"]

    original = runtime.setKnotPoint
    state = {"failed": False}

    def failing_setter(shape, spline_index, knot_index, point):
        # Fail once only: the restore path has to be able to write knot 2 back.
        if knot_index == 2 and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("host refused the write")
        original(shape, spline_index, knot_index, point)

    monkeypatch.setattr(runtime, "setKnotPoint", failing_setter)
    result = edit.main(
        node_name="curve_a",
        token=token,
        knots=[{"index": 2, "position": [40.0, 5.0, 10.0]}],
    )

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "apply_edits"
    assert result["data"]["restored"] is True
    assert inspect.main(node_name="curve_a")["data"]["splines"][0]["knots"][1]["position"] == [
        25.0,
        0.0,
        10.0,
    ]


# ── curve_model ───────────────────────────────────────────────────────


def test_curve_model_creates_a_rounded_profile_and_stores_parameters(monkeypatch):
    """A generated profile is verified in world space and persisted on the node."""
    _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(
        action="create",
        name="duct_profile",
        profile="rounded_rect",
        width=120.0,
        height=60.0,
        corner_radius=18.0,
        corner_segments=4,
    )

    assert result["success"] is True, result
    assert result["data"]["node"]["node_name"] == "duct_profile"
    assert result["data"]["profile"] == "rounded_rect"
    assert result["data"]["params_stored"] is True
    assert result["data"]["point_count"] > 4

    read_back = module.main(action="read", node_name="duct_profile")
    assert read_back["success"] is True
    assert read_back["data"]["name"] == "duct_profile"
    assert read_back["data"]["width"] == 120.0
    assert read_back["data"]["corner_radius"] == 18.0


def test_curve_model_list_reports_stored_models(monkeypatch):
    """List enumerates the nodes that carry a curve model payload."""
    _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    module.main(action="create", name="profile_one", profile="rectangle", width=10.0, height=10.0)
    module.main(action="create", name="profile_two", profile="circle", radius=5.0, segments=12)

    listed = module.main(action="list")

    assert listed["success"] is True
    assert sorted(entry["name"] for entry in listed["data"]["models"]) == [
        "profile_one",
        "profile_two",
    ]


def test_curve_model_clamps_an_oversized_corner_radius(monkeypatch):
    """A radius that cannot fit the profile is clamped and reported."""
    _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(
        action="create",
        name="clamped_profile",
        profile="rounded_rect",
        width=20.0,
        height=10.0,
        corner_radius=500.0,
    )

    assert result["success"] is True, result
    assert result["data"]["warnings"]
    assert "clamped" in result["data"]["warnings"][0]


def test_curve_model_sweep_registers_the_profile_as_a_section(monkeypatch):
    """A sweep is only reported when the modifier confirms the section."""
    _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    module = _load(MODELING_DIR, "action_curve_model.py")

    draw.main(points=[[0.0, 0.0, 0.0], [0.0, 0.0, 100.0]], name="duct_path")
    result = module.main(
        action="create",
        name="duct_sweep",
        profile="rounded_rect",
        operation="sweep",
        path_node="duct_path",
        width=40.0,
        height=20.0,
    )

    assert result["success"] is True, result
    assert result["data"]["sweep"]["attached"] is True
    assert result["data"]["sweep"]["path_node"]["node_name"] == "duct_path"


def test_curve_model_sweep_fails_when_the_section_cannot_be_confirmed(monkeypatch):
    """A Sweep modifier that will not report its section fails the call."""
    runtime = _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    module = _load(MODELING_DIR, "action_curve_model.py")

    draw.main(points=[[0.0, 0.0, 0.0], [0.0, 0.0, 100.0]], name="duct_path")

    class _OpaqueSweep:
        name = "Sweep"

        def __init__(self) -> None:
            self.sectionType = None

        def AddShape(self, node):  # noqa: N802 - mirrors the native method name.
            return None

    monkeypatch.setattr(runtime, "Sweep", _OpaqueSweep)
    result = module.main(
        action="create",
        name="duct_sweep",
        profile="circle",
        operation="sweep",
        path_node="duct_path",
        radius=8.0,
    )

    assert result["success"] is False
    assert "section" in result["message"]


def test_curve_model_sweep_requires_a_path(monkeypatch):
    """A sweep without a path fails instead of silently building a profile."""
    runtime = _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(action="create", name="no_path", profile="circle", operation="sweep")

    assert result["success"] is False
    assert "path_node" in result["message"]
    assert runtime.nodes == []


def test_curve_model_update_rebuilds_the_profile(monkeypatch):
    """An update regenerates the spline from the new parameters."""
    _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    module.main(action="create", name="profile_a", profile="rectangle", width=40.0, height=20.0)
    result = module.main(
        action="update",
        name="profile_a",
        node_name="profile_a",
        profile="rectangle",
        width=80.0,
        height=20.0,
    )

    assert result["success"] is True, result
    assert result["data"]["point_count"] == 4
    assert [point[0] for point in result["data"]["points"]] == [-40.0, 40.0, 40.0, -40.0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile": "hexagon"},
        {"operation": "extrude"},
        {"action": "frobnicate"},
        {"profile": "polyline"},
        {"profile": "circle", "radius": 0.0},
        {"profile": "circle", "segments": 2},
        {"profile": "rectangle", "width": -1.0},
        {"corner_segments": 99},
    ],
)
def test_curve_model_rejects_invalid_inputs(monkeypatch, overrides):
    """Invalid inputs fail closed before a node is created."""
    runtime = _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    params = {"action": "create", "name": "bad"}
    params.update(overrides)
    result = module.main(**params)

    assert result["success"] is False
    assert runtime.nodes == []


# ── loft_mesh ─────────────────────────────────────────────────────────


def _two_profiles(monkeypatch, runtime):
    module = _load(MODELING_DIR, "action_curve_model.py")
    module.main(action="create", name="profile_a", profile="circle", radius=20.0, segments=12)
    module.main(action="create", name="profile_b", profile="circle", radius=10.0, segments=12)


def test_loft_mesh_registers_every_cross_section(monkeypatch):
    """The registered shape count has to match the requested sections."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    result = module.main(
        action="create",
        name="duct_loft",
        cross_sections=["profile_a", "profile_b"],
        shape_steps=4,
        path_steps=8,
        cap_start=True,
        cap_end=True,
    )

    assert result["success"] is True, result
    assert result["data"]["cross_section_count"] == 2
    assert result["data"]["registered_shape_count"] == 2
    assert result["data"]["applied_surface_params"] == {
        "shape_steps": 4,
        "path_steps": 8,
        "cap_start": True,
        "cap_end": True,
    }
    assert result["data"]["params_stored"] is True


def test_loft_mesh_fails_when_a_cross_section_is_rejected(monkeypatch):
    """A section the host refuses fails the call and removes the new node."""
    runtime = _install(monkeypatch, FakeRuntime(loft_rejects_shapes=True))
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    result = module.main(action="create", cross_sections=["profile_a", "profile_b"])

    assert result["success"] is False
    assert "addShape" in result["message"] or "Loft object" in result["message"]
    assert not any(getattr(node, "name", "") == "Loft001" for node in runtime.nodes)


def test_loft_mesh_fails_when_the_shape_count_cannot_be_confirmed(monkeypatch):
    """An unreadable shape count is a failure, not an assumed success."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    class _OpaqueLoft:
        def __init__(self) -> None:
            self.name = "Loft001"
            self.handle = 999
            self.modifiers: list = []
            self.user_properties: dict = {}
            self.isHidden = False
            self.parent = None

        @property
        def baseObject(self):  # noqa: N802 - mirrors the native node interface.
            return self

        transform = None
        objectTransform = None

        def addShape(self, node, param=None):  # noqa: N802 - mirrors the native naming.
            return None

    monkeypatch.setattr(runtime, "Loft", _OpaqueLoft)
    result = module.main(action="create", cross_sections=["profile_a", "profile_b"])

    assert result["success"] is False
    assert "shape count" in result["message"]


def test_loft_mesh_rejects_surface_parameters_on_every_action_path(monkeypatch):
    """A parameter the host refuses fails the call on create and on update."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    module.main(action="create", name="duct_loft", cross_sections=["profile_a", "profile_b"])
    loft = runtime.getNodeByName("duct_loft")
    loft.reject_steps = True

    # An update owns the caller's node, so the node stays; the rejection is
    # still a failure and is never presented as an applied parameter.
    result = module.main(action="update", node_name="duct_loft", shape_steps=3)
    assert result["success"] is False, result
    assert result["data"]["rejected_surface_params"]
    assert result["data"]["rejected_surface_params"][0]["property"] == "shape_steps"
    assert "shape_steps" not in result["data"]["applied_surface_params"]
    assert result["data"]["rolled_back"] is False
    assert result["data"]["restored"] is True
    # No cross-sections were supplied, so no shape count was measured and
    # none is reported: a count here would claim a readback that never ran.
    assert "observed_shape_count" not in result["data"]
    assert "expected_shape_count" not in result["data"]
    assert runtime.getNodeByName("duct_loft") is loft
    assert loft.numShapes == 2

    # The same rejection on an update that also adds cross-sections: the new
    # sections come back off instead of leaving a half-updated loft.
    extended = module.main(
        action="update", node_name="duct_loft", cross_sections=["profile_a", "profile_b"], shape_steps=3
    )
    assert extended["success"] is False, extended
    assert extended["data"]["rejected_surface_params"][0]["property"] == "shape_steps"
    assert extended["data"]["restored"] is True
    assert extended["data"]["observed_shape_count"] == 2
    assert loft.numShapes == 2

    # A create that hits the same rejection fails outright and keeps no node.
    runtime.loft_rejects_steps = True
    fresh = module.main(action="create", cross_sections=["profile_a", "profile_b"], shape_steps=9)
    assert fresh["success"] is False
    assert fresh["data"]["rejected_surface_params"][0]["property"] == "shape_steps"
    assert fresh["data"]["rolled_back"] is True


def test_loft_mesh_persists_the_parameters_a_rejected_update_did_apply(monkeypatch):
    """A partially accepted update must not leave `read` answering stale values."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    created = module.main(
        action="create",
        name="duct_loft",
        cross_sections=["profile_a", "profile_b"],
        shape_steps=4,
        cap_start=True,
    )
    assert created["success"] is True, created
    loft = runtime.getNodeByName("duct_loft")
    loft.reject_steps = True

    # The host accepts `cap_start` and refuses `shape_steps`, so the loft ends
    # up holding a value the call reports as failed.
    result = module.main(
        action="update", node_name="duct_loft", cap_start=False, shape_steps=9
    )
    assert result["success"] is False, result
    assert result["data"]["applied_surface_params"] == {"cap_start": False}
    assert [entry["property"] for entry in result["data"]["rejected_surface_params"]] == ["shape_steps"]
    assert result["data"]["params_stored"] is True
    assert loft.cap_start is False
    assert loft.shape_steps == 4

    # The stored record has to describe the loft as it is: the parameter the
    # host took is recorded, the one it refused keeps the previous value.
    read = module.main(action="read", node_name="duct_loft")
    assert read["success"] is True, read
    assert read["data"]["surface_params"] == {"shape_steps": 4, "cap_start": False}


def test_loft_mesh_records_only_the_sections_a_failed_update_kept(monkeypatch):
    """A partial rollback leaves a prefix of the added sections, not all of them."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    created = module.main(
        action="create", name="duct_loft", cross_sections=["profile_a", "profile_b"]
    )
    assert created["success"] is True, created
    loft = runtime.getNodeByName("duct_loft")

    # Take back one of the two sections this call adds, then refuse: cleanup
    # stops with the loft holding three shapes where it started with two.
    removals = []
    real_delete_shape = loft.deleteShape

    def _delete_shape_once(index):
        if removals:
            raise RuntimeError("the host refused the second removal")
        removals.append(index)
        return real_delete_shape(index)

    monkeypatch.setattr(loft, "deleteShape", _delete_shape_once)
    loft.reject_steps = True

    result = module.main(
        action="update", node_name="duct_loft", cross_sections=["profile_a", "profile_b"], shape_steps=9
    )
    assert result["success"] is False, result
    assert result["data"]["restored"] is False
    assert result["data"]["observed_shape_count"] == 3
    assert result["data"]["params_stored"] is True
    assert loft.numShapes == 3

    # Only the section the host kept is recorded, so `read` cannot name a
    # cross-section that is no longer on the loft.
    read = module.main(action="read", node_name="duct_loft")
    assert read["success"] is True, read
    assert read["data"]["cross_sections"] == ["profile_a", "profile_b", "profile_a"]
    assert read["data"]["cross_section_count"] == 3


def _loft_refuses_the_path_node(monkeypatch, runtime, loft):
    """The host refuses the path node, after no cross-section was added."""

    def _refuse_path(node):
        raise RuntimeError("the host refused the path node")

    monkeypatch.setattr(loft, "createPath", _refuse_path)
    return {"path_node": "profile_a"}


def _loft_refuses_the_node_name(monkeypatch, runtime, loft):
    loft.reject_rename = True
    return {"name": "renamed_loft"}


def _loft_refuses_the_parameter_write(monkeypatch, runtime, loft):
    runtime.no_user_props = True
    return {"cap_start": False}


class _UnanswerableProperty:
    """A property the host refuses to answer for at all, set and get alike."""

    def __get__(self, instance, owner=None):
        raise RuntimeError("the host refused to answer")

    def __set__(self, instance, value):
        instance.__dict__["cap_start"] = value


def _loft_refuses_the_surface_probe(monkeypatch, runtime, loft):
    monkeypatch.setattr(LoftObject, "cap_start", _UnanswerableProperty(), raising=False)
    return {"cap_start": True}


@pytest.mark.parametrize(
    "refuse",
    [
        _loft_refuses_the_path_node,
        _loft_refuses_the_node_name,
        _loft_refuses_the_parameter_write,
        _loft_refuses_the_surface_probe,
    ],
    ids=["path-node", "node-name", "parameter-write", "surface-probe"],
)
def test_loft_mesh_omits_the_shape_count_when_no_baseline_was_measured(monkeypatch, refuse):
    """An update that added no section measured no count, so it reports none.

    `count_before` only holds a measured value once the call reads the host
    shape count, which only happens for a call carrying cross-sections. A
    failure on an update without them has nothing to compare the host count
    against, so the pair has to be left out: reporting it would describe a
    verification that never ran, and the removal probe behind it would take
    the caller's own cross-sections back off the loft while trying to reach a
    baseline of zero.
    """
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    created = module.main(
        action="create", name="duct_loft", cross_sections=["profile_a", "profile_b"]
    )
    assert created["success"] is True, created
    loft = runtime.getNodeByName("duct_loft")

    result = module.main(action="update", node_name="duct_loft", **refuse(monkeypatch, runtime, loft))

    assert result["success"] is False, result
    assert "observed_shape_count" not in result["data"]
    assert "expected_shape_count" not in result["data"]
    # Nothing this call added was left behind, and the two cross-sections the
    # caller registered before it were never taken back off the loft.
    assert loft.numShapes == 2
    assert runtime.getNodeByName("duct_loft") is loft


def test_loft_mesh_fails_when_the_constructor_returns_no_node(monkeypatch):
    """An object-only constructor cannot be placed, and says so."""
    runtime = _install(monkeypatch, FakeRuntime(loft_returns_object_only=True))
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    result = module.main(action="create", cross_sections=["profile_a", "profile_b"])

    assert result["success"] is False
    assert "scene node" in result["message"]


def test_loft_mesh_read_and_list_report_persisted_parameters(monkeypatch):
    """Stored loft parameters survive a read and show up in the listing."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    module.main(action="create", name="duct_loft", cross_sections=["profile_a", "profile_b"])
    read = module.main(action="read", node_name="duct_loft")
    listed = module.main(action="list")

    assert read["success"] is True
    assert read["data"]["cross_sections"] == ["profile_a", "profile_b"]
    assert len(listed["data"]["lofts"]) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "create", "cross_sections": ["profile_a"]},
        {"action": "create", "cross_sections": ["profile_a"] * 65},
        {"action": "create", "cross_sections": [""]},
        {"shape_steps": 0},
        {"path_steps": 101},
        {"cap_start": "yes"},
    ],
)
def test_loft_mesh_rejects_invalid_inputs(monkeypatch, overrides):
    """Validation failures happen before any loft is created."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")
    before = len(runtime.nodes)

    params = {"action": "create", "cross_sections": ["profile_a", "profile_b"]}
    params.update(overrides)
    result = module.main(**params)

    assert result["success"] is False
    assert len(runtime.nodes) == before


# ── boolean_operation ─────────────────────────────────────────────────


def _boolean_scene(runtime):
    runtime._register(Mesh("wall_block"))
    runtime._register(Mesh("window_opening"))
    runtime._register(Mesh("second_opening"))


def test_boolean_operation_creates_a_verified_subtraction(monkeypatch):
    """The mode and the operand count are both read back before success."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="subtraction",
        base_node="wall_block",
        operands=["window_opening"],
        name="wall_with_opening",
    )

    assert result["success"] is True, result
    assert result["data"]["operation"] == "subtraction"
    assert result["data"]["operation_code"] == 2
    assert result["data"]["operand_count"] == 2
    assert result["data"]["node"]["node_name"] == "wall_with_opening"


def test_boolean_operation_rejects_an_unregistered_operand(monkeypatch):
    """An operand the host refuses fails the call and removes the new node."""
    runtime = _install(monkeypatch, FakeRuntime(boolean_rejects_operands=True))
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is True


def test_boolean_operation_reports_a_coerced_mode(monkeypatch):
    """A host that keeps a different mode cannot be reported as configured."""
    runtime = _install(monkeypatch, FakeRuntime(boolean_coerces_op=True))
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="subtraction",
        base_node="wall_block",
        operands=["window_opening"],
    )

    assert result["success"] is False
    assert "kept operation 0 instead of the requested 2" in result["message"]
    assert result["data"]["rolled_back"] is True


def test_boolean_operation_fails_when_the_mode_cannot_be_read_back(monkeypatch):
    """A mode the host accepts but will not report back is not a success."""
    runtime = _install(monkeypatch, FakeRuntime(boolean_hides_operation=True))
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
    )

    assert result["success"] is False
    assert "cannot be read back" in result["message"]


def test_boolean_operation_routes_cut_to_the_class_that_supports_it(monkeypatch):
    """ProBoolean has no cut mode - 3 is Merge there - so cut must not reuse it."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="cut",
        base_node="wall_block",
        operands=["window_opening"],
        name="cut_solid",
    )

    assert result["success"] is True, result
    assert result["data"]["boolean_class"] == "Boolean2"
    assert result["data"]["operation_code"] == 5
    # The ProBoolean constructor must not have been used for a cut.
    boolean_node = runtime.getNodeByName("cut_solid")
    assert boolean_node.class_name == "Boolean2"


def test_boolean_operation_rejects_cut_when_only_proboolean_exists(monkeypatch):
    """A host with only ProBoolean reports that cut is unsupported, not merged."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    def no_boolean2():
        raise RuntimeError("no Boolean2 constructor")

    monkeypatch.setattr(runtime, "Boolean2", no_boolean2, raising=False)
    result = module.main(
        action="create",
        operation="cut",
        base_node="wall_block",
        operands=["window_opening"],
    )

    assert result["success"] is False
    assert "does not support the cut operation" in result["message"]


def test_boolean_operation_re_adjusts_operands(monkeypatch):
    """Swap, extract, add, and remove all run against a live boolean."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
        name="wall_boolean",
    )
    assert created["success"] is True
    node_name = created["data"]["node"]["node_name"]

    added = module.main(action="add_operands", node_name=node_name, operands=["second_opening"])
    assert added["success"] is True
    assert added["data"]["operand_count"] == 3

    swapped = module.main(
        action="set_operand", node_name=node_name, operand_index=3, operands=["wall_block"]
    )
    assert swapped["success"] is True
    assert swapped["data"]["operand"]["node_name"] == "wall_block"

    extracted = module.main(action="extract_operand", node_name=node_name, operand_index=2)
    assert extracted["success"] is True

    removed = module.main(action="remove_operand", node_name=node_name, operand_index=3)
    assert removed["success"] is True
    assert removed["data"]["operand_count"] == 2

    switched = module.main(action="set_operation", node_name=node_name, operation="intersection")
    assert switched["success"] is True
    assert switched["data"]["operation_code"] == 1

    read = module.main(action="read", node_name=node_name)
    assert read["success"] is True
    assert read["data"]["operation"] == "intersection"
    assert read["data"]["operand_count"] == 2


def test_boolean_operation_fails_when_no_boolean_constructor_exists(monkeypatch):
    """A host without a boolean class fails loudly instead of guessing."""
    runtime = _install(monkeypatch, FakeRuntime(no_boolean=True))
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
    )

    assert result["success"] is False
    assert "ProBoolean" in result["message"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "create", "base_node": None},
        {"action": "create", "operation": None},
        {"action": "create", "operation": "merge"},
        {"action": "create", "operands": []},
        {"action": "set_operation", "operation": None},
        {"action": "extract_operand", "operand_index": None},
        {"action": "set_operand", "operand_index": 0},
    ],
)
def test_boolean_operation_rejects_invalid_inputs(monkeypatch, overrides):
    """Validation failures happen before any boolean node is created."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")
    before = len(runtime.nodes)

    params = {"action": "create", "operation": "union", "base_node": "wall_block", "operands": ["window_opening"]}
    params.update(overrides)
    result = module.main(**params)

    assert result["success"] is False
    assert len(runtime.nodes) == before


# ── tools.yaml contracts ──────────────────────────────────────────────


def _tools(directory: Path) -> list:
    return yaml.safe_load((directory / "tools.yaml").read_text(encoding="utf-8"))["tools"]


def _tool(directory: Path, name: str) -> dict:
    return next(item for item in _tools(directory) if item["name"] == name)


@pytest.mark.parametrize(
    "tool_name",
    ["draw_spline", "inspect_curve", "edit_curve", "curve_model", "loft_mesh"],
)
def test_modeling_curve_tools_declare_bounded_main_thread_contracts(tool_name):
    """Every new modeling tool is bounded, main-thread, and fully annotated."""
    tool = _tool(MODELING_DIR, tool_name)

    assert tool["execution"] == "sync"
    assert tool["affinity"] == "main"
    assert tool["enforce_thread_affinity"] is True
    assert tool["input_schema"]["additionalProperties"] is False
    assert (MODELING_DIR / tool["source_file"]).is_file()
    for flag in ("read_only", "destructive", "idempotent"):
        assert isinstance(tool[flag], bool), flag
    for key in ("side_effects", "produces", "risk", "intent", "annotations"):
        assert key in tool, key
    if tool_name != "inspect_curve":
        assert isinstance(tool.get("undo"), dict), tool_name
        # Each of these performs several host writes with no undo hold, so the
        # grouping is not queryable: batch_call, never single_call.
        assert tool["undo"]["granularity"] == "batch_call"
        assert tool["undo"]["supported"] is True


def test_boolean_operation_declares_a_bounded_high_risk_contract():
    """The boolean tool is bounded, main-thread, and declares its undo entry."""
    tool = _tool(MESH_OPS_DIR, "boolean_operation")

    assert tool["execution"] == "sync"
    assert tool["affinity"] == "main"
    assert tool["enforce_thread_affinity"] is True
    assert tool["risk"] == "high"
    assert tool["input_schema"]["additionalProperties"] is False
    assert (MESH_OPS_DIR / tool["source_file"]).is_file()
    assert tool["undo"]["granularity"] == "batch_call"
    assert tool["undo"]["supported"] is True
    # remove_operand drops an operand, so the tool is destructive.
    assert tool["destructive"] is True
    assert tool["annotations"]["destructive_hint"] is True
    assert tool["side_effects"]["deletes"] is True


def test_edit_curve_requires_a_token_and_bounded_knot_edits():
    """The edit contract refuses an unguarded or unbounded knot edit."""
    tool = _tool(MODELING_DIR, "edit_curve")

    assert tool["input_schema"]["required"] == ["token", "knots"]
    assert tool["input_schema"]["properties"]["token"]["minLength"] == 1
    knots = tool["input_schema"]["properties"]["knots"]
    assert knots["minItems"] == 1
    assert knots["maxItems"] == 256
    assert knots["items"]["additionalProperties"] is False
    assert knots["items"]["required"] == ["index"]


def test_draw_spline_bounds_the_point_list():
    """A single call cannot push an unbounded point list onto the main thread."""
    tool = _tool(MODELING_DIR, "draw_spline")

    points = tool["input_schema"]["properties"]["points"]
    assert points["minItems"] == 2
    assert points["maxItems"] == 256
    assert points["items"]["minItems"] == points["items"]["maxItems"] == 3


# ── Regression tests for review findings ───────────────────────────────


def test_persistence_is_verified_through_the_native_user_property_channel(monkeypatch):
    """Stored parameters are read back through setUserPropVal/getUserPropVal."""
    runtime = _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(action="create", name="duct_profile", profile="rectangle", width=10, height=10)
    assert result["success"] is True, result

    node = runtime.getNodeByName("duct_profile")
    # The payload has to live in the native user property buffer, not in a
    # Python attribute the wrapper drops when the call returns.
    assert "dcc_mcp_curve_model" in node.user_properties
    assert runtime.getUserPropVal(node, "dcc_mcp_curve_model").startswith("{")


def test_persistence_fails_closed_when_the_native_channel_is_unavailable(monkeypatch):
    """No user property API means no persistence, and the call must say so."""
    runtime = _install(monkeypatch, FakeRuntime())
    monkeypatch.delattr(FakeRuntime, "setUserPropVal", raising=True)
    monkeypatch.delattr(FakeRuntime, "getUserPropVal", raising=True)
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(action="create", name="duct_profile", profile="rectangle", width=10, height=10)

    assert result["success"] is False
    assert "setUserPropVal" in result["data"]["store_error"]
    assert runtime.nodes == []


def test_persistence_fails_when_the_native_write_is_rejected(monkeypatch):
    """A user property write the host refuses must not count as stored."""
    runtime = _install(monkeypatch, FakeRuntime(no_user_props=True))
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(action="create", name="duct_profile", profile="rectangle", width=10, height=10)

    assert result["success"] is False
    assert "rejected" in result["data"]["store_error"]
    assert runtime.nodes == []


def test_persistence_fails_when_the_native_readback_differs(monkeypatch):
    """A buffer that keeps something else cannot be reported as stored."""
    runtime = _install(monkeypatch, FakeRuntime(user_prop_readback_tampered=True))
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(action="create", name="duct_profile", profile="rectangle", width=10, height=10)

    assert result["success"] is False
    assert "kept" in result["data"]["store_error"]
    assert runtime.nodes == []


@pytest.mark.parametrize("bad_operand", [1, None, [1, 2], 3.5, object()])
def test_boolean_operation_rejects_operand_entries_that_are_not_nodes(monkeypatch, bad_operand):
    """Only names, name/handle objects, and nodes may reach the host."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=[bad_operand],
    )

    assert result["success"] is False
    assert "operands entries" in result["message"]
    assert not runtime.deleted


def test_boolean_operation_reports_an_unconfirmed_rollback(monkeypatch):
    """A delete the host ignores must not be reported as a completed rollback."""
    runtime = _install(monkeypatch, FakeRuntime(boolean_rejects_operation=True, delete_is_noop=True))
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False
    assert len(runtime.nodes) == 4  # three meshes plus the orphaned boolean


def test_add_operands_verifies_the_count_grew(monkeypatch):
    """A host that accepts the call but registers nothing must fail the call."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
        name="wall_boolean",
    )
    assert created["success"] is True

    boolean_node = runtime.getNodeByName("wall_boolean")

    def silent_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(boolean_node, "add_operand", silent_noop)
    result = module.main(action="add_operands", node_name="wall_boolean", operands=["second_opening"])

    assert result["success"] is False
    assert "left 2 operand(s) instead of the expected 3" in result["message"]


def test_remove_operand_verifies_the_count_dropped(monkeypatch):
    """A removal the host ignores is reported as a failure, not a success."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening", "second_opening"],
        name="wall_boolean",
    )
    assert created["success"] is True
    boolean_node = runtime.getNodeByName("wall_boolean")
    monkeypatch.setattr(boolean_node, "RemoveOp", lambda index: None)

    result = module.main(action="remove_operand", node_name="wall_boolean", operand_index=3)

    assert result["success"] is False
    assert "left 3 operand(s) instead of the expected 2" in result["message"]


def test_extract_operand_requires_the_operand_list_to_survive(monkeypatch):
    """Extraction copies an operand out; it must not shrink the list."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
        name="wall_boolean",
    )
    assert created["success"] is True
    boolean_node = runtime.getNodeByName("wall_boolean")

    def destructive_extract(index):
        boolean_node.operands.pop(index - 1)

    monkeypatch.setattr(boolean_node, "ExtractOp", destructive_extract)
    result = module.main(action="extract_operand", node_name="wall_boolean", operand_index=2)

    assert result["success"] is False
    assert "left 1 operand(s) instead of the expected 2" in result["message"]


def test_set_operand_validates_the_index_before_calling_the_host(monkeypatch):
    """An out-of-range slot must not be dispatched to the host at all."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
        name="wall_boolean",
    )
    assert created["success"] is True
    boolean_node = runtime.getNodeByName("wall_boolean")
    calls = []
    monkeypatch.setattr(boolean_node, "SetOp", lambda index, node: calls.append(index))

    result = module.main(
        action="set_operand", node_name="wall_boolean", operand_index=9, operands=["wall_block"]
    )

    assert result["success"] is False
    assert "out of range" in result["message"]
    assert calls == []


# ── Regression tests for the second review round ──────────────────────


def test_boolean_create_reports_rollback_when_an_operand_cannot_be_resolved(monkeypatch):
    """An unresolvable operand still has to report whether cleanup worked."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["ghost_operand"],
    )

    assert result["success"] is False
    assert "rolled_back" in result["data"]
    assert result["data"]["rolled_back"] is True


def test_boolean_create_reports_an_unconfirmed_rollback_on_resolution_failure(monkeypatch):
    """A cleanup the host ignores must not be reported as a completed rollback."""
    runtime = _install(monkeypatch, FakeRuntime(delete_is_noop=True))
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    result = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["ghost_operand"],
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False


def test_curve_model_delete_fails_when_the_parameters_cannot_be_cleared(monkeypatch):
    """An unconfirmed metadata removal must not be reported as cleared."""
    runtime = _install(monkeypatch, FakeRuntime())
    module = _load(MODELING_DIR, "action_curve_model.py")

    module.main(action="create", name="duct_profile", profile="rectangle", width=10, height=10)
    # A getter that keeps reporting a payload makes the removal unconfirmable.
    monkeypatch.setattr(runtime, "getUserPropVal", lambda node, key: "still here")

    result = module.main(action="delete", node_name="duct_profile")

    assert result["success"] is False
    assert "could not be cleared" in result["message"]
    assert result["data"]["removed"] is False


def test_curve_model_update_leaves_the_original_spline_intact_on_failure(monkeypatch):
    """A failed update must not destroy the spline it was meant to replace."""
    runtime = _install(monkeypatch, FakeRuntime())
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")
    model = _load(MODELING_DIR, "action_curve_model.py")

    model.main(action="create", name="profile_a", profile="rectangle", width=40, height=20)
    before = inspect.main(node_name="profile_a")
    assert before["success"] is True
    original_knots = [knot["position"] for knot in before["data"]["splines"][0]["knots"]]

    original_add_knot = runtime.addKnot

    def failing_add_knot(shape, spline_index, knot_type, segment_type, point):
        # Fail on the third knot of the replacement spline only.
        if spline_index == 2 and len(shape.splines[spline_index - 1].knots) >= 2:
            raise RuntimeError("host refused the third knot")
        original_add_knot(shape, spline_index, knot_type, segment_type, point)

    monkeypatch.setattr(runtime, "addKnot", failing_add_knot)
    result = model.main(
        action="update",
        name="profile_a",
        node_name="profile_a",
        profile="rounded_rect",
        width=80,
        height=20,
        corner_radius=5,
    )

    assert result["success"] is False
    after = inspect.main(node_name="profile_a")
    assert after["success"] is True
    assert after["data"]["spline_count"] == 1
    assert [knot["position"] for knot in after["data"]["splines"][0]["knots"]] == original_knots
    assert result["data"]["discarded_replacement"] is True


def test_curve_model_update_replaces_only_after_the_profile_is_verified(monkeypatch):
    """The destructive delete happens last, and the final index is re-checked."""
    _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")
    model = _load(MODELING_DIR, "action_curve_model.py")

    model.main(action="create", name="profile_a", profile="rectangle", width=40, height=20)
    draw.main(points=[[0, 200, 0], [10, 200, 0]], mode="append", node_name="profile_a", spline_index=2)

    result = model.main(
        action="update",
        name="profile_a",
        node_name="profile_a",
        profile="rectangle",
        width=80,
        height=20,
    )

    assert result["success"] is True, result
    after = inspect.main(node_name="profile_a")
    assert after["data"]["spline_count"] == 2
    # The rebuilt profile lands at the last index and the other spline is intact.
    assert [knot["position"] for knot in after["data"]["splines"][1]["knots"]] == [
        [-40.0, -10.0, 0.0],
        [40.0, -10.0, 0.0],
        [40.0, 10.0, 0.0],
        [-40.0, 10.0, 0.0],
    ]
    assert [knot["position"] for knot in after["data"]["splines"][0]["knots"]] == [
        [0.0, 200.0, 0.0],
        [10.0, 200.0, 0.0],
    ]


# ── Regression tests for the exact-head review findings ───────────────


def _create_cut_boolean(module, runtime, name="cut_solid"):
    """Build a Boolean2-backed boolean via the `cut` operation."""
    return module.main(
        action="create",
        operation="cut",
        base_node="wall_block",
        operands=["window_opening"],
        name=name,
    )


def test_loft_update_with_new_cross_sections_extends_the_loft(monkeypatch):
    """Update supplies sections on top of the ones the node already holds."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    model = _load(MODELING_DIR, "action_curve_model.py")
    model.main(action="create", name="second_section", profile="circle", radius=8, segments=12)
    model.main(action="create", name="third_section", profile="circle", radius=6, segments=12)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    created = module.main(
        action="create", name="duct_loft", cross_sections=["profile_a", "profile_b"]
    )
    assert created["success"] is True, created

    updated = module.main(
        action="update", node_name="duct_loft", cross_sections=["second_section", "third_section"]
    )

    assert updated["success"] is True, updated
    assert updated["data"]["registered_before"] == 2
    assert updated["data"]["registered_shape_count"] == 4
    assert updated["data"]["cross_section_count"] == 2

    # The stored record is the union, not just the sections from this call.
    read = module.main(action="read", node_name="duct_loft")
    assert read["data"]["cross_sections"] == [
        "profile_a",
        "profile_b",
        "second_section",
        "third_section",
    ]
    assert read["data"]["cross_section_count"] == 4


def test_loft_rollback_reports_an_unconfirmed_delete(monkeypatch):
    """A cleanup the host ignores must not be reported as a completed rollback."""
    runtime = _install(monkeypatch, FakeRuntime(loft_rejects_shapes=True, delete_is_noop=True))
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")

    result = module.main(action="create", cross_sections=["profile_a", "profile_b"])

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False
    assert not isinstance(result["message"], bool)


def test_detect_adapter_uses_boolean2_codes_for_the_legacy_class_name(monkeypatch):
    """A node reported as "Boolean" must not be read through the ProBoolean map."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = _create_cut_boolean(module, runtime)
    assert created["success"] is True, created
    boolean_node = runtime.getNodeByName("cut_solid")
    assert boolean_node.class_name == "Boolean2"
    # The legacy name the case is named for: report the node as "Boolean" and
    # it still has to resolve through the Boolean2 map, never the ProBoolean one.
    boolean_node.class_name = "Boolean"

    # Boolean2 union is 1; the ProBoolean map would call the same code
    # "intersection". Both must be resolved through the Boolean2 map.
    set_result = module.main(
        action="set_operation", node_name="cut_solid", operation="union"
    )
    assert set_result["success"] is True, set_result
    assert set_result["data"]["boolean_class"] == "Boolean2"
    assert set_result["data"]["operation_code"] == 1

    read = module.main(action="read", node_name="cut_solid")
    assert read["success"] is True
    assert read["data"]["boolean_class"] == "Boolean2"
    assert read["data"]["operation"] == "union"
    assert read["data"]["operation_code"] == 1


def test_class_id_probe_does_not_shadow_the_class_name_fallback(monkeypatch):
    """A host probe that answers with a class id must not mask the real name."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = _create_cut_boolean(module, runtime)
    assert created["success"] is True, created
    boolean_node = runtime.getNodeByName("cut_solid")
    boolean_node.class_name = "Boolean2"
    # Code 2 is intersection on Boolean2 and subtraction on ProBoolean, so
    # only the class name can settle it.
    boolean_node.bool_op = 2

    def _failing_class_of(node):
        raise RuntimeError("classOf is unavailable on this host")

    def _class_id(node):
        # MAXScript's getClassId answers with a class id, never a class name.
        return "#(1234, 0)"

    monkeypatch.setattr(runtime, "classOf", _failing_class_of, raising=False)
    monkeypatch.setattr(runtime, "getClassId", _class_id, raising=False)

    read = module.main(action="read", node_name="cut_solid")
    assert read["success"] is True, read
    assert read["data"]["boolean_class"] == "Boolean2"
    assert read["data"]["operation"] == "intersection"
    assert read["data"]["operation_code"] == 2


def test_detect_adapter_reports_ambiguity_instead_of_guessing(monkeypatch):
    """An unrecognisable class plus an ambiguous code is an error, not a coin flip."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = _create_cut_boolean(module, runtime)
    assert created["success"] is True, created
    boolean_node = runtime.getNodeByName("cut_solid")
    # A class the adapters do not recognise, with a mode code (2) both maps own.
    boolean_node.class_name = "SomeFutureBoolean"
    boolean_node.bool_op = 2

    read = module.main(action="read", node_name="cut_solid")
    assert read["success"] is False
    assert "ambiguous" in read["message"]

    set_result = module.main(
        action="set_operation", node_name="cut_solid", operation="subtraction"
    )
    assert set_result["success"] is False
    assert "ambiguous" in set_result["message"]
    # Guessing would have written code 2, which is intersection on Boolean2.
    assert boolean_node.bool_op == 2


def test_detect_adapter_falls_back_to_the_operand_count_without_a_mode(monkeypatch):
    """A node with no readable mode still resolves through its operand count."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    created = _create_cut_boolean(module, runtime)
    assert created["success"] is True, created
    boolean_node = runtime.getNodeByName("cut_solid")
    boolean_node.hide_operation = True

    result = module.main(action="add_operands", node_name="cut_solid", operands=["second_opening"])

    assert result["success"] is True, result
    assert result["data"]["operand_count"] == 3


def test_operation_label_is_resolved_per_adapter(monkeypatch):
    """The reverse mode map must follow the class, not a shared table."""
    runtime = _install(monkeypatch, FakeRuntime())
    _boolean_scene(runtime)
    module = _load(MESH_OPS_DIR, "action_boolean_operation.py")

    # ProBoolean: 0 union, 1 intersection, 2 subtraction.
    union = module.main(
        action="create",
        operation="union",
        base_node="wall_block",
        operands=["window_opening"],
        name="pb_union",
    )
    assert union["success"] is True, union
    assert union["data"]["boolean_class"] == "ProBoolean"
    assert union["data"]["operation_code"] == 0
    assert module.main(action="read", node_name="pb_union")["data"]["operation"] == "union"

    intersection = module.main(
        action="create",
        operation="intersection",
        base_node="wall_block",
        operands=["window_opening"],
        name="pb_intersection",
    )
    assert intersection["success"] is True, intersection
    assert intersection["data"]["operation_code"] == 1
    assert (
        module.main(action="read", node_name="pb_intersection")["data"]["operation"]
        == "intersection"
    )

    # Boolean2: 1 union, 2 intersection, 3 subtraction, 5 cut.
    cut = _create_cut_boolean(module, runtime, name="b2_cut")
    assert cut["data"]["boolean_class"] == "Boolean2"
    assert cut["data"]["operation_code"] == 5
    assert module.main(action="read", node_name="b2_cut")["data"]["operation"] == "cut"


def test_curve_model_delete_fails_when_the_node_cannot_be_removed(monkeypatch):
    """An unconfirmed node deletion must not be reported as deleted."""
    runtime = _install(monkeypatch, FakeRuntime(delete_is_noop=True))
    module = _load(MODELING_DIR, "action_curve_model.py")

    module.main(action="create", name="duct_profile", profile="rectangle", width=10, height=10)
    result = module.main(action="delete", node_name="duct_profile", delete_node=True)

    assert result["success"] is False
    assert result["data"]["removed"] is False
    assert runtime.getNodeByName("duct_profile") is not None


def test_create_spline_shape_returns_a_built_node_for_rollback(monkeypatch):
    """A node built before the failure has to reach the caller for cleanup."""
    runtime = _install(monkeypatch, FakeRuntime())
    monkeypatch.delattr(FakeRuntime, "addNewSpline", raising=True)
    module = _load(MODELING_DIR, "action_curve_model.py")

    result = module.main(
        action="create", name="leaked_profile", profile="rectangle", width=10, height=10
    )

    assert result["success"] is False
    assert "addNewSpline" in result["message"]
    # The shape is created before addNewSpline is missed, so it must be gone.
    assert runtime.nodes == []


def test_edit_curve_rollback_restores_the_knot_type(monkeypatch):
    """A rollback has to restore the knot type, not just the positions."""
    runtime = _install(monkeypatch, FakeRuntime())
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    inspect = _load(MODELING_DIR, "action_inspect_curve.py")
    edit = _load(MODELING_DIR, "action_edit_curve.py")

    draw.main(points=[[0, 0, 0], [25, 0, 10], [50, 0, 0]], name="curve_a")
    token = inspect.main(node_name="curve_a")["data"]["token"]
    assert inspect.main(node_name="curve_a")["data"]["splines"][0]["knots"][1][
        "knot_type"
    ] == "corner"

    original = runtime.setOutVec

    def failing_setter(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "setOutVec", failing_setter)
    # Change the type and the handle together so the failure happens mid-write.
    result = edit.main(
        node_name="curve_a",
        token=token,
        knots=[{"index": 2, "knot_type": "bezier", "out_vec": [1, 0, 0]}],
    )

    assert result["success"] is False
    assert original is not None
    after = inspect.main(node_name="curve_a")
    knot = after["data"]["splines"][0]["knots"][1]
    # The rollback must have put the type back, not left "bezier" behind.
    assert knot["knot_type"] == "corner"


# ── Regression tests for the third review round ───────────────────────


def _loft_with_two_sections(module, model, name="duct_loft"):
    model.main(action="create", name="second_section", profile="circle", radius=8, segments=12)
    model.main(action="create", name="third_section", profile="circle", radius=6, segments=12)
    created = module.main(action="create", name=name, cross_sections=["profile_a", "profile_b"])
    assert created["success"] is True, created
    return created


def test_loft_update_takes_partial_sections_back_off_on_failure(monkeypatch):
    """An update that half-registers must not leave the caller's loft modified."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")
    model = _load(MODELING_DIR, "action_curve_model.py")
    _loft_with_two_sections(module, model)
    loft = runtime.getNodeByName("duct_loft")

    model.main(action="create", name="fourth_section", profile="circle", radius=4, segments=12)

    original_add = loft.addShape

    def failing_second_add(node, param=None):
        if len(loft.shapes) >= 3:
            raise RuntimeError("host refused the third cross-section")
        return original_add(node, param)

    monkeypatch.setattr(loft, "addShape", failing_second_add)
    result = module.main(
        action="update", node_name="duct_loft", cross_sections=["second_section", "fourth_section"]
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False
    # The loft the caller already owned is back to its two original sections.
    assert result["data"]["restored"] is True
    assert result["data"]["observed_shape_count"] == 2
    assert result["data"]["expected_shape_count"] == 2
    assert len(loft.shapes) == 2


def test_loft_update_reports_an_unrestorable_partial_add(monkeypatch):
    """When the host offers no removal call, the partial state is still reported."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")
    model = _load(MODELING_DIR, "action_curve_model.py")
    _loft_with_two_sections(module, model)
    loft = runtime.getNodeByName("duct_loft")

    model.main(action="create", name="fourth_section", profile="circle", radius=4, segments=12)

    def no_removal_api(*_args, **_kwargs):
        raise AttributeError("deleteShape")

    monkeypatch.setattr(loft, "deleteShape", no_removal_api, raising=False)
    original_add = loft.addShape

    def failing_second_add(node, param=None):
        if len(loft.shapes) >= 3:
            raise RuntimeError("host refused the third cross-section")
        return original_add(node, param)

    monkeypatch.setattr(loft, "addShape", failing_second_add)
    result = module.main(
        action="update", node_name="duct_loft", cross_sections=["second_section", "fourth_section"]
    )

    assert result["success"] is False
    # Never claim a clean rollback: the caller is told the loft is still dirty.
    assert result["data"]["restored"] is False
    assert result["data"]["observed_shape_count"] == 3
    assert result["data"]["expected_shape_count"] == 2


def test_loft_update_preserves_the_previous_path_and_surface_metadata(monkeypatch):
    """A cumulative update must not drop fields this call did not supply."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    draw = _load(MODELING_DIR, "action_draw_spline.py")
    draw.main(points=[[0, 0, 0], [0, 0, 100]], name="duct_path")

    module = _load(MODELING_DIR, "action_loft_mesh.py")
    created = module.main(
        action="create",
        name="duct_loft",
        cross_sections=["profile_a", "profile_b"],
        path_node="duct_path",
        shape_steps=4,
        cap_start=True,
    )
    assert created["success"] is True, created

    # A later update touches only one surface flag.
    updated = module.main(action="update", node_name="duct_loft", cap_end=True)
    assert updated["success"] is True, updated

    read = module.main(action="read", node_name="duct_loft")
    assert read["data"]["path_node"] == "duct_path"
    assert read["data"]["surface_params"]["shape_steps"] == 4
    assert read["data"]["surface_params"]["cap_start"] is True
    assert read["data"]["surface_params"]["cap_end"] is True
    assert read["data"]["cross_sections"] == ["profile_a", "profile_b"]


def test_loft_update_records_a_repeated_cross_section_per_registration(monkeypatch):
    """A repeated section is a real extra shape, so it is recorded each time."""
    runtime = _install(monkeypatch, FakeRuntime())
    _two_profiles(monkeypatch, runtime)
    module = _load(MODELING_DIR, "action_loft_mesh.py")
    model = _load(MODELING_DIR, "action_curve_model.py")
    _loft_with_two_sections(module, model)

    updated = module.main(
        action="update", node_name="duct_loft", cross_sections=["profile_a", "profile_a"]
    )
    assert updated["success"] is True, updated
    assert updated["data"]["registered_shape_count"] == 4

    read = module.main(action="read", node_name="duct_loft")
    assert read["data"]["cross_sections"] == [
        "profile_a",
        "profile_b",
        "profile_a",
        "profile_a",
    ]
    assert read["data"]["cross_section_count"] == 4


def test_boolean_operation_row_is_in_the_destructive_undo_table():
    """A destructive tool's undo row belongs in the destructive table."""
    doc = (ROOT / "docs" / "UNDO.md").read_text(encoding="utf-8")
    destructive = doc.split("## Destructive tools", 1)[1]
    non_destructive = doc.split("## Destructive tools", 1)[0]

    assert "`3dsmax-mesh-ops__boolean_operation`" in destructive
    assert "`3dsmax-mesh-ops__boolean_operation`" not in non_destructive
