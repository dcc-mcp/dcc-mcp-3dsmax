"""Tests for the bundled 3ds Max mesh operations skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-mesh-ops"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Modifier:
    def __init__(self, name: str) -> None:
        self.name = name
        self.enabled = True


class _FakeNode:
    def __init__(self, name: str, handle: int, *, verts: int = 8, edges: int = 12, faces: int = 6) -> None:
        self.name = name
        self.handle = handle
        self.vertex_count = verts
        self.edge_count = edges
        self.face_count = faces
        self.isHidden = False
        self.parent = None
        self.modifiers = [_Modifier("Skin")] if name == "hero_mesh" else []
        self.smoothing_groups = {index: 1 for index in range(1, faces + 1)}
        self.selected_faces = [1, 2]
        self.explicit_normal = None
        self.attached = []


class _FakePolyOp:
    def __init__(self, runtime: "_FakeRuntime") -> None:
        self.runtime = runtime

    def getNumVerts(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        return node.vertex_count

    def getNumEdges(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        return node.edge_count

    def getNumFaces(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        return node.face_count

    def getFaceSmoothGroup(self, node, face_index):  # noqa: N802 - mirrors pymxs runtime naming.
        return node.smoothing_groups.get(face_index, 0)

    def setFaceSmoothGroup(self, node, face_index, smoothing_group):  # noqa: N802 - mirrors pymxs runtime naming.
        node.smoothing_groups[int(face_index)] = int(smoothing_group)

    def attach(self, target, source):
        target.attached.append(source.name)

    def detachFaces(self, node, face_indices, asNode=True, name="DetachedMesh"):  # noqa: N803 - mirrors pymxs keyword.
        detached = _FakeNode(
            name, max(item.handle for item in self.runtime.objects) + 1, verts=4, edges=5, faces=len(face_indices)
        )
        self.runtime.objects.append(detached)
        return detached


class _FakeMeshOp:
    def setNormal(self, node, normal):  # noqa: N802 - mirrors pymxs runtime naming.
        node.explicit_normal = list(normal)

    def clearExplicitNormals(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        node.explicit_normal = None


class _FakeRuntime:
    def __init__(self) -> None:
        self.hero = _FakeNode("hero_mesh", 42)
        self.helper = _FakeNode("helper_mesh", 43, verts=4, edges=6, faces=4)
        self.objects = [self.hero, self.helper]
        self.selection = [self.hero]
        self.polyOp = _FakePolyOp(self)
        self.meshop = _FakeMeshOp()

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def addModifier(self, node, modifier):  # noqa: N802 - mirrors pymxs runtime naming.
        node.modifiers.append(modifier)

    def Turn_to_Mesh(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("Turn_to_Mesh")

    def STL_Check(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("STL_Check")

    def TurboSmooth(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("TurboSmooth")

    def ProOptimizer(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("ProOptimizer")

    def Edit_Normals(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("Edit_Normals")

    def attach(self, target, source):
        target.attached.append(source.name)

    def copy(self, node):
        copied = _FakeNode("{}_copy".format(node.name), max(item.handle for item in self.objects) + 1)
        self.objects.append(copied)
        return copied


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_mesh_read_tools_return_topology_smoothing_and_stack(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    topology = _load_action("action_get_mesh_topology.py").main(node_names=["hero_mesh"])
    selected = _load_action("action_get_selected_mesh_topology.py").main()
    smoothing = _load_action("action_get_smoothing_groups.py").main(node_names=["hero_mesh"])
    modifiers = _load_action("action_get_modifier_stack.py").main(node_names=["hero_mesh"])

    assert topology["success"] is True
    assert topology["data"]["nodes"][0]["vertex_count"] == 8
    assert topology["data"]["nodes"][0]["edge_count"] == 12
    assert topology["data"]["nodes"][0]["face_count"] == 6
    assert selected["data"]["nodes"][0]["node"]["node_name"] == "hero_mesh"
    assert smoothing["data"]["nodes"][0]["groups"] == {"1": 6}
    assert modifiers["data"]["nodes"][0]["modifiers"][0]["name"] == "Skin"


def test_mesh_tools_require_explicit_targets(monkeypatch):
    _install_fake_pymxs(monkeypatch)
    result = _load_action("action_get_mesh_topology.py").main()

    assert result["success"] is False
    assert "node_names or handles" in result["message"]


def test_mesh_mutation_tools_update_fake_runtime(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    triangulated = _load_action("action_triangulate_meshes.py").main(node_names=["hero_mesh"])
    cleaned = _load_action("action_cleanup_meshes.py").main(handles=[42])
    subdivided = _load_action("action_apply_subdivision.py").main(node_names=["hero_mesh"], iterations=2)
    proxy = _load_action("action_create_proxy_meshes.py").main(node_names=["hero_mesh"], reduction_percent=25)
    attached = _load_action("action_attach_meshes.py").main(target_name="hero_mesh", source_names=["helper_mesh"])
    detached = _load_action("action_detach_selected_faces.py").main(
        node_name="hero_mesh", use_current_face_selection=True
    )

    modifier_names = [modifier.name for modifier in runtime.hero.modifiers]
    assert triangulated["success"] is True
    assert "Turn_to_Mesh" in modifier_names
    assert cleaned["success"] is True
    assert "STL_Check" in modifier_names
    assert subdivided["success"] is True
    assert "TurboSmooth" in modifier_names
    assert proxy["data"]["proxies"][0]["node"]["node_name"] == "hero_mesh_proxy"
    assert runtime.objects[-2].modifiers[0].name == "ProOptimizer"
    assert attached["data"]["target"]["node"]["node_name"] == "hero_mesh"
    assert runtime.hero.attached == ["helper_mesh"]
    assert detached["success"] is True
    assert detached["data"]["detached"]["node"]["node_name"] == "DetachedMesh"


def test_normal_and_smoothing_tools_update_fake_runtime_through_executor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    set_normals = run_skill_script(
        str(SKILL_DIR / "action_set_explicit_normals.py"),
        {"node_names": ["hero_mesh"], "normal": [0, 0, 1]},
    )
    assigned = _load_action("action_assign_smoothing_group.py").main(
        node_names=["hero_mesh"],
        smoothing_group=4,
        face_indices=[1, 2],
    )

    assert set_normals["success"] is True
    assert runtime.hero.explicit_normal == [0.0, 0.0, 1.0]
    assert assigned["success"] is True
    assert runtime.hero.smoothing_groups[1] == 4
    assert runtime.hero.smoothing_groups[2] == 4

    cleared = _load_action("action_clear_explicit_normals.py").main(node_names=["hero_mesh"])

    assert cleared["success"] is True
    assert runtime.hero.explicit_normal is None
