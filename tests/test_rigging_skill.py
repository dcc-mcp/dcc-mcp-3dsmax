"""Tests for the bundled 3ds Max rigging skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-rigging"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Controller:
    pass


class _TransformValue:
    def __init__(self) -> None:
        self.controller = _Controller()


class _Modifier:
    def __init__(self, name: str) -> None:
        self.name = name
        self.enabled = True
        self.bones = []


class _Constraint:
    def __init__(self, name: str) -> None:
        self.name = name
        self.targets = []

    def appendTarget(self, target, weight):  # noqa: N802 - mirrors 3ds Max controller naming.
        self.targets.append({"target": target, "weight": weight})


class _FakeNode:
    def __init__(self, name: str, handle: int, *, class_name: str = "Node") -> None:
        self.name = name
        self.handle = handle
        self.className = class_name
        self.parent = None
        self.isHidden = False
        self.position = _TransformValue()
        self.rotation = _TransformValue()
        self.scale = _TransformValue()
        self.modifiers = []
        self.constraints = []


class _FakeBoneSystem:
    def __init__(self, runtime: "_FakeRuntime") -> None:
        self.runtime = runtime

    def createBone(self, start, end, up_axis):  # noqa: N802 - mirrors pymxs runtime naming.
        bone = self.runtime._make_node("bone", "Bone")
        bone.start = start
        bone.end = end
        bone.up_axis = up_axis
        return bone


class _FakeRuntime:
    def __init__(self) -> None:
        self._next_handle = 50
        self.hero = _FakeNode("hero_mesh", 42)
        self.ctrl = _FakeNode("ctrl_root", 43, class_name="Point")
        skin = _Modifier("Skin")
        skin.bones = [self.ctrl]
        self.hero.modifiers = [skin]
        self.objects = [self.hero, self.ctrl]
        self.selection = [self.hero]
        self.BoneSys = _FakeBoneSystem(self)
        self.Biped = object()

    def _make_node(self, name: str, class_name: str = "Node"):
        node = _FakeNode(name, self._next_handle, class_name=class_name)
        self._next_handle += 1
        return node

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return [float(x), float(y), float(z)]

    def Point(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("point", "Point")

    def Dummy(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("dummy", "Dummy")

    def SplineShape(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._make_node("path", "SplineShape")

    def Position_Constraint(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Constraint("Position_Constraint")

    def Bend(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("Bend")

    def Twist(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("Twist")

    def addModifier(self, node, modifier):  # noqa: N802 - mirrors pymxs runtime naming.
        node.modifiers.append(modifier)


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_rigging_create_tools_make_helpers_bones_chains_and_paths(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    helper = _load_action("action_create_helper_node.py").main(
        name="hand_ctrl", helper_type="point", position=[1, 2, 3]
    )
    bone = _load_action("action_create_bone_node.py").main(name="upper_arm", start=[0, 0, 0], end=[0, 10, 0])
    chain = _load_action("action_create_joint_chain.py").main(
        base_name="arm",
        positions=[[0, 0, 0], [0, 10, 0], [0, 18, 0]],
    )
    path = _load_action("action_create_path_helper.py").main(name="motion_path", points=[[0, 0, 0], [5, 0, 0]])

    assert helper["success"] is True
    assert runtime.getNodeByName("hand_ctrl").position == [1.0, 2.0, 3.0]
    assert bone["data"]["bone"]["node_name"] == "upper_arm"
    assert chain["data"]["changed_node_count"] == 2
    assert runtime.getNodeByName("arm_02").parent.name == "arm_01"
    assert path["data"]["point_count"] == 2
    assert runtime.getNodeByName("motion_path").path_points == [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]]


def test_rigging_read_tools_report_state_and_optional_systems(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    state = _load_action("action_list_rig_state.py").main(use_selection=True)
    availability = _load_action("action_get_character_system_availability.py").main()

    assert state["success"] is True
    assert state["data"]["nodes"][0]["skinning"]["has_skin"] is True
    assert state["data"]["nodes"][0]["skinning"]["bone_count"] == 1
    assert state["data"]["nodes"][0]["deformers"][0]["name"] == "Skin"
    assert availability["data"]["systems"]["biped"] is True
    assert availability["data"]["available"] is True


def test_rigging_deformer_and_constraint_workflow_through_executor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    applied = run_skill_script(
        str(SKILL_DIR / "action_apply_deformer_modifier.py"),
        {"node_names": ["hero_mesh"], "deformer_type": "bend", "attributes": {"angle": 45}},
    )
    constrained = _load_action("action_set_constraint_target.py").main(
        constrained_name="hero_mesh",
        target_name="ctrl_root",
        constraint_type="position",
        weight=50,
    )
    state = _load_action("action_list_rig_state.py").main(node_names=["hero_mesh"])

    assert applied["success"] is True
    assert runtime.hero.modifiers[-1].name == "Bend"
    assert runtime.hero.modifiers[-1].angle == 45
    assert constrained["data"]["changed_target_count"] == 1
    assert runtime.hero.constraints[0].target is runtime.ctrl
    assert state["data"]["nodes"][0]["constraints"][0]["target"]["node_name"] == "ctrl_root"

    removed = _load_action("action_remove_deformer_modifier.py").main(node_names=["hero_mesh"], deformer_type="bend")
    invalid = _load_action("action_set_constraint_target.py").main(
        constrained_name="hero_mesh",
        target_name="hero_mesh",
        constraint_type="position",
    )

    assert removed["data"]["changed_modifier_count"] == 1
    assert all(modifier.name != "Bend" for modifier in runtime.hero.modifiers)
    assert invalid["success"] is False
    assert "different" in invalid["message"]


def test_rigging_tools_report_target_and_modifier_errors(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    missing_target = _load_action("action_list_rig_state.py").main(node_names=["missing"])
    missing_modifier = _load_action("action_remove_deformer_modifier.py").main(
        node_names=["hero_mesh"], deformer_type="twist"
    )

    assert missing_target["success"] is False
    assert "could not be resolved" in missing_target["message"]
    assert missing_modifier["success"] is False
    assert missing_modifier["data"]["changed_modifier_count"] == 0
