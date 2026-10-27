"""Contract regressions for typed animation and rigging vocabulary."""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

ANIMATION_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-animation"
RIGGING_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-rigging"


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _Node:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.parent = None
        self.isHidden = False
        self.position = [0.0, 0.0, 0.0]
        self.rotation = [0.0, 0.0, 0.0]
        self.scale = [1.0, 1.0, 1.0]
        self.keyframes = []
        self.modifiers = []


class _Skin:
    def __init__(self) -> None:
        self.name = "Skin"
        self.bone_names = {1: "root_bone", 2: "tip_bone"}
        self.weights = {1: {1: 1.0}, 2: {1: 0.5, 2: 0.5}}


class _SkinOps:
    def GetNumberVertices(self, skin):  # noqa: N802 - mirrors pymxs.
        return len(skin.weights)

    def GetNumberBones(self, skin):  # noqa: N802 - mirrors pymxs.
        return len(skin.bone_names)

    def GetBoneName(self, skin, bone_id, _name_flag):  # noqa: N802 - mirrors pymxs.
        return skin.bone_names[bone_id]

    def GetVertexWeightCount(self, skin, vertex):  # noqa: N802 - mirrors pymxs.
        return len(skin.weights[vertex])

    def GetVertexWeightBoneID(self, skin, vertex, influence):  # noqa: N802 - mirrors pymxs.
        return list(skin.weights[vertex])[influence - 1]

    def GetVertexWeight(self, skin, vertex, influence):  # noqa: N802 - mirrors pymxs.
        return list(skin.weights[vertex].values())[influence - 1]

    def isUnNormalizeVertex(self, skin, vertex):  # noqa: N802 - mirrors pymxs.
        return not abs(sum(skin.weights[vertex].values()) - 1.0) <= 1e-6

    def unNormalizeVertex(self, _skin, _vertex, _enabled):  # noqa: N802 - mirrors pymxs.
        return None

    def ReplaceVertexWeights(self, skin, vertex, bone_ids, weights):  # noqa: N802 - mirrors pymxs.
        skin.weights[vertex] = dict(zip(bone_ids, weights))


class _Constraint:
    def __init__(self) -> None:
        self.targets = []
        self.relative = False

    def appendTarget(self, target, weight):  # noqa: N802 - mirrors pymxs.
        self.targets.append((target, weight))


class _Runtime:
    def __init__(self) -> None:
        self.objects = [
            _Node("rotor_main", 1),
            _Node("rotor_tail", 2),
            _Node("hero_mesh", 3),
            _Node("target_mesh", 4),
        ]
        self.objects[-2].modifiers = [_Skin()]
        self.objects[-1].modifiers = [_Skin()]
        self.selection = []
        self.frameRate = 24.0
        self.skinOps = _SkinOps()

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs.
        return next((node for node in self.objects if node.name == name), None)

    def Point3(self, *values):  # noqa: N802 - mirrors pymxs.
        return [float(value) for value in values]

    def EulerAngles(self, *values):  # noqa: N802 - mirrors pymxs.
        return [float(value) for value in values]

    def Position_Constraint(self):  # noqa: N802 - mirrors pymxs.
        return _Constraint()

    def Circle(self):  # noqa: N802 - mirrors pymxs.
        return _Node("circle", 100 + len(self.objects))

    def color(self, red, green, blue):
        return [int(red), int(green), int(blue)]


def _install_fake_pymxs(monkeypatch):
    runtime = _Runtime()
    monkeypatch.setitem(
        sys.modules,
        "pymxs",
        types.SimpleNamespace(
            runtime=runtime,
            animate=lambda _enabled: _Context(),
            attime=lambda _frame: _Context(),
        ),
    )
    return runtime


def test_batch_keyframes_round_trip_through_public_executor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    written = run_skill_script(
        str(ANIMATION_DIR / "action_set_keyframes.py"),
        {
            "node_names": ["rotor_main", "rotor_tail"],
            "keys": [
                {
                    "frame": 1,
                    "property": "rotation",
                    "value": [0, 0, 0],
                    "in_tangent": "linear",
                    "out_tangent": "linear",
                },
                {
                    "frame": 24,
                    "property": "rotation",
                    "value": [0, 360, 0],
                    "in_tangent": "linear",
                    "out_tangent": "linear",
                },
            ],
        },
    )

    assert written["success"] is True, written
    assert written["data"]["changed_key_count"] == 4
    assert [node.rotation for node in runtime.objects[:2]] == [[0.0, 360.0, 0.0]] * 2

    readback = run_skill_script(
        str(ANIMATION_DIR / "action_get_anim_curves.py"),
        {"node_names": ["rotor_main", "rotor_tail"]},
    )

    assert readback["success"] is True, readback
    assert readback["data"]["curve_data"]["schema"] == "dcc-mcp/anim-curves@1"
    assert readback["data"]["curve_data"]["fps"] == 24.0
    curves = readback["data"]["curve_data"]["curves"]
    assert [curve["target"] for curve in curves] == [
        "rotor_main.rotation",
        "rotor_tail.rotation",
    ]
    assert all(curve["key_count"] == 2 for curve in curves)
    assert curves[0]["keys"][1] == {
        "t": 24.0,
        "v": [0.0, 360.0, 0.0],
        "in": "linear",
        "out": "linear",
    }


def test_curve_import_validates_complete_payload_before_mutation(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    invalid = run_skill_script(
        str(ANIMATION_DIR / "action_import_anim_curves.py"),
        {
            "curve_data": {
                "schema": "dcc-mcp/anim-curves@1",
                "fps": 24,
                "curves": [
                    {
                        "target": "rotor_main.rotation",
                        "keys": [{"t": 1, "v": [0, 0, 0], "in": "linear", "out": "linear"}],
                        "key_count": 1,
                    },
                    {
                        "target": "missing.rotation",
                        "keys": [{"t": 1, "v": [0, 0, 0], "in": "linear", "out": "linear"}],
                        "key_count": 1,
                    },
                ],
            }
        },
    )

    assert invalid["success"] is False
    assert "missing" in invalid["message"].lower()
    assert all(node.keyframes == [] for node in runtime.objects)


def test_curve_import_converts_source_fps_to_runtime_frames(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.frameRate = 30.0

    from dcc_mcp_3dsmax._executor import run_skill_script

    imported = run_skill_script(
        str(ANIMATION_DIR / "action_import_anim_curves.py"),
        {
            "curve_data": {
                "schema": "dcc-mcp/anim-curves@1",
                "fps": 24,
                "curves": [
                    {
                        "target": "rotor_main.rotation",
                        "keys": [{"t": 24, "v": [0, 360, 0], "in": "linear", "out": "linear"}],
                        "key_count": 1,
                    }
                ],
            }
        },
    )

    assert imported["success"] is True, imported
    assert runtime.objects[0].keyframes[0]["frame"] == 30.0
    assert imported["data"]["curve_data"]["fps"] == 30.0
    assert imported["data"]["curve_data"]["curves"][0]["keys"][0]["t"] == 30.0


def test_skin_weights_round_trip_through_native_skinops(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    before = run_skill_script(
        str(RIGGING_DIR / "action_get_skin_weights.py"),
        {"node_name": "hero_mesh", "vertex_indices": [1, 2]},
    )

    assert before["success"] is True, before
    assert before["data"]["weights"]["vertex_count"] == 2
    assert before["data"]["weights"]["unnormalized_vertex_count"] == 0
    assert before["data"]["weights"]["vertices"][1]["influences"] == [
        {"bone_id": 1, "bone_name": "root_bone", "weight": 0.5},
        {"bone_id": 2, "bone_name": "tip_bone", "weight": 0.5},
    ]

    changed = run_skill_script(
        str(RIGGING_DIR / "action_set_skin_weights.py"),
        {
            "node_name": "hero_mesh",
            "vertices": [
                {
                    "vertex": 2,
                    "influences": [
                        {"bone_id": 1, "weight": 0.25},
                        {"bone_id": 2, "weight": 0.75},
                    ],
                }
            ],
        },
    )

    assert changed["success"] is True, changed
    assert changed["data"]["changed_vertex_count"] == 1
    assert changed["data"]["weights"]["vertices"][0]["influences"] == [
        {"bone_id": 1, "bone_name": "root_bone", "weight": 0.25},
        {"bone_id": 2, "bone_name": "tip_bone", "weight": 0.75},
    ]


def test_copy_weights_maps_bones_by_name_and_verifies_target(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.objects[-2].modifiers[0].weights[2] = {1: 0.2, 2: 0.8}

    from dcc_mcp_3dsmax._executor import run_skill_script

    copied = run_skill_script(
        str(RIGGING_DIR / "action_copy_weights.py"),
        {
            "source_name": "hero_mesh",
            "target_name": "target_mesh",
            "vertex_indices": [2],
        },
    )

    assert copied["success"] is True, copied
    assert copied["data"]["changed_vertex_count"] == 1
    assert copied["data"]["weights"]["vertices"][0]["influences"] == [
        {"bone_id": 1, "bone_name": "root_bone", "weight": 0.2},
        {"bone_id": 2, "bone_name": "tip_bone", "weight": 0.8},
    ]


def test_pose_data_round_trips_with_transform_readback(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.objects[0].position = [1.0, 2.0, 3.0]
    runtime.objects[0].rotation = [4.0, 5.0, 6.0]

    from dcc_mcp_3dsmax._executor import run_skill_script

    saved = run_skill_script(
        str(RIGGING_DIR / "action_save_pose.py"),
        {"node_names": ["rotor_main"]},
    )
    assert saved["success"] is True, saved
    assert saved["data"]["pose_data"]["schema"] == "dcc-mcp/pose@1"

    runtime.objects[0].position = [10.0, 20.0, 30.0]
    runtime.objects[0].rotation = [40.0, 50.0, 60.0]
    loaded = run_skill_script(
        str(RIGGING_DIR / "action_load_pose.py"),
        {"pose_data": saved["data"]["pose_data"]},
    )

    assert loaded["success"] is True, loaded
    assert loaded["data"]["changed_node_count"] == 1
    assert runtime.objects[0].position == [1.0, 2.0, 3.0]
    assert runtime.objects[0].rotation == [4.0, 5.0, 6.0]


def test_create_constraint_preserves_offset_and_reads_back_target(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    created = run_skill_script(
        str(RIGGING_DIR / "action_create_constraint.py"),
        {
            "constrained_name": "rotor_main",
            "target_name": "rotor_tail",
            "constraint_type": "point",
            "maintain_offset": True,
            "weight": 75,
        },
    )

    assert created["success"] is True, created
    assert created["data"]["constraint_type"] == "point"
    assert created["data"]["maintain_offset"] is True
    controller = runtime.objects[0].position_constraint
    assert controller.relative is True
    assert controller.targets == [(runtime.objects[1], 75.0)]


def test_create_control_applies_bounded_shape_color_and_readback(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    created = run_skill_script(
        str(RIGGING_DIR / "action_create_control.py"),
        {
            "name": "cockpit_ctrl",
            "shape": "circle",
            "size": 12,
            "position": [1, 2, 3],
            "color": [255, 128, 0],
        },
    )

    assert created["success"] is True, created
    control = runtime.getNodeByName("cockpit_ctrl")
    assert control.position == [1.0, 2.0, 3.0]
    assert control.wirecolor == [255, 128, 0]
    assert created["data"]["shape"] == "circle"


def test_rig_state_exports_normalization_assertion_surface(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    state = run_skill_script(
        str(RIGGING_DIR / "action_list_rig_state.py"),
        {"node_names": ["hero_mesh"]},
    )

    assert state["success"] is True, state
    assert state["data"]["schema"] == "dcc-mcp/rig-state@1"
    assert state["data"]["joints"]["count"] == 0
    assert state["data"]["skins"][0]["vertex_count"] == 2
    assert state["data"]["skins"][0]["unnormalized_vertices"] == 0


def test_skin_weight_file_round_trip_is_explicit_and_verified(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    output_path = tmp_path / "hero.skin-weights.json"

    from dcc_mcp_3dsmax._executor import run_skill_script

    exported = run_skill_script(
        str(RIGGING_DIR / "action_export_skin_weights.py"),
        {"node_name": "hero_mesh", "output_path": str(output_path)},
    )
    assert exported["success"] is True, exported
    assert output_path.is_file()
    assert len(exported["data"]["sha256"]) == 64
    refused_overwrite = run_skill_script(
        str(RIGGING_DIR / "action_export_skin_weights.py"),
        {"node_name": "hero_mesh", "output_path": str(output_path)},
    )
    assert refused_overwrite["success"] is False
    assert "overwrite" in refused_overwrite["message"].lower()

    runtime.objects[-2].modifiers[0].weights[2] = {1: 1.0}
    imported = run_skill_script(
        str(RIGGING_DIR / "action_import_skin_weights.py"),
        {
            "node_name": "hero_mesh",
            "input_path": str(output_path),
            "expected_sha256": exported["data"]["sha256"],
        },
    )

    assert imported["success"] is True, imported
    assert imported["data"]["changed_vertex_count"] == 2
    assert runtime.objects[-2].modifiers[0].weights[2] == {1: 0.5, 2: 0.5}


def test_get_anim_curves_reads_native_component_values_and_tangents(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    node = runtime.objects[0]

    def _key(frame, value, in_tangent, out_tangent):
        return types.SimpleNamespace(
            time=frame * 160,
            value=value,
            inTangentType=in_tangent,
            outTangentType=out_tangent,
        )

    components = {
        "x": types.SimpleNamespace(keys=[_key(1, 0.0, "linear", "linear"), _key(24, 0.0, "linear", "step")]),
        "y": types.SimpleNamespace(keys=[_key(1, 0.0, "linear", "linear"), _key(24, -360.0, "linear", "step")]),
        "z": types.SimpleNamespace(keys=[_key(1, 0.0, "linear", "linear"), _key(24, 0.0, "linear", "step")]),
    }
    node.controller = types.SimpleNamespace(properties={"rotation": types.SimpleNamespace(properties=components)})
    runtime.Name = str
    runtime.ticksPerFrame = 160
    runtime.getPropertyController = lambda controller, name: controller.properties.get(str(name))
    runtime.getPropNames = lambda controller: list(controller.properties)
    runtime.numKeys = lambda controller: (
        2 if controller is node.controller.properties["rotation"] else len(getattr(controller, "keys", []))
    )
    runtime.getKeyTime = lambda controller, index: controller.keys[index - 1].time
    runtime.getKey = lambda controller, index: controller.keys[index - 1]

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(ANIMATION_DIR / "action_get_anim_curves.py"),
        {"node_names": ["rotor_main"], "properties": ["rotation"]},
    )

    assert result["success"] is True, result
    curve = result["data"]["curve_data"]["curves"][0]
    assert curve["target"] == "rotor_main.rotation"
    assert curve["key_count"] == 2
    assert curve["keys"][1] == {
        "t": 24.0,
        "v": [0.0, 360.0, 0.0],
        "in": "linear",
        "out": "step",
    }


def test_batch_keyframes_preserves_distinct_in_and_out_tangents(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    result = run_skill_script(
        str(ANIMATION_DIR / "action_set_keyframes.py"),
        {
            "node_names": ["rotor_main"],
            "keys": [
                {
                    "frame": 12,
                    "property": "rotation",
                    "value": [0, 180, 0],
                    "in_tangent": "linear",
                    "out_tangent": "step",
                }
            ],
        },
    )

    assert result["success"] is True, result
    assert result["data"]["curve_data"]["curves"][0]["keys"][0]["in"] == "linear"
    assert result["data"]["curve_data"]["curves"][0]["keys"][0]["out"] == "step"


def test_rotation_readback_converts_native_quaternion_to_degrees(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    rotation = types.SimpleNamespace(x=0, y=0, z=0.08715574, w=0.9961947)
    runtime.objects[0].rotation = rotation
    runtime.quatToEuler = lambda value: types.SimpleNamespace(x=0, y=0, z=-10) if value is rotation else None
    from dcc_mcp_3dsmax._animation_contract import _read_transform_at

    assert _read_transform_at(runtime, runtime.objects[0], 1, "rotation") == [0, 0, -10]
    assert _read_transform_at(runtime, runtime.objects[0], 1, "rotation", quaternion=True) == [
        0,
        0,
        0.08715574,
        0.9961947,
    ]


def test_quaternion_key_uses_all_four_components(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.Quat = lambda x, y, z, w: types.SimpleNamespace(x=x, y=y, z=z, w=w)
    runtime.quatToEuler = lambda value: types.SimpleNamespace(x=0, y=0, z=0)
    from dcc_mcp_3dsmax._animation_contract import set_keyframes

    result = set_keyframes(
        runtime, node_names=["rotor_main"], keys=[{"frame": 1, "property": "rotation", "value": [0, 0, 0, 1]}]
    )
    assert result["success"] is True, result
    assert runtime.objects[0].rotation.w == 1


def test_rotation_key_preserves_world_pivot_position(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    class OrbitingNode(_Node):
        def __setattr__(self, name, value):
            if name == "rotation" and hasattr(self, "position"):
                self.position = [0, 0, 0]
            object.__setattr__(self, name, value)

    node = OrbitingNode("rotor_main", 1)
    node.position = [100, 80, 10]
    runtime.objects[0] = node
    from dcc_mcp_3dsmax._animation_contract import set_keyframes

    result = set_keyframes(
        runtime, node_names=["rotor_main"], keys=[{"frame": 1, "property": "rotation", "value": [0, 0, -45]}]
    )
    assert result["success"] is True, result
    assert node.position == [100, 80, 10]


def test_rotation_only_key_does_not_author_a_position_key(monkeypatch):
    """A rotation-only key must not write node.position inside animate(True)."""
    runtime = _Runtime()

    class _AnimState:
        active = False

    state = _AnimState()

    class _AnimateContext:
        def __init__(self, enabled):
            self.enabled = enabled

        def __enter__(self):
            state.active = bool(self.enabled)
            return self

        def __exit__(self, *_args):
            state.active = False
            return None

    class _AttimeContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class _TrackingNode(_Node):
        def __init__(self, name, handle):
            super().__init__(name, handle)
            self.position_keys = []

        def __setattr__(self, key, value):
            # In Max, a property write inside animate(True) authors a key.
            if key == "position" and state.active and hasattr(self, "position_keys"):
                self.position_keys.append(list(value))
            if key == "rotation" and hasattr(self, "position"):
                # Native pivot orbit is an internal side-effect, not a
                # MAXScript property assignment, so it bypasses the tracker.
                object.__setattr__(self, "position", [0, 0, 0])
            object.__setattr__(self, key, value)

    node = _TrackingNode("rotor_main", 1)
    node.position = [100, 80, 10]
    runtime.objects[0] = node
    monkeypatch.setitem(
        sys.modules,
        "pymxs",
        types.SimpleNamespace(
            runtime=runtime,
            animate=lambda _enabled: _AnimateContext(_enabled),
            attime=lambda _frame: _AttimeContext(),
        ),
    )
    from dcc_mcp_3dsmax._animation_contract import set_keyframes

    result = set_keyframes(
        runtime, node_names=["rotor_main"], keys=[{"frame": 1, "property": "rotation", "value": [0, 0, -45]}]
    )
    assert result["success"] is True, result
    assert node.position == [100, 80, 10]  # pivot restored
    assert node.position_keys == []  # but no spurious position key was authored
