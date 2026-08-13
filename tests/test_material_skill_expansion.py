"""Tests for expanded bundled 3ds Max material skill tools."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-materials"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _BitmapTexture:
    def __init__(self, filename: str = "") -> None:
        self.filename = filename


class _NormalBump:
    def __init__(self) -> None:
        self.normal_map = None


class _PhysicalMaterial:
    def __init__(self) -> None:
        self.name = "Physical"
        self.base_color = [128.0, 128.0, 128.0]
        self.roughness = 0.5
        self.metalness = 0.0
        self.diffuseMap = None
        self.normalMap = None
        self.roughnessMap = None
        self.metalnessMap = None

    @property
    def bump_map(self):
        return self.normalMap

    @bump_map.setter
    def bump_map(self, value):
        self.normalMap = value


class _PermissivePhysicalMaterial(_PhysicalMaterial):
    """Model MXSWrapperBase accepting a non-native property without persistence."""

    def __init__(self) -> None:
        super().__init__()
        self._native_bump_map = None

    @property
    def bump_map(self):
        return self._native_bump_map

    @bump_map.setter
    def bump_map(self, value):
        self._native_bump_map = value

    def __setattr__(self, name, value) -> None:
        if name in {"normalMap", "normal_map"}:
            return
        super().__setattr__(name, value)


class _StandardMaterial:
    def __init__(self, name: str = "Standard") -> None:
        self.name = name
        self.diffuse = [200.0, 200.0, 200.0]
        self.specular = [255.0, 255.0, 255.0]
        self.glossiness = 20.0
        self.diffuseMap = _BitmapTexture("textures/missing_basecolor.png")


class _FakeNode:
    def __init__(self, name: str, handle: int, material) -> None:
        self.name = name
        self.handle = handle
        self.material = material
        self.isHidden = False
        self.parent = None


class _MaterialLibrary:
    """Model pymxs MaterialLibrary, which is iterable but not a Python list."""

    def __init__(self, values) -> None:
        self.values = list(values)

    def __contains__(self, value) -> bool:
        return value in self.values

    def __getitem__(self, index):
        return self.values[index]

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


class _FakeRuntime:
    def __init__(self) -> None:
        self.standard = _StandardMaterial()
        self.physical = _PhysicalMaterial()
        self.sceneMaterials = _MaterialLibrary([self.standard, self.physical])
        self.objects = [_FakeNode("hero_mesh", 42, self.standard)]
        self.selection = list(self.objects)

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def PhysicalMaterial(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _PhysicalMaterial()

    def StandardMaterial(self, name="Standard"):  # noqa: N802 - mirrors pymxs runtime naming.
        return _StandardMaterial(name=name)

    def Bitmaptexture(self, filename=""):  # noqa: N802 - mirrors pymxs runtime naming.
        return _BitmapTexture(filename)

    def Normal_Bump(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _NormalBump()

    def append(self, collection, value):
        collection.values.append(value)

    def color(self, red, green, blue):
        return [float(red), float(green), float(blue)]


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_material_read_tools_report_materials_assignments_and_maps(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    materials = _load_action("action_list_scene_materials.py").main()
    assignments = _load_action("action_list_node_material_assignments.py").main()
    inspected = _load_action("action_inspect_material.py").main("Standard")
    connections = _load_action("action_list_bitmap_connections.py").main("Standard")

    assert materials["success"] is True
    assert materials["data"]["count"] == 2
    assert assignments["data"]["assignments"][0]["material"]["name"] == "Standard"
    assert inspected["data"]["material"]["glossiness"] == 20.0
    assert connections["data"]["connections"][0]["path"] == "textures/missing_basecolor.png"


def test_material_creation_attribute_and_bitmap_tools(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    texture = tmp_path / "hero_basecolor.png"
    texture.write_text("png", encoding="utf-8")

    from dcc_mcp_3dsmax._executor import run_skill_script

    physical = _load_action("action_create_physical_material.py").main(
        "HeroPhysical",
        base_color=[10, 20, 30],
        roughness=0.25,
        metalness=0.8,
    )
    pbr = _load_action("action_create_pbr_material.py").main("HeroPBR", base_color=[100, 120, 140])
    updated = run_skill_script(
        str(SKILL_DIR / "action_set_material_attributes.py"),
        {"material_name": "HeroPhysical", "attributes": {"opacity": 0.75, "roughness": 0.4}},
    )
    assigned = _load_action("action_assign_bitmap_texture.py").main("HeroPhysical", "diffuse", str(texture))

    assert physical["success"] is True
    assert physical["data"]["material"]["name"] == "HeroPhysical"
    assert pbr["success"] is True
    assert pbr["data"]["material"]["base_color"] == [100.0, 120.0, 140.0]
    assert len(runtime.sceneMaterials) == 4
    assert updated["success"] is True
    assert runtime.sceneMaterials[-2].opacity == 0.75
    assert runtime.sceneMaterials[-2].roughness == 0.4
    assert assigned["success"] is True
    assert runtime.sceneMaterials[-2].diffuseMap.filename == str(texture)


def test_standard_material_can_be_applied_after_creation(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    created = _load_action("action_create_standard_material.py").main(
        "SignalMetal", diffuse=[12, 24, 38], specular=[170, 210, 255], glossiness=65
    )
    applied = _load_action("action_apply_material.py").main(material_name="SignalMetal", node_names=["hero_mesh"])

    assert created["success"] is True
    assert applied["success"] is True
    assert runtime.objects[0].material.name == "SignalMetal"


def test_material_reset_and_missing_texture_reports(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    missing = _load_action("action_report_missing_textures.py").main(["Standard"])
    blocked = _load_action("action_assign_bitmap_texture.py").main("Standard", "diffuse", "textures/not_here.png")
    reset = _load_action("action_reset_material.py").main(use_selection=True)

    assert missing["success"] is True
    assert missing["data"]["count"] == 1
    assert blocked["success"] is False
    assert "does not exist" in blocked["message"]
    assert reset["success"] is True
    assert runtime.objects[0].material is None


def test_physical_normal_map_survives_other_pbr_map_assignments(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    paths = {}
    for slot in ("normal", "roughness", "metalness"):
        path = tmp_path / (slot + ".png")
        path.write_text("png", encoding="utf-8")
        paths[slot] = path

    material = runtime.physical
    for slot in ("normal", "roughness", "metalness"):
        result = _load_action("action_assign_bitmap_texture.py").main("Physical", slot, str(paths[slot]))
        assert result["success"] is True

    connections = _load_action("action_list_bitmap_connections.py").main("Physical")
    by_slot = {row["slot"]: row for row in connections["data"]["connections"]}

    assert set(by_slot) >= {"normal", "roughness", "metalness"}
    assert by_slot["normal"]["path"] == str(paths["normal"])
    assert material.bump_map.normal_map.filename == str(paths["normal"])


def test_physical_normal_map_prefers_native_pymxs_bump_property(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.physical = _PermissivePhysicalMaterial()
    runtime.sceneMaterials = _MaterialLibrary([runtime.standard, runtime.physical])
    texture = tmp_path / "normal.png"
    texture.write_text("png", encoding="utf-8")

    assigned = _load_action("action_assign_bitmap_texture.py").main("Physical", "normal", str(texture))
    connections = _load_action("action_list_bitmap_connections.py").main("Physical")

    assert assigned["success"] is True
    assert runtime.physical.bump_map.normal_map.filename == str(texture)
    assert connections["data"]["connections"] == [
        {
            "slot": "normal",
            "attribute": "bump_map",
            "map_type": "_NormalBump",
            "path": str(texture),
            "exists": True,
        }
    ]
