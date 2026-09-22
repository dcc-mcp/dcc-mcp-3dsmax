"""Offline contracts for material and render write paths.

A write tool must never report success for a value the host did not keep. These
tests drive the write paths with fake pymxs runtimes that either refuse a
property or accept it without persisting it -- the two ways a host can make a
tool lie about its own result.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

ROOT = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax"
MATERIALS_DIR = ROOT / "skills" / "3dsmax-materials"
RENDER_DIR = ROOT / "skills" / "3dsmax-render"


def _load_action(skill_dir: Path, script_name: str):
    path = skill_dir / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_verify_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Material doubles
# ---------------------------------------------------------------------------


class _SilentMaterial:
    """Model MXSWrapperBase accepting a property it never persists."""

    _SWALLOWED: frozenset = frozenset()

    def __setattr__(self, name, value) -> None:
        if name in self._SWALLOWED:
            return
        super().__setattr__(name, value)


class _StandardMaterial(_SilentMaterial):
    def __init__(self, name: str = "Standard") -> None:
        self.name = name
        self.diffuse = [200.0, 200.0, 200.0]
        self.specular = [255.0, 255.0, 255.0]
        self.glossiness = 20.0


class _PhysicalMaterial(_SilentMaterial):
    def __init__(self, name: str = "Physical") -> None:
        self.name = name
        self.base_color = [128.0, 128.0, 128.0]
        self.roughness = 0.5
        self.metalness = 0.0


class _RejectingMaterial(_PhysicalMaterial):
    """Model a host that raises on a property it does not own."""

    _REJECTED: frozenset = frozenset()

    def __setattr__(self, name, value) -> None:
        if name in self._REJECTED and name != "_REJECTED":
            raise AttributeError("Cannot set unknown property {}".format(name))
        super().__setattr__(name, value)


class _SilentAssignmentNode:
    """Model a node that accepts a material assignment without keeping it."""

    def __init__(self, name: str, handle: int, material) -> None:
        self.name = name
        self.handle = handle
        self.material = material
        self.isHidden = False
        self.parent = None
        self._silent = True

    def __setattr__(self, name, value) -> None:
        if name == "material" and getattr(self, "_silent", False):
            return
        super().__setattr__(name, value)


class _MaterialRuntime:
    def __init__(self, *, material=None, standard=None, node=None) -> None:
        self.standard = standard if standard is not None else _StandardMaterial()
        self.physical = material if material is not None else _PhysicalMaterial()
        self.sceneMaterials = [self.standard, self.physical]
        self.objects = [node] if node is not None else []
        self.selection = list(self.objects)

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def PhysicalMaterial(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self.physical

    def StandardMaterial(self, name="Standard"):  # noqa: N802 - mirrors pymxs runtime naming.
        self.standard.name = name
        return self.standard

    def color(self, red, green, blue):
        return [float(red), float(green), float(blue)]


def _install_material_runtime(monkeypatch, runtime):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


# ---------------------------------------------------------------------------
# Render doubles
# ---------------------------------------------------------------------------


class _Viewport:
    def __init__(self) -> None:
        self.camera = None


class _RenderNode:
    def __init__(self, name: str, handle: int, *, is_camera: bool = False, material=None) -> None:
        self.name = name
        self.handle = handle
        self.is_camera = is_camera
        self.material = material
        self.isHidden = False
        self.parent = None


class _SilentRuntime:
    """Model MXSWrapperBase accepting a property it never persists."""

    _SWALLOWED: frozenset = frozenset()

    def __setattr__(self, name, value) -> None:
        if name in self._SWALLOWED:
            return
        super().__setattr__(name, value)


class _RenderRuntime(_SilentRuntime):
    def __init__(self) -> None:
        self.camera = _RenderNode("main_camera", 84, is_camera=True)
        self.second_camera = _RenderNode("second_camera", 85, is_camera=True)
        self.mesh = _RenderNode("hero_mesh", 42, material=object())
        self.objects = [self.camera, self.second_camera, self.mesh]
        self.viewport = _Viewport()
        self.activeCamera = self.camera
        self.renderWidth = 1280
        self.renderHeight = 720
        self.animationRangeStart = 1
        self.animationRangeEnd = 24
        self.frameStart = 1
        self.frameEnd = 24
        self.rendOutputFilename = ""
        self.rendSaveFile = False
        self.renderQualityPreset = "preview"
        self.currentRenderer = object()
        self.render_count = 0

    def Name(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
        return value

    def classOf(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        return "Targetcamera" if node is self.camera else type(node).__name__

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def render(self, *, outputfile, camera=None, vfb=True):
        self.render_count += 1
        Path(outputfile).write_text("render", encoding="utf-8")
        return object()


def _install_render_runtime(monkeypatch, runtime):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime, byref=lambda value: value))
    return runtime


# ---------------------------------------------------------------------------
# Material write paths
# ---------------------------------------------------------------------------


def test_create_standard_material_fails_when_the_host_ignores_glossiness(monkeypatch):
    material = _StandardMaterial()
    material._SWALLOWED = frozenset({"glossiness", "glossinessValue"})
    runtime = _install_material_runtime(monkeypatch, _MaterialRuntime(standard=material))

    result = _load_action(MATERIALS_DIR, "action_create_standard_material.py").main(
        "Silent", diffuse=[12, 24, 38], specular=[170, 210, 255], glossiness=65
    )

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["attribute"] == "glossiness"
    assert [entry["attribute"] for entry in result["data"]["applied"]] == ["diffuse", "specular"]
    # The material still holds the value the host kept, not the requested one.
    assert runtime.standard.glossiness == 20.0


def test_create_physical_material_fails_when_the_host_refuses_metalness(monkeypatch):
    material = _RejectingMaterial()
    material._REJECTED = frozenset({"metalness", "metalnessValue", "metallic", "base_metalness"})
    _install_material_runtime(monkeypatch, _MaterialRuntime(material=material))

    result = _load_action(MATERIALS_DIR, "action_create_physical_material.py").main(
        "Rejecting", base_color=[10, 20, 30], roughness=0.25, metalness=0.8
    )

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["attribute"] == "metalness"
    assert result["data"]["material"]["roughness"] == 0.25


def test_create_pbr_material_fails_when_base_color_is_silently_ignored(monkeypatch):
    material = _PhysicalMaterial()
    material._SWALLOWED = frozenset({"base_color", "baseColor", "diffuse", "diffuseColor"})
    _install_material_runtime(monkeypatch, _MaterialRuntime(material=material))

    result = _load_action(MATERIALS_DIR, "action_create_pbr_material.py").main("SilentPBR", base_color=[1, 2, 3])

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["attribute"] == "base_color"
    assert result["data"]["material"]["base_color"] == [128.0, 128.0, 128.0]


def test_create_pbr_material_reports_applied_attributes(monkeypatch):
    _install_material_runtime(monkeypatch, _MaterialRuntime())

    result = _load_action(MATERIALS_DIR, "action_create_pbr_material.py").main(
        "VerifiedPBR", base_color=[100, 120, 140], roughness=0.3, metalness=0.9
    )

    assert result["success"] is True, result
    assert [entry["attribute"] for entry in result["data"]["applied"]] == ["base_color", "roughness", "metalness"]
    assert result["data"]["applied_count"] == 3
    assert result["data"]["errors"] == []


def test_apply_material_reports_nodes_it_could_not_resolve(monkeypatch):
    material = _PhysicalMaterial(name="Hero")
    node = _RenderNode("hero_mesh", 42, material=None)
    runtime = _MaterialRuntime(material=material, node=node)
    _install_material_runtime(monkeypatch, runtime)

    result = _load_action(MATERIALS_DIR, "action_apply_material.py").main(
        material_name="Hero", node_names=["hero_mesh", "ghost_mesh"]
    )

    assert result["success"] is False, result
    assert result["data"]["skipped"] == ["ghost_mesh"]
    assert result["data"]["applied_count"] == 1
    assert node.material is material


def test_apply_material_fails_when_a_node_ignores_the_assignment(monkeypatch):
    material = _PhysicalMaterial(name="Hero")
    node = _SilentAssignmentNode("hero_mesh", 42, material=None)
    _install_material_runtime(monkeypatch, _MaterialRuntime(material=material, node=node))

    result = _load_action(MATERIALS_DIR, "action_apply_material.py").main(
        material_name="Hero", node_names=["hero_mesh"]
    )

    assert result["success"] is False, result
    assert result["data"]["applied_count"] == 0
    assert result["data"]["errors"][0]["node"]["node_name"] == "hero_mesh"
    assert "readback" in result["data"]["errors"][0]["error"]


def test_reset_material_fails_when_a_node_keeps_its_material(monkeypatch):
    material = _PhysicalMaterial(name="Hero")
    node = _SilentAssignmentNode("hero_mesh", 42, material=material)
    _install_material_runtime(monkeypatch, _MaterialRuntime(material=material, node=node))

    result = _load_action(MATERIALS_DIR, "action_reset_material.py").main(node_names=["hero_mesh"])

    assert result["success"] is False, result
    assert result["data"]["count"] == 0
    assert result["data"]["errors"][0]["node"]["node_name"] == "hero_mesh"
    assert node.material is material


# ---------------------------------------------------------------------------
# Render write paths
# ---------------------------------------------------------------------------


def test_render_setting_writes_report_what_the_host_applied(monkeypatch, tmp_path):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    output_path = tmp_path / "beauty.png"

    output = _load_action(RENDER_DIR, "action_set_render_output_options.py").main(
        output_path=str(output_path), save_file=True
    )
    frame_range = _load_action(RENDER_DIR, "action_set_frame_range.py").main(start_frame=10, end_frame=20)
    resolution = _load_action(RENDER_DIR, "action_set_render_resolution.py").main(width=1920, height=1080)
    camera = _load_action(RENDER_DIR, "action_set_render_camera.py").main(camera_name="main_camera")
    preset = _load_action(RENDER_DIR, "action_set_render_quality_preset.py").main("final")

    assert output["success"] is True, output
    assert output["data"]["applied"] == ["output_path", "save_file"]
    assert output["data"]["errors"] == []
    assert frame_range["data"]["applied"] == [
        "start_frame",
        "end_frame",
        "render_start_frame",
        "render_end_frame",
    ]
    assert resolution["data"]["applied"] == ["width", "height"]
    assert runtime.renderWidth == 1920
    assert camera["data"]["applied"] == ["camera"]
    assert runtime.activeCamera is runtime.camera
    assert preset["data"]["applied"][0] == "preset"
    assert runtime.renderQualityPreset == "final"


def test_set_render_resolution_fails_when_the_host_ignores_the_width(monkeypatch):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    runtime._SWALLOWED = frozenset({"renderWidth"})

    result = _load_action(RENDER_DIR, "action_set_render_resolution.py").main(width=1920, height=1080)

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["setting"] == "width"
    assert result["data"]["applied"] == ["height"]
    assert runtime.renderWidth == 1280


def test_set_frame_range_fails_when_the_host_ignores_the_start_frame(monkeypatch):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    runtime._SWALLOWED = frozenset({"animationRangeStart"})

    result = _load_action(RENDER_DIR, "action_set_frame_range.py").main(start_frame=10, end_frame=20)

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["setting"] == "start_frame"
    assert runtime.animationRangeStart == 1


def test_set_render_output_fails_when_the_host_ignores_the_output_path(monkeypatch, tmp_path):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    runtime._SWALLOWED = frozenset({"rendOutputFilename"})

    result = _load_action(RENDER_DIR, "action_set_render_output_options.py").main(
        output_path=str(tmp_path / "beauty.png"), save_file=True
    )

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["setting"] == "output_path"
    assert result["data"]["applied"] == ["save_file"]
    assert runtime.rendOutputFilename == ""


def test_set_render_camera_fails_when_the_host_ignores_active_camera(monkeypatch):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    runtime._SWALLOWED = frozenset({"activeCamera"})

    result = _load_action(RENDER_DIR, "action_set_render_camera.py").main(camera_name="second_camera")

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["setting"] == "camera"
    assert result["data"]["applied"] == []
    assert runtime.activeCamera is runtime.camera


def test_set_render_quality_preset_fails_when_the_host_ignores_the_preset(monkeypatch):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    runtime._SWALLOWED = frozenset({"renderQualityPreset"})

    result = _load_action(RENDER_DIR, "action_set_render_quality_preset.py").main("final")

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["setting"] == "preset"
    assert runtime.renderQualityPreset == "preview"


def test_set_render_quality_preset_reports_a_rejected_derived_knob(monkeypatch):
    """Preset-derived knobs are reported, but they do not fail the preset."""

    class _RejectingPresetRuntime(_RenderRuntime):
        def __setattr__(self, name, value) -> None:
            if name == "render_sampling":
                raise AttributeError("Cannot set unknown property render_sampling")
            super().__setattr__(name, value)

    runtime = _install_render_runtime(monkeypatch, _RejectingPresetRuntime())

    result = _load_action(RENDER_DIR, "action_set_render_quality_preset.py").main("final")

    assert result["success"] is True, result
    assert result["data"]["applied"][0] == "preset"
    assert result["data"]["unverified"] == ["sampling"]
    assert any("render_sampling" in warning for warning in result["data"]["warnings"])
    assert runtime.renderQualityPreset == "final"


def test_render_scene_stops_when_the_output_path_cannot_be_written(monkeypatch, tmp_path):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    runtime._SWALLOWED = frozenset({"rendOutputFilename"})
    output_path = tmp_path / "beauty.png"

    result = _load_action(RENDER_DIR, "action_render_scene.py").main(output_path=str(output_path), overwrite=True)

    assert result["success"] is False, result
    assert result["data"]["errors"][0]["setting"] == "output_path"
    assert runtime.render_count == 0
    assert not output_path.exists()


def test_hdr_render_stops_when_the_host_ignores_the_bit_depth(monkeypatch, tmp_path):
    runtime = _install_render_runtime(monkeypatch, _RenderRuntime())
    runtime.outputBitDepth = 8
    runtime._SWALLOWED = frozenset({"outputBitDepth"})
    output_path = tmp_path / "beauty.exr"

    result = _load_action(RENDER_DIR, "action_render_hdr.py").main(
        output_path=str(output_path), overwrite=True, bit_depth=16
    )

    assert result["success"] is False, result
    assert result["data"]["format_settings"]["errors"][0]["setting"] == "bit_depth"
    assert runtime.render_count == 0
    assert not output_path.exists()


def test_hdr_render_reports_an_unverifiable_bit_depth_without_claiming_it(monkeypatch, tmp_path):
    """A bit depth the host never reads back is reported, never assumed."""

    class _UnverifiableBitDepthRuntime(_RenderRuntime):
        """Model a host that takes the bit depth but cannot report it back."""

        def __getattr__(self, name):
            if name == "outputBitDepth":
                raise AttributeError(name)
            return super().__getattribute__(name)

    runtime = _install_render_runtime(monkeypatch, _UnverifiableBitDepthRuntime())
    runtime._SWALLOWED = frozenset({"outputBitDepth"})
    output_path = tmp_path / "beauty.exr"

    result = _load_action(RENDER_DIR, "action_render_hdr.py").main(
        output_path=str(output_path), overwrite=True, bit_depth=16
    )

    assert result["success"] is True, result
    assert result["data"]["format_settings"]["unverified"] == ["bit_depth"]
    assert any("outputBitDepth" in warning for warning in result["data"]["format_settings"]["warnings"])
    assert output_path.exists()
