"""Tests for V-Ray material parameters, texture sets, lights, and HDRI bitmaps.

The doubles in this module model a live V-Ray host: they expose ``isProperty``
and ``Name`` so the renderer-aware writers must resolve the native property
instead of silently inventing a Python attribute.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILLS = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills"
MATERIALS_DIR = SKILLS / "3dsmax-materials"
LOOKDEV_DIR = SKILLS / "3dsmax-lookdev"
LIGHTING_DIR = SKILLS / "3dsmax-camera-lighting"


def _load_action(skill_dir: Path, script_name: str):
    path = skill_dir / script_name
    spec = importlib.util.spec_from_file_location("{}_{}".format(skill_dir.name, path.stem), str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Color:
    def __init__(self, r: float, g: float, b: float) -> None:
        self.r = r
        self.g = g
        self.b = b


class _BitmapTexture:
    def __init__(self, filename: str = "") -> None:
        self.filename = filename


class _NormalBump:
    def __init__(self) -> None:
        self.normal_map = None


class _VRayMtl:
    """V-Ray 5+ material: roughness lives behind ``brdf_useRoughness``."""

    def __init__(self) -> None:
        self.name = ""
        self.diffuse = None
        self.brdf_useRoughness = False
        self.reflectionRoughness = 0.0
        self.metalness = 0.0
        self.opacity = 1.0
        self.texmap_diffuse = None
        self.texmap_roughness = None
        self.texmap_reflectionRoughness = None
        self.texmap_metalness = None
        self.texmap_bump = None
        self.texmap_displacement = None
        self.texmap_opacity = None


class _VRayMtlLegacy:
    """V-Ray 4 material: only a reflection glossiness property exists."""

    def __init__(self) -> None:
        self.name = ""
        self.diffuse = None
        self.reflection_glossiness = 1.0
        self.metalness = 0.0
        self.texmap_diffuse = None
        self.texmap_reflectionGlossiness = None


class _VRayMtlWithoutRoughness:
    """A VRayMtl that exposes neither roughness spelling."""

    def __init__(self) -> None:
        self.name = ""
        self.diffuse = None
        self.texmap_diffuse = None


class _VRayBitmap:
    def __init__(self) -> None:
        self.bitmap = None
        self.HDRIMapType = 0
        self.gamma = 1.0
        self.color_space = ""
        self.horizontalRotation = 0.0


class _VRayBitmapWithoutMapType(_VRayBitmap):
    """A VRayBitmap build that cannot store a projection type."""

    def __init__(self) -> None:
        super().__init__()
        del self.HDRIMapType


class _Target:
    def __init__(self) -> None:
        self.name = "target"
        self.position = [0.0, 0.0, 0.0]


class _VRayLight:
    def __init__(self, handle: int) -> None:
        self.name = ""
        self.handle = handle
        self.type = 0
        self.units = 0
        self.multiplier = 1.0
        self.color = None
        self.castShadows = True
        self.normalizeColor = True
        self.targeted = False
        self.U_size = 10.0
        self.V_size = 10.0
        self.texmap = None
        self.position = [0.0, 0.0, 0.0]
        self.enabled = True
        self.target = None
        self.baseObject = self


class _MaxOps:
    def __init__(self, runtime: "_VRayRuntime") -> None:
        self._runtime = runtime

    def getNodeByHandle(self, handle):  # noqa: N802 - mirrors pymxs naming.
        for node in self._runtime.objects:
            if getattr(node, "handle", None) == handle:
                return node
        return None


class _VRayRenderer:
    """Stand-in for ``renderers.current`` on a V-Ray scene."""

    pass


class _VRayRuntime:
    def __init__(self, material_class=_VRayMtl, bitmap_class=_VRayBitmap) -> None:
        self._next_handle = 500
        self._material_class = material_class
        self._bitmap_class = bitmap_class
        self.sceneMaterials = []
        self.objects = []
        self.selection = []
        self.environmentMap = None
        self.environmentMapOn = False
        self.environmentMapAmount = 0.0
        self.environmentMapAngle = 0.0
        self.currentRenderer = _VRayRenderer()
        self.maxOps = _MaxOps(self)

    # -- pymxs-shaped helpers -------------------------------------------------
    def Name(self, value):  # noqa: N802 - mirrors pymxs naming.
        return str(value)

    def isProperty(self, node, name):  # noqa: N802 - mirrors pymxs naming.
        return hasattr(node, str(name))

    def classOf(self, node):  # noqa: N802 - mirrors pymxs naming.
        return type(node).__name__

    def superClassOf(self, node):  # noqa: N802 - mirrors pymxs naming.
        return type(node).__name__

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs naming.
        return [float(x), float(y), float(z)]

    def color(self, red, green, blue):
        return _Color(float(red), float(green), float(blue))

    def delete(self, node) -> None:
        if node in self.objects:
            self.objects.remove(node)
        if node in self.sceneMaterials:
            self.sceneMaterials.remove(node)

    def append(self, collection, value) -> None:
        collection.append(value)

    # -- factories ------------------------------------------------------------
    def VRayMtl(self):  # noqa: N802 - mirrors pymxs naming.
        material = self._material_class()
        self.sceneMaterials.append(material)
        return material

    def VRayBitmap(self):  # noqa: N802 - mirrors pymxs naming.
        return self._bitmap_class()

    def Bitmaptexture(self, filename=""):  # noqa: N802 - mirrors pymxs naming.
        return _BitmapTexture(filename)

    def Normal_Bump(self):  # noqa: N802 - mirrors pymxs naming.
        return _NormalBump()

    def VRayLight(self):  # noqa: N802 - mirrors pymxs naming.
        light = _VRayLight(self._next_handle)
        self._next_handle += 1
        self.objects.append(light)
        return light

    def OmniLight(self):  # noqa: N802 - mirrors pymxs naming.
        light = _VRayLight(self._next_handle)
        light.name = "omni"
        light.type = 0
        self._next_handle += 1
        self.objects.append(light)
        return light


def _install_pymxs(monkeypatch, runtime):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def _write_texture(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("png", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# V-Ray material parameters
# ---------------------------------------------------------------------------


def test_vray_roughness_enables_brdf_switch_and_writes_reflection_roughness(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    runtime.objects.append(_VRayLight(1))
    runtime.selection = list(runtime.objects)

    result = _load_action(LOOKDEV_DIR, "action_assign_renderer_material.py").main(
        material_name="HeroVRay",
        renderer_type="vray",
        base_color=[200, 120, 40],
        roughness=0.7,
        metalness=0.25,
        node_names=[],
    )

    assert result["success"] is True, result
    material = runtime.sceneMaterials[0]
    assert material.brdf_useRoughness is True
    assert material.reflectionRoughness == 0.7
    assert material.metalness == 0.25
    applied = {entry["parameter"]: entry for entry in result["data"]["applied_parameters"]}
    assert applied["roughness"]["attribute"] == "reflectionRoughness"
    assert applied["roughness"]["prerequisites"] == ["brdf_useRoughness"]


def test_vray_legacy_material_uses_inverted_reflection_glossiness(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime(material_class=_VRayMtlLegacy))

    result = _load_action(LOOKDEV_DIR, "action_assign_renderer_material.py").main(
        material_name="LegacyVRay", renderer_type="vray", roughness=0.7
    )

    assert result["success"] is True, result
    material = runtime.sceneMaterials[0]
    assert material.reflection_glossiness == pytest.approx(0.3)
    applied = {entry["parameter"]: entry for entry in result["data"]["applied_parameters"]}
    assert applied["roughness"]["attribute"] == "reflection_glossiness"
    assert applied["roughness"]["transform"] == "inverse"


def test_vray_roughness_failure_is_reported_and_rolls_the_material_back(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime(material_class=_VRayMtlWithoutRoughness))

    result = _load_action(LOOKDEV_DIR, "action_assign_renderer_material.py").main(
        material_name="BrokenVRay", renderer_type="vray", roughness=0.4
    )

    assert result["success"] is False
    assert result["data"]["errors"][0]["parameter"] == "roughness"
    assert result["data"]["rollback"]["rolled_back"] is True
    assert runtime.sceneMaterials == []


def test_set_material_attributes_reports_unapplied_attributes(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    material = runtime.VRayMtl()
    material.name = "Mat"

    ok = _load_action(MATERIALS_DIR, "action_set_material_attributes.py").main(
        material_name="Mat", attributes={"roughness": 0.2}
    )
    failed = _load_action(MATERIALS_DIR, "action_set_material_attributes.py").main(
        material_name="Mat", attributes={"sheen": 0.5}
    )

    assert ok["success"] is True
    assert ok["data"]["renderer"] == "vray"
    assert failed["success"] is False
    assert failed["data"]["errors"][0]["attribute"] == "sheen"


def test_assign_bitmap_texture_uses_vray_native_slot(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    material = runtime.VRayMtl()
    material.name = "Mat"
    texture = _write_texture(tmp_path, "hero_basecolor.png")

    result = _load_action(MATERIALS_DIR, "action_assign_bitmap_texture.py").main(
        material_name="Mat", slot="roughness", texture_path=str(texture)
    )

    assert result["success"] is True, result
    assert result["data"]["attribute"] == "texmap_roughness"
    assert material.texmap_roughness.filename == str(texture)


# ---------------------------------------------------------------------------
# Texture-set driven material creation
# ---------------------------------------------------------------------------


def test_create_material_from_textures_wires_every_vray_slot(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    directory = tmp_path / "textures"
    directory.mkdir()
    for name in (
        "hero_basecolor.png",
        "hero_roughness.png",
        "hero_metalness.png",
        "hero_normal.png",
        "hero_displacement.png",
        "hero_opacity.png",
    ):
        _write_texture(directory, name)

    result = _load_action(MATERIALS_DIR, "action_create_material_from_textures.py").main(
        texture_dir=str(directory), name="HeroSet", renderer="vray"
    )

    assert result["success"] is True, result
    material = runtime.sceneMaterials[0]
    assert result["data"]["renderer"] == "vray"
    assert material.texmap_diffuse.filename.endswith("hero_basecolor.png")
    assert material.texmap_roughness.filename.endswith("hero_roughness.png")
    assert material.texmap_metalness.filename.endswith("hero_metalness.png")
    assert material.texmap_bump.normal_map.filename.endswith("hero_normal.png")
    assert material.texmap_displacement.filename.endswith("hero_displacement.png")
    assert material.texmap_opacity.filename.endswith("hero_opacity.png")
    assert result["data"]["wired_count"] == 6


def test_create_material_from_textures_reports_unsupported_slots(monkeypatch, tmp_path):
    _install_pymxs(monkeypatch, _VRayRuntime())
    directory = tmp_path / "set"
    directory.mkdir()
    _write_texture(directory, "hero_basecolor.png")
    _write_texture(directory, "hero_ao.png")

    result = _load_action(MATERIALS_DIR, "action_create_material_from_textures.py").main(
        texture_dir=str(directory), name="HeroSet", renderer="vray"
    )

    assert result["success"] is True
    assert any("ao" in warning for warning in result["data"]["warnings"])
    assert result["data"]["wired_count"] == 1


def test_create_material_from_textures_fails_and_rolls_back_when_slot_is_absent(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime(material_class=_VRayMtlWithoutRoughness))
    directory = tmp_path / "set"
    directory.mkdir()
    _write_texture(directory, "hero_basecolor.png")
    _write_texture(directory, "hero_roughness.png")

    result = _load_action(MATERIALS_DIR, "action_create_material_from_textures.py").main(
        texture_dir=str(directory), name="HeroSet", renderer="vray"
    )

    assert result["success"] is False
    assert [entry["slot"] for entry in result["data"]["errors"]] == ["roughness"]
    assert result["data"]["rollback"]["rolled_back"] is True
    assert runtime.sceneMaterials == []


def test_create_material_from_textures_requires_a_source(monkeypatch):
    _install_pymxs(monkeypatch, _VRayRuntime())

    result = _load_action(MATERIALS_DIR, "action_create_material_from_textures.py").main(name="None")

    assert result["success"] is False
    assert "texture_dir" in result["message"]


# ---------------------------------------------------------------------------
# V-Ray lights
# ---------------------------------------------------------------------------


def test_create_vray_light_builds_verified_dome_light_with_hdri(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    hdri = _write_texture(tmp_path, "studio.hdr")

    result = _load_action(LIGHTING_DIR, "action_create_vray_light.py").main(
        lights=[
            {
                "name": "VRayDome",
                "shape": "environment",
                "units": "cd_m2",
                "multiplier": 3.0,
                "color": [255, 240, 220],
                "cast_shadows": False,
                "normalize_color": False,
                "texture_path": str(hdri),
                "map_type": "spherical",
                "gamma": 2.2,
                "color_space": "sRGB",
                "horizontal_rotation": 45.0,
            }
        ]
    )

    assert result["success"] is True, result
    light = runtime.objects[0]
    assert light.type == 1
    assert light.units == 2
    assert light.multiplier == 3.0
    assert light.castShadows is False
    assert light.normalizeColor is False
    assert light.texmap.HDRIMapType == 2
    assert light.texmap.bitmap.filename == str(hdri)
    assert light.texmap.gamma == 2.2
    assert light.texmap.color_space == "sRGB"
    assert light.texmap.horizontalRotation == 45.0
    assert result["data"]["lights"][0]["shape"] == "environment"
    assert result["data"]["lights"][0]["units"] == "cd_m2"
    assert result["data"]["lights"][0]["texture"] == str(hdri)


def test_create_vray_light_applies_rectangle_size_and_target(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())

    result = _load_action(LIGHTING_DIR, "action_create_vray_light.py").main(
        name="VRayRect",
        shape="rectangle",
        position=[0, -100, 50],
        size_u=40.0,
        size_v=20.0,
        targeted=True,
    )

    assert result["success"] is True, result
    light = runtime.objects[0]
    assert light.type == 0
    assert light.U_size == 40.0
    assert light.V_size == 20.0
    assert light.targeted is True


def test_create_vray_light_batch_rolls_back_when_one_light_fails(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())

    result = _load_action(LIGHTING_DIR, "action_create_vray_light.py").main(
        lights=[{"name": "Good", "shape": "sphere"}, {"name": "Bad", "shape": "triangle"}]
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is True
    assert runtime.objects == []
    assert result["data"]["errors"][0]["index"] == 1


def test_create_vray_light_rejects_more_than_the_batch_limit(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())

    result = _load_action(LIGHTING_DIR, "action_create_vray_light.py").main(
        lights=[{"name": "Light{}".format(index)} for index in range(33)]
    )

    assert result["success"] is False
    assert "Too many" in result["message"]
    assert runtime.objects == []


def test_create_vray_light_rejects_mixed_batch_and_single_arguments(monkeypatch):
    _install_pymxs(monkeypatch, _VRayRuntime())

    result = _load_action(LIGHTING_DIR, "action_create_vray_light.py").main(
        lights=[{"name": "Good"}], name="Single"
    )

    assert result["success"] is False
    assert "not both" in result["message"]


def test_create_vray_light_fails_closed_without_a_vray_factory(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    runtime.VRayLight = None

    result = _load_action(LIGHTING_DIR, "action_create_vray_light.py").main(name="Missing")

    assert result["success"] is False
    assert result["data"]["errors"][0]["data"]["failure_reason"] == "vray_light_factory_unavailable"


# ---------------------------------------------------------------------------
# V-Ray HDRI environment
# ---------------------------------------------------------------------------


def test_setup_hdr_lighting_uses_vray_bitmap_controls(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    hdri = _write_texture(tmp_path, "studio.hdr")

    result = _load_action(LOOKDEV_DIR, "action_setup_hdr_lighting.py").main(
        hdri_path=str(hdri),
        map_type="mirrored_ball",
        gamma=1.8,
        color_space="ACEScg",
        horizontal_rotation=90.0,
        create_rig=False,
    )

    assert result["success"] is True, result
    assert result["data"]["bitmap_type"] == "_VRayBitmap"
    applied = {entry["field"]: entry["attribute"] for entry in result["data"]["bitmap_controls"]}
    assert applied["map_type"] == "HDRIMapType"
    assert applied["gamma"] == "gamma"
    assert applied["color_space"] == "color_space"
    assert applied["horizontal_rotation"] == "horizontalRotation"
    bitmap = runtime.environmentMap
    assert bitmap.HDRIMapType == 3
    assert bitmap.gamma == 1.8
    assert bitmap.color_space == "ACEScg"
    assert bitmap.horizontalRotation == 90.0
    assert runtime.environmentMapOn is True


def test_setup_hdr_lighting_reports_unsupported_map_type(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    hdri = _write_texture(tmp_path, "studio.hdr")

    result = _load_action(LOOKDEV_DIR, "action_setup_hdr_lighting.py").main(
        hdri_path=str(hdri), map_type="cylindrical", create_rig=False
    )

    assert result["success"] is False
    assert result["data"]["bitmap_errors"][0]["field"] == "map_type"
    assert runtime.environmentMap is None


def test_setup_hdr_lighting_reports_missing_projection_property(monkeypatch, tmp_path):
    _install_pymxs(monkeypatch, _VRayRuntime(bitmap_class=_VRayBitmapWithoutMapType))
    hdri = _write_texture(tmp_path, "studio.hdr")

    result = _load_action(LOOKDEV_DIR, "action_setup_hdr_lighting.py").main(
        hdri_path=str(hdri), map_type="cubic", create_rig=False
    )

    assert result["success"] is False
    assert result["data"]["bitmap_errors"][0]["field"] == "map_type"
    assert result["data"]["bitmap_errors"][0]["candidates"]


# ---------------------------------------------------------------------------
# Renderer auto-detection
# ---------------------------------------------------------------------------


def test_auto_renderer_detects_vray_from_the_active_renderer(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    directory = tmp_path / "auto"
    directory.mkdir()
    _write_texture(directory, "hero_basecolor.png")
    _write_texture(directory, "hero_roughness.png")

    result = _load_action(MATERIALS_DIR, "action_create_material_from_textures.py").main(
        texture_dir=str(directory), name="AutoSet"
    )

    assert result["success"] is True, result
    assert result["data"]["renderer"] == "vray"
    material = runtime.sceneMaterials[0]
    assert material.texmap_diffuse is not None
    assert material.texmap_roughness is not None


def test_auto_renderer_detects_vray_from_the_material_class(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())
    material = runtime.VRayMtl()
    material.name = "Auto"

    result = _load_action(MATERIALS_DIR, "action_set_material_attributes.py").main(
        material_name="Auto", attributes={"roughness": 0.55}
    )

    assert result["success"] is True
    assert result["data"]["renderer"] == "vray"
    assert result["data"]["applied"][0]["native_attribute"] == "reflectionRoughness"
    assert material.brdf_useRoughness is True
