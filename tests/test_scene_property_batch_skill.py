"""Offline tests for generic property access and batch object operations.

These tests use a fake pymxs runtime so the 3ds Max host is never required.
Every write tool is exercised on both the success path and the failure path to
prove no write can report success after the host rejected it.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scene"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_prop_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Fake pymxs value types ──────────────────────────────────────────────


class _Point3:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __eq__(self, other):
        return (
            isinstance(other, _Point3)
            and math.isclose(self.x, other.x)
            and math.isclose(self.y, other.y)
            and math.isclose(self.z, other.z)
        )

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __repr__(self):
        return "Point3({}, {}, {})".format(self.x, self.y, self.z)


class _EulerAngles(_Point3):
    pass


class _Color:
    def __init__(self, r=0.0, g=0.0, b=0.0):
        self.r = float(r)
        self.g = float(g)
        self.b = float(b)

    def __eq__(self, other):
        return (
            isinstance(other, _Color)
            and math.isclose(self.r, other.r)
            and math.isclose(self.g, other.g)
            and math.isclose(self.b, other.b)
        )


class _Matrix3:
    """Minimal 4x3 transform: three basis rows plus a translation row.

    Mirrors the 3ds Max row-vector convention, where ``v * M`` transforms a
    point and ``A * B`` applies ``A`` before ``B``. Getting this right is what
    makes ``translation * tm`` a local-space move and ``tm * translation`` a
    world-space move.
    """

    def __init__(self, rows=None):
        if rows is None:
            rows = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]]
        self.rows = [list(row) for row in rows]

    def __getitem__(self, index):
        return _Point3(*self.rows[index])

    def __mul__(self, other):
        if isinstance(other, _Point3):
            vector = [other.x, other.y, other.z]
            return _Point3(
                sum(vector[i] * self.rows[i][0] for i in range(3)) + self.rows[3][0],
                sum(vector[i] * self.rows[i][1] for i in range(3)) + self.rows[3][1],
                sum(vector[i] * self.rows[i][2] for i in range(3)) + self.rows[3][2],
            )
        left = [[self.rows[r][c] for c in range(3)] for r in range(3)]
        right = [[other.rows[r][c] for c in range(3)] for r in range(3)]
        product = [[sum(left[r][k] * right[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
        translation = [sum(self.rows[3][k] * right[k][c] for k in range(3)) + other.rows[3][c] for c in range(3)]
        return _Matrix3([product[0], product[1], product[2], translation])


def _rotation_x(degrees):
    rad = math.radians(degrees)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return _Matrix3(
        [
            [1.0, 0.0, 0.0],
            [0.0, cos_a, sin_a],
            [0.0, -sin_a, cos_a],
            [0.0, 0.0, 0.0],
        ]
    )


def _rotation_y(degrees):
    rad = math.radians(degrees)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return _Matrix3(
        [
            [cos_a, 0.0, -sin_a],
            [0.0, 1.0, 0.0],
            [sin_a, 0.0, cos_a],
            [0.0, 0.0, 0.0],
        ]
    )


def _rotation_z(degrees):
    rad = math.radians(degrees)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return _Matrix3(
        [
            [cos_a, sin_a, 0.0],
            [-sin_a, cos_a, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 0.0],
        ]
    )


# ── Fake scene node ─────────────────────────────────────────────────────


class _BaseObject:
    """Stand-in for node.baseObject; identity decides copy vs instance."""

    def __init__(self, kind="Box"):
        self.kind = kind


class _FakeNode:
    def __init__(self, name, handle, *, base_object=None, transform=None, is_frozen=False):
        self.name = name
        self.handle = handle
        self.baseObject = base_object if base_object is not None else _BaseObject()
        self.parent = None
        self.isHidden = False
        self.isFrozen = is_frozen
        self.wirecolor = _Color(1.0, 1.0, 1.0)
        self.visibility = 1.0
        self.renderable = True
        self.castShadows = True
        self._transform = transform if transform is not None else _Matrix3()
        self._scale = _Point3(1.0, 1.0, 1.0)
        self.pivot = _Point3(0.0, 0.0, 0.0)
        self.min = _Point3(-1.0, -1.0, -1.0)
        self.max = _Point3(1.0, 1.0, 1.0)
        self.material = None
        # "rejected" raises like a host that refuses a value; "swallowed"
        # silently ignores the write, which is the dangerous silent-success case.
        self.rejected_properties = set()
        self.swallowed_properties = set()

    # -- transform plumbing ------------------------------------------------

    @property
    def transform(self):
        return self._transform

    @transform.setter
    def transform(self, value):
        if "transform" in self.rejected_properties:
            raise RuntimeError("host rejected transform")
        self._transform = value

    @property
    def pos(self):
        return _Point3(*self._transform.rows[3])

    @pos.setter
    def pos(self, value):
        if "pos" in self.rejected_properties:
            raise RuntimeError("host rejected pos")
        if "pos" in self.swallowed_properties:
            return
        self._transform = _Matrix3(
            [
                self._transform.rows[0],
                self._transform.rows[1],
                self._transform.rows[2],
                [float(value.x), float(value.y), float(value.z)],
            ]
        )

    @property
    def rotation(self):
        """Extract euler degrees from the rotation part, order Rx*Ry*Rz."""
        rows = []
        for row in self._transform.rows[:3]:
            length = math.sqrt(sum(item * item for item in row))
            rows.append([item / length for item in row] if length > 1e-9 else list(row))
        sy = max(-1.0, min(1.0, -rows[0][2]))
        y_angle = math.asin(sy)
        cos_y = math.cos(y_angle)
        if abs(cos_y) < 1e-9:
            return _EulerAngles(0.0, math.degrees(y_angle), 0.0)
        x_angle = math.atan2(rows[1][2] / cos_y, rows[2][2] / cos_y)
        z_angle = math.atan2(rows[0][1] / cos_y, rows[0][0] / cos_y)
        return _EulerAngles(math.degrees(x_angle), math.degrees(y_angle), math.degrees(z_angle))

    @rotation.setter
    def rotation(self, value):
        if "rotation" in self.rejected_properties:
            raise RuntimeError("host rejected rotation")
        if "rotation" in self.swallowed_properties:
            return
        matrix = _rotation_x(float(value.x)) * _rotation_y(float(value.y)) * _rotation_z(float(value.z))
        scaled = [[matrix.rows[r][c] * self._scale_component(r) for c in range(3)] for r in range(3)]
        self._transform = _Matrix3([scaled[0], scaled[1], scaled[2], list(self._transform.rows[3])])

    def _scale_component(self, index):
        values = [self._scale.x, self._scale.y, self._scale.z]
        return values[index]

    @property
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        if "scale" in self.rejected_properties:
            raise RuntimeError("host rejected scale")
        self._scale = _Point3(float(value.x), float(value.y), float(value.z))

    def isHiddenGetter(self):  # pragma: no cover - mirrors pymxs callable style
        return self.isHidden


# ── Fake runtime ────────────────────────────────────────────────────────


class _MaxClass:
    """Stand-in for a MAXScript class value: callable and in the class hierarchy."""

    def __init__(self, name, factory, superclass="GeometryClass"):
        self.name = name
        self._factory = factory
        self.superclass = superclass

    def __call__(self, **kwargs):
        return self._factory(**kwargs)


class _SuperClassValue:
    """Callable stand-in for a MAXScript superclass such as GeometryClass.

    Depending on the pymxs mapping, ``superClassOf`` returns either a wrapper
    instance or the generated class object. A Python class is always callable,
    so this shape is what catches a ``callable()`` based rejection.
    """

    def __init__(self, name):
        self.name = name

    def __call__(self, *args, **kwargs):
        return None

    def __str__(self):
        return self.name


class _FakeRuntime:
    def __init__(self):
        self.hero = _FakeNode("hero_box", 42)
        self.helper = _FakeNode("hero_helper", 43)
        self.objects = [self.hero, self.helper]
        self.creations = []
        self.clones = []
        self._next_handle = 1000
        # Creatable classes are _MaxClass instances, so superClassOf proves them.
        self.Teapot = _MaxClass("Teapot", self._make_teapot)
        self.Tube = _MaxClass("Tube", self._make_tube)
        # Bare global functions: callable, but not classes, so superClassOf fails.
        # deleteFile / resetMaxFile are also blacklisted; unlistedGlobalFunc exercises
        # the positive class check on its own.
        self.deleteFile = lambda *args, **kwargs: None
        self.resetMaxFile = lambda *args, **kwargs: None
        self.unlistedGlobalFunc = lambda *args, **kwargs: None

    def superClassOf(self, symbol):  # noqa: N802 - mirrors pymxs runtime naming.
        if isinstance(symbol, _MaxClass):
            return symbol.superclass
        raise RuntimeError("superClassOf() requires a MAXWrapper")

    # -- value constructors ----------------------------------------------

    def Point3(self, x=0.0, y=0.0, z=0.0):  # noqa: N802
        return _Point3(x, y, z)

    def EulerAngles(self, x=0.0, y=0.0, z=0.0):  # noqa: N802
        return _EulerAngles(x, y, z)

    def Color(self, r=0.0, g=0.0, b=0.0):  # noqa: N802
        return _Color(r, g, b)

    def Matrix3(self, value):  # noqa: N802
        return _Matrix3()

    def transMatrix(self, point):  # noqa: N802
        return _Matrix3(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
                [float(point.x), float(point.y), float(point.z)],
            ]
        )

    def rotateXMatrix(self, degrees):  # noqa: N802
        return _rotation_x(degrees)

    def rotateYMatrix(self, degrees):  # noqa: N802
        return _rotation_y(degrees)

    def rotateZMatrix(self, degrees):  # noqa: N802
        return _rotation_z(degrees)

    # -- scene queries ----------------------------------------------------

    def getNodeByName(self, name):  # noqa: N802
        for node in self.objects:
            if node.name == name:
                return node
        return None

    # -- creatable classes -------------------------------------------------

    def _make_teapot(self, radius=10.0):
        return self._create("Teapot", radius=radius)

    def _make_tube(self, radius1=5.0, radius2=10.0, height=25.0):
        return self._create("Tube", radius1=radius1, radius2=radius2, height=height)

    def _create(self, class_name, **kwargs):
        node = _FakeNode("{}_{}".format(class_name.lower(), len(self.creations) + 1), self._next_handle)
        self._next_handle += 1
        node.min = _Point3(-1.0, -1.0, -1.0)
        node.max = _Point3(1.0, 1.0, 1.0)
        self.creations.append((class_name, kwargs))
        self.objects.append(node)
        return node

    # -- clone modes -------------------------------------------------------

    def copy(self, node):
        clone = _FakeNode("{}_copy".format(node.name), self._next_handle, base_object=_BaseObject())
        self._next_handle += 1
        self.objects.append(clone)
        self.clones.append(("copy", node.name))
        return clone

    def instance(self, node):
        clone = _FakeNode("{}_inst".format(node.name), self._next_handle, base_object=node.baseObject)
        self._next_handle += 1
        self.objects.append(clone)
        self.clones.append(("instance", node.name))
        return clone

    def reference(self, node):
        clone = _FakeNode("{}_ref".format(node.name), self._next_handle, base_object=_BaseObject(kind="Ref"))
        self._next_handle += 1
        self.objects.append(clone)
        self.clones.append(("reference", node.name))
        return clone

    def areNodesInstances(self, first, second):  # noqa: N802
        return first.baseObject is second.baseObject


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


# ── get_object_properties ───────────────────────────────────────────────


def test_get_object_properties_returns_compact_defaults(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_object_properties.py").main(node_name="hero_box")

    assert result["success"] is True
    values = result["data"]["properties"]
    assert values["name"] == "hero_box"
    assert values["pos"] == [0.0, 0.0, 0.0]
    assert values["wirecolor"] == [1.0, 1.0, 1.0]
    assert result["data"]["bounding_box"]["min"] == [-1.0, -1.0, -1.0]


def test_get_object_properties_reports_unknown_names(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_object_properties.py").main(
        node_name="hero_box", properties=["name", "does_not_exist"]
    )

    assert result["success"] is True
    assert result["data"]["properties"] == {"name": "hero_box"}
    assert result["data"]["unavailable"][0]["name"] == "does_not_exist"


def test_get_object_properties_fails_when_nothing_readable(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_object_properties.py").main(
        node_name="hero_box", properties=["nope_one", "nope_two"]
    )

    assert result["success"] is False
    assert len(result["data"]["unavailable"]) == 2


def test_get_object_properties_fails_for_missing_node(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_object_properties.py").main(node_name="ghost")

    assert result["success"] is False
    assert "No matching node" in result["message"]


def test_get_object_properties_rejects_empty_property_list(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_get_object_properties.py").main(node_name="hero_box", properties=["", "  "])

    assert result["success"] is False


# ── set_object_property ─────────────────────────────────────────────────


def test_set_object_property_writes_scalar_string(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="name", value="renamed_hero"
    )

    assert result["success"] is True
    assert result["data"]["verified"] is True
    assert result["data"]["value"] == "renamed_hero"
    assert result["data"]["previous"] == "hero_box"
    assert runtime.hero.name == "renamed_hero"


def test_set_object_property_writes_point3_from_mapping(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="pos", value={"x": 5.0, "y": 6.0, "z": 7.0}
    )

    assert result["success"] is True
    assert result["data"]["value"] == [5.0, 6.0, 7.0]
    assert runtime.hero.pos == _Point3(5.0, 6.0, 7.0)


def test_set_object_property_writes_color(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="wirecolor", value=[0.25, 0.5, 0.75]
    )

    assert result["success"] is True
    assert result["data"]["value"] == [0.25, 0.5, 0.75]
    assert runtime.hero.wirecolor == _Color(0.25, 0.5, 0.75)


def test_set_object_property_fails_for_unknown_property(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="not_a_property", value=1
    )

    assert result["success"] is False
    assert "does not exist" in result["message"]
    assert result["data"]["property"] == "not_a_property"


def test_set_object_property_fails_for_wrong_type(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="renderable", value="not-a-bool"
    )

    assert result["success"] is False
    assert "boolean" in result["message"]
    assert result["data"]["expected_type"] == "bool"
    assert runtime.hero.renderable is True


def test_set_object_property_fails_for_bad_vector_shape(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_object_property.py").main(node_name="hero_box", property="pos", value=[1.0, 2.0])

    assert result["success"] is False
    assert "x, y, and z" in result["message"]


def test_set_object_property_fails_when_host_rejects_write(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.rejected_properties.add("pos")

    result = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="pos", value=[1.0, 2.0, 3.0]
    )

    assert result["success"] is False
    assert "rejected" in result["message"]
    assert result["data"]["error"]


def test_set_object_property_fails_when_readback_differs(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    class _SwallowingNode(_FakeNode):
        @property
        def renderable(self):
            return True

        @renderable.setter
        def renderable(self, value):
            pass  # host silently ignores the write

    runtime.hero = _SwallowingNode("hero_box", 42)
    runtime.objects = [runtime.hero, runtime.helper]

    result = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="renderable", value=False
    )

    assert result["success"] is False
    assert "did not take effect" in result["message"]
    assert result["data"]["readback"] is True


def test_set_object_property_refuses_private_and_methods(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    private = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="_transform", value=None
    )
    method = _load_action("action_set_object_property.py").main(
        node_name="hero_box", property="isHiddenGetter", value=None
    )

    assert private["success"] is False
    assert "private" in private["message"]
    assert method["success"] is False
    assert "method" in method["message"]


def test_set_object_property_requires_property(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_set_object_property.py").main(node_name="hero_box", property="  ", value=1)

    assert result["success"] is False
    assert "required" in result["message"]


# ── batch_rename_objects ────────────────────────────────────────────────


def test_batch_rename_applies_pattern_with_numbering(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_batch_rename_objects.py").main(
        node_names=["hero_box", "hero_helper"], mode="pattern", base="prop_{index}", padding=2
    )

    assert result["success"] is True
    assert runtime.hero.name == "prop_01"
    assert runtime.helper.name == "prop_02"
    assert result["data"]["count"] == 2


def test_batch_rename_supports_prefix_replace_and_suffix(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    suffix = _load_action("action_batch_rename_objects.py").main(node_names=["hero_box"], base="_v2")
    prefix = _load_action("action_batch_rename_objects.py").main(node_names=["hero_box_v2"], mode="prefix", base="geo_")
    replaced = _load_action("action_batch_rename_objects.py").main(
        node_names=["geo_hero_box_v2"], mode="replace", search="geo_", base="env_"
    )

    assert suffix["success"] is True and runtime.hero.name == "env_hero_box_v2"
    assert prefix["success"] is True
    assert replaced["success"] is True


def test_batch_rename_rejects_duplicate_targets(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_batch_rename_objects.py").main(
        node_names=["hero_box", "hero_helper"], mode="pattern", base="same_{index}", start_index=1, padding=0
    )

    # padding=0 is invalid, so this exercises input validation first.
    assert result["success"] is False
    assert "padding" in result["message"]

    # A pattern without {index}/{name} collapses both nodes onto one name.
    duplicate = _load_action("action_batch_rename_objects.py").main(handles=[42, 43], mode="pattern", base="collides")
    assert duplicate["success"] is False
    assert "duplicate" in duplicate["message"]
    assert runtime.hero.name == "hero_box"
    assert runtime.helper.name == "hero_helper"


def test_batch_rename_rejects_empty_selection_and_bad_mode(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    empty = _load_action("action_batch_rename_objects.py").main(node_names=[], base="_x")
    bad_mode = _load_action("action_batch_rename_objects.py").main(node_names=["hero_box"], mode="shuffle")
    missing_search = _load_action("action_batch_rename_objects.py").main(
        node_names=["hero_box"], mode="replace", base="x"
    )

    assert empty["success"] is False
    assert bad_mode["success"] is False
    assert bad_mode["data"]["supported"] == ["suffix", "prefix", "replace", "pattern"]
    assert missing_search["success"] is False


def test_batch_rename_fails_when_readback_differs(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    original = runtime.hero.name

    class _Unrenamable(_FakeNode):
        @property
        def name(self):
            return original

        @name.setter
        def name(self, value):
            pass

    runtime.hero = _Unrenamable(original, 42)
    runtime.objects = [runtime.hero, runtime.helper]

    result = _load_action("action_batch_rename_objects.py").main(node_names=[original], base="_nope")

    assert result["success"] is False
    assert "did not take effect" in result["message"]


# ── create_object ───────────────────────────────────────────────────────


def test_create_object_returns_placement_feedback(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_create_object.py").main(
        object_type="Teapot",
        name="hero_teapot",
        position=[10.0, 20.0, 30.0],
        params={"radius": 42.0},
    )

    assert result["success"] is True
    assert result["data"]["node"]["node_name"] == "hero_teapot"
    assert result["data"]["placement"]["position"] == [10.0, 20.0, 30.0]
    assert result["data"]["bounding_box"]["max"] == [1.0, 1.0, 1.0]
    assert result["data"]["params"]["radius"] == 42.0
    assert runtime.creations[-1][1] == {"radius": 42.0}


def test_create_object_rejects_unknown_class(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_create_object.py").main(object_type="NotARealClass")

    assert result["success"] is False
    assert "no such class" in result["message"]


def test_create_object_rejects_blocked_and_malformed_names(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    blocked = _load_action("action_create_object.py").main(object_type="execute")
    malformed = _load_action("action_create_object.py").main(object_type="Box; delete $")

    assert blocked["success"] is False
    assert "not creatable" in blocked["message"]
    assert malformed["success"] is False
    assert "plain 3ds Max class name" in malformed["message"]


def test_create_object_reports_constructor_failure(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    def _broken_factory(**kwargs):
        raise TypeError("bad argument")

    # A proven class whose constructor raises must surface as a failure.
    runtime.Broken = _MaxClass("Broken", _broken_factory)
    broken = _load_action("action_create_object.py").main(object_type="Broken")

    assert broken["success"] is False
    assert "could not create" in broken["message"]


def test_create_object_rejects_bare_global_function(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    # Not on the blacklist, so only the positive class check can reject it.
    result = _load_action("action_create_object.py").main(object_type="unlistedGlobalFunc")

    assert result["success"] is False
    assert "not a creatable" in result["message"]
    assert result["data"]["object_type"] == "unlistedGlobalFunc"
    assert result["data"]["evidence"]["predicate"] == "superClassOf"


def test_create_object_accepts_class_when_superclass_is_callable(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    # A real host can map superClassOf onto a class object, which is callable.
    # Rejecting on callable() would break every legitimate creatable class.
    def _super_class_of(symbol):
        if isinstance(symbol, _MaxClass):
            return _SuperClassValue(symbol.superclass)
        raise RuntimeError("superClassOf() requires a MAXWrapper")

    monkeypatch.setattr(runtime, "superClassOf", _super_class_of, raising=False)

    result = _load_action("action_create_object.py").main(object_type="Teapot", name="real_host_teapot")

    assert result["success"] is True, result["message"]
    assert result["data"]["node"]["node_name"] == "real_host_teapot"
    assert result["data"]["creatable_class_evidence"]["superclass"] == "GeometryClass"
    assert result["data"]["creatable_class_evidence"]["superclass_callable"] is True
    assert runtime.creations[-1][0] == "Teapot"


def test_create_object_rejects_unproven_symbol_even_when_flag_unset(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    monkeypatch.delenv("DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT", raising=False)

    # The class gate is unconditional; it must not depend on the flag.
    result = _load_action("action_create_object.py").main(object_type="unlistedGlobalFunc")

    assert result["success"] is False
    assert "not a creatable" in result["message"]
    assert result["data"]["arbitrary_script_disabled"] is False


def test_create_object_rejects_blacklisted_destructive_global(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_create_object.py").main(object_type="resetMaxFile")

    assert result["success"] is False
    assert "not creatable" in result["message"]


def test_create_object_rejects_unproven_symbol_when_arbitrary_script_disabled(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    monkeypatch.setenv("DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT", "1")

    result = _load_action("action_create_object.py").main(object_type="unlistedGlobalFunc")

    assert result["success"] is False
    assert "DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT" in result["message"]
    assert result["data"]["arbitrary_script_disabled"] is True


def test_create_object_fails_closed_when_flag_set_and_no_class_predicate(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    monkeypatch.setenv("DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT", "1")
    monkeypatch.delattr(_FakeRuntime, "superClassOf")

    # Even a genuine class cannot be proven, so the call must fail closed.
    result = _load_action("action_create_object.py").main(object_type="Teapot")

    assert result["success"] is False
    assert "no creatable-class predicate" in result["message"]
    assert result["data"]["evidence"]["available"] is False


def test_create_object_allows_proven_class_even_when_flag_set(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    monkeypatch.setenv("DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT", "1")

    # The flag must not over-block: a proven creatable class still works.
    result = _load_action("action_create_object.py").main(object_type="Teapot", name="flagged_teapot")

    assert result["success"] is True
    assert result["data"]["node"]["node_name"] == "flagged_teapot"
    assert result["data"]["creatable_class_evidence"]["superclass"] == "GeometryClass"
    assert runtime.creations[-1][0] == "Teapot"


# ── transform_object ────────────────────────────────────────────────────


def test_transform_object_moves_in_world_space(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_transform_object.py").main(
        node_names=["hero_box"], move=[1.0, 2.0, 3.0], space="world"
    )

    assert result["success"] is True
    assert runtime.hero.pos == _Point3(1.0, 2.0, 3.0)
    assert result["data"]["nodes"][0]["after"]["position"] == [1.0, 2.0, 3.0]


def test_transform_object_local_move_uses_node_axes(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    # Rotate 90 degrees about Z, then a local +X move must land on world +Y.
    rotated = _load_action("action_transform_object.py").main(
        node_names=["hero_box"], rotate=[0.0, 0.0, 90.0], space="world"
    )
    assert rotated["success"] is True

    moved = _load_action("action_transform_object.py").main(
        node_names=["hero_box"], move=[2.0, 0.0, 0.0], space="local"
    )

    assert moved["success"] is True
    after = moved["data"]["nodes"][0]["after"]["position"]
    assert abs(after[0]) < 1e-6
    assert abs(after[1] - 2.0) < 1e-6
    assert abs(runtime.hero.pos.x) < 1e-9
    assert abs(runtime.hero.pos.y - 2.0) < 1e-9


def test_transform_object_scales_by_multiplier(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    first = _load_action("action_transform_object.py").main(node_names=["hero_box"], scale=[2.0, 2.0, 2.0])
    assert first["success"] is True
    assert runtime.hero.scale == _Point3(2.0, 2.0, 2.0)
    second = _load_action("action_transform_object.py").main(node_names=["hero_box"], scale=[0.5, 1.0, 1.0])

    assert first["success"] is True and second["success"] is True
    assert second["data"]["nodes"][0]["after"]["scale"] == [1.0, 2.0, 2.0]


def test_transform_object_absolute_mode_sets_position(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    _load_action("action_transform_object.py").main(node_names=["hero_box"], move=[9.0, 0.0, 0.0])
    result = _load_action("action_transform_object.py").main(
        node_names=["hero_box"], move=[4.0, 5.0, 6.0], relative=False
    )

    assert result["success"] is True
    assert runtime.hero.pos == _Point3(4.0, 5.0, 6.0)


def test_transform_object_rejects_bad_space_and_missing_operation(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    bad_space = _load_action("action_transform_object.py").main(node_names=["hero_box"], move=[1, 0, 0], space="screen")
    no_op = _load_action("action_transform_object.py").main(node_names=["hero_box"])
    bad_vector = _load_action("action_transform_object.py").main(node_names=["hero_box"], move=[1, 2])

    assert bad_space["success"] is False
    assert bad_space["data"]["supported"] == ["world", "local"]
    assert no_op["success"] is False
    assert "At least one" in no_op["message"]
    assert bad_vector["success"] is False


def test_transform_object_absolute_fails_when_position_is_swallowed(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.swallowed_properties.add("pos")

    result = _load_action("action_transform_object.py").main(
        node_names=["hero_box"], move=[4.0, 5.0, 6.0], relative=False
    )

    assert result["success"] is False
    assert "did not take effect" in result["message"]
    assert result["data"]["requested"] == [4.0, 5.0, 6.0]
    assert result["data"]["readback"] == [0.0, 0.0, 0.0]
    assert result["data"]["before"]["position"] == [0.0, 0.0, 0.0]


def test_transform_object_absolute_fails_when_rotation_is_swallowed(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.swallowed_properties.add("rotation")

    result = _load_action("action_transform_object.py").main(
        node_names=["hero_box"], rotate=[0.0, 0.0, 90.0], relative=False
    )

    assert result["success"] is False
    assert "did not take effect" in result["message"]
    assert result["data"]["requested"] == [0.0, 0.0, 90.0]
    assert result["data"]["before"]["rotation"] == [0.0, 0.0, 0.0]
    # Rotation is compared as a matrix, so equivalent euler forms do not fail.
    assert result["data"]["readback_rotation_rows"] == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]


def test_transform_object_fails_when_host_rejects_transform(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.hero.rejected_properties.add("transform")

    result = _load_action("action_transform_object.py").main(node_names=["hero_box"], move=[1.0, 1.0, 1.0])

    assert result["success"] is False
    assert "rejected" in result["message"]


def test_transform_object_reports_missing_node(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_transform_object.py").main(node_names=["ghost"], move=[1.0, 0.0, 0.0])

    assert result["success"] is False
    assert "could not be resolved" in result["message"]


# ── clone_objects ───────────────────────────────────────────────────────


def test_clone_objects_copy_does_not_share_geometry(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_clone_objects.py").main(node_names=["hero_box"], mode="copy", name_suffix="_copy")

    assert result["success"] is True
    assert result["data"]["clones"][0]["clone"]["node_name"] == "hero_box_copy"
    assert result["data"]["clones"][0]["shares_geometry"] is False
    assert runtime.clones[-1][0] == "copy"


def test_clone_objects_instance_shares_geometry(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_clone_objects.py").main(node_names=["hero_box"], mode="instance")

    assert result["success"] is True
    assert result["data"]["clones"][0]["shares_geometry"] is True
    clone = runtime.objects[-1]
    assert clone.baseObject is runtime.hero.baseObject


def test_clone_objects_reference_is_verified(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    result = _load_action("action_clone_objects.py").main(node_names=["hero_box"], mode="reference")

    assert result["success"] is True
    assert result["data"]["clones"][0]["shares_geometry"] is False
    assert "warnings" not in result["data"]
    assert runtime.objects[-1].baseObject is not runtime.hero.baseObject


def test_clone_objects_warns_when_reference_unverifiable(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    monkeypatch.delattr(_FakeRuntime, "areNodesInstances")

    result = _load_action("action_clone_objects.py").main(node_names=["hero_box"], mode="reference")

    assert result["success"] is True
    assert result["data"]["warnings"]
    assert "areNodesInstances" in result["data"]["warnings"][0]


def test_clone_objects_fails_when_mode_semantics_are_wrong(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    # Make instance() behave like copy(): the claim must be caught.
    runtime.instance = runtime.copy

    result = _load_action("action_clone_objects.py").main(node_names=["hero_box"], mode="instance")

    assert result["success"] is False
    assert "does not share" in result["message"]


def test_clone_objects_rejects_unknown_mode(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_clone_objects.py").main(node_names=["hero_box"], mode="mirror")

    assert result["success"] is False
    assert result["data"]["supported"] == ["copy", "instance", "reference"]


# ── analyze_node_orientation ────────────────────────────────────────────


def test_analyze_node_orientation_reports_axes_for_identity(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_analyze_node_orientation.py").main(node_name="hero_box")

    assert result["success"] is True
    data = result["data"]
    assert data["local_axes"]["x"]["angle_deg"] == 0.0
    assert data["axis_aligned"] is True
    assert data["pivot"]["value"] == [0.0, 0.0, 0.0]
    assert data["transform"]["rows"][3] == [0.0, 0.0, 0.0]
    assert data["identity_transform"] is True


def test_analyze_node_orientation_measures_rotation_drift(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    _load_action("action_transform_object.py").main(node_names=["hero_box"], rotate=[0.0, 0.0, 90.0], space="world")

    result = _load_action("action_analyze_node_orientation.py").main(node_name="hero_box")

    assert result["success"] is True
    assert result["data"]["axis_aligned"] is False
    assert abs(result["data"]["local_axes"]["x"]["angle_deg"] - 90.0) < 1e-3
    assert result["data"]["identity_transform"] is False


def test_analyze_node_orientation_reports_missing_node(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    result = _load_action("action_analyze_node_orientation.py").main(node_name="ghost")

    assert result["success"] is False
    assert "No matching node" in result["message"]


# ── set_visibility freeze support ───────────────────────────────────────


def test_set_visibility_freeze_and_unfreeze(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    frozen = _load_action("action_set_visibility.py").main(node_names=["hero_box"], state="freeze")
    assert frozen["success"] is True
    assert runtime.hero.isFrozen is True

    unfrozen = _load_action("action_set_visibility.py").main(node_names=["hero_box"], state="unfreeze")
    assert unfrozen["success"] is True
    assert runtime.hero.isFrozen is False


def test_set_visibility_boolean_flag_still_works(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    hidden = _load_action("action_set_visibility.py").main(node_names=["hero_box"], visible=False)
    assert hidden["success"] is True
    assert runtime.hero.isHidden is True
    assert hidden["data"]["visible"] is False

    shown = _load_action("action_set_visibility.py").main(node_names=["hero_box"], visible=True)
    assert shown["success"] is True
    assert runtime.hero.isHidden is False


def test_set_visibility_rejects_bad_state_and_missing_arguments(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    bad_state = _load_action("action_set_visibility.py").main(node_names=["hero_box"], state="invisible")
    missing = _load_action("action_set_visibility.py").main(node_names=["hero_box"])

    assert bad_state["success"] is False
    assert bad_state["data"]["supported"] == ["show", "hide", "freeze", "unfreeze"]
    assert missing["success"] is False


def test_set_visibility_fails_when_freeze_is_ignored(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    class _Unfreezable(_FakeNode):
        @property
        def isFrozen(self):
            return False

        @isFrozen.setter
        def isFrozen(self, value):
            pass  # host silently ignores the write

    runtime.hero = _Unfreezable("hero_box", 42)
    runtime.objects = [runtime.hero, runtime.helper]

    result = _load_action("action_set_visibility.py").main(node_names=["hero_box"], state="freeze")

    assert result["success"] is False
    assert "did not take effect" in result["message"]
