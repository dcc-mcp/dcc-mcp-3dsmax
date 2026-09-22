"""Tests for the renderer-aware light providers (Arnold, Corona, photometric).

The doubles model a live host: they expose ``isProperty`` and ``Name`` so the
provider writers must resolve the native property instead of inventing a Python
attribute, and every assertion checks that a rejected or silently ignored
control is reported as a failure.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _light_providers as providers  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-camera-lighting"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_provider_test_module", str(path))
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
        self.color_space = ""


class _Light:
    """Base double: every declared control is a plain attribute."""

    def __init__(self, handle: int, name: str = "") -> None:
        self.name = name
        self.handle = handle
        self.enabled = True
        self.position = [0.0, 0.0, 0.0]
        self.color = None
        self.castShadows = True
        self.intensity = 1.0
        self.target = None


class _ArnoldAreaLight(_Light):
    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self.type = 0
        self.units = 0
        self.exposure = 0.0
        self.U_size = 10.0
        self.V_size = 10.0
        self.radius = 1.0
        self.samples = 2
        self.spread = 1.0
        self.normalize = True
        self.texmap = None


class _ArnoldAreaLightWithKelvin(_ArnoldAreaLight):
    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self.use_color_temperature = False
        self.color_temperature = 6500.0


class _CoronaLight(_Light):
    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self.shapeType = 0
        self.intensityUnits = 0
        self.width = 10.0
        self.length = 10.0
        self.radius = 1.0
        self.colorMode = 0
        self.temperature = 6500.0
        self.texmap = None


class _CoronaSun(_Light):
    pass


class _MrAreaOmni(_Light):
    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self.type = 0
        self.intensityUnits = 0
        self.shadowsOn = True
        self.mr_Width = 10.0
        self.mr_Length = 10.0
        self.mr_Radius = 5.0
        self.colorType = 0
        self.kelvin = 6500.0
        self.intensity = 1000.0


class _FreeLight(_Light):
    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self.intensity = 1000.0
        self.shadowsOn = True


class _OmniLight(_Light):
    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self.multiplier = 1.0


class _SilentArnoldLight(_ArnoldAreaLight):
    """A host light that accepts writes but never stores them."""

    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self._intensity = 1.0

    @property
    def intensity(self) -> float:  # type: ignore[override]
        return self._intensity

    @intensity.setter
    def intensity(self, value: float) -> None:
        return


class _MaxOps:
    def __init__(self, runtime: "_ProviderRuntime") -> None:
        self._runtime = runtime

    def getNodeByHandle(self, handle):  # noqa: N802 - mirrors pymxs naming.
        for node in self._runtime.objects:
            if getattr(node, "handle", None) == handle:
                return node
        return None


class _Renderer:
    def __init__(self, name: str) -> None:
        self.name = name


class _Renderers:
    def __init__(self, current: Any) -> None:
        self.current = current


class _ProviderRuntime:
    """Host double whose factories and active renderer are configurable."""

    light_class = _ArnoldAreaLight

    def __init__(self, renderer_name: str = "Arnold") -> None:
        self._next_handle = 900
        self.objects: list = []
        self.sceneMaterials: list = []
        self.selection: list = []
        self.renderers = _Renderers(_Renderer(renderer_name))
        self.maxOps = _MaxOps(self)

    # -- pymxs-shaped helpers -------------------------------------------------
    def Name(self, value):  # noqa: N802 - mirrors pymxs naming.
        return str(value)

    def isProperty(self, node, name):  # noqa: N802 - mirrors pymxs naming.
        return hasattr(node, str(name))

    def classOf(self, node):  # noqa: N802 - mirrors pymxs naming.
        if isinstance(node, _Renderer):
            return node.name
        return type(node).__name__

    def superClassOf(self, node):  # noqa: N802 - mirrors pymxs naming.
        return self.classOf(node)

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs naming.
        return [float(x), float(y), float(z)]

    def color(self, red, green, blue):
        return _Color(float(red), float(green), float(blue))

    def delete(self, node) -> None:
        if node in self.objects:
            self.objects.remove(node)

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def Bitmaptexture(self, filename="") -> _BitmapTexture:  # noqa: N802 - mirrors pymxs naming.
        return _BitmapTexture(filename)

    # -- factories ------------------------------------------------------------
    def _make(self, node_class, name: str = ""):
        node = node_class(self._next_handle, name)
        self._next_handle += 1
        self.objects.append(node)
        return node

    def aiAreaLight(self) -> Any:
        return self._make(self.light_class)

    def aiSkyDomeLight(self) -> Any:
        return self._make(_ArnoldAreaLight)

    def aiPhotometricLight(self) -> Any:
        return self._make(_ArnoldAreaLight)

    def CoronaLight(self) -> Any:
        return self._make(_CoronaLight)

    def CoronaSun(self) -> Any:
        return self._make(_CoronaSun)

    def mrAreaOmni(self) -> Any:
        return self._make(_MrAreaOmni)

    def FreeLight(self) -> Any:
        return self._make(_FreeLight)

    def OmniLight(self) -> Any:
        return self._make(_OmniLight)


class VRayLightDouble(_Light):
    """Stand-in for a VRayLight node (the name drives provider detection)."""

    def __init__(self, handle: int, name: str = "") -> None:
        super().__init__(handle, name)
        self.type = 0
        self.units = 0
        self.multiplier = 1.0
        self.castShadows = True
        self.normalizeColor = True
        self.U_size = 10.0
        self.V_size = 10.0
        self.texmap = None
        self.target = None


class _VRayRuntime(_ProviderRuntime):
    """Host double whose active renderer and light factory are V-Ray."""

    def __init__(self) -> None:
        super().__init__("V_Ray_6")

    def VRayLight(self) -> VRayLightDouble:  # noqa: N802 - mirrors pymxs naming.
        return self._make(VRayLightDouble)

    def VRayBitmap(self) -> _BitmapTexture:  # noqa: N802 - mirrors pymxs naming.
        return _BitmapTexture()


def _install_pymxs(monkeypatch, runtime):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


# ---------------------------------------------------------------------------
# Capability discovery
# ---------------------------------------------------------------------------


def test_capabilities_report_factory_availability_and_routing(monkeypatch):
    _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_lighting_capabilities.py").main()

    assert result["success"] is True, result
    data = result["data"]
    assert data["renderer"]["family"] == "arnold"
    assert data["default_provider"] == "arnold"
    assert data["max_lights_per_call"] == 32
    arnold = next(entry for entry in data["providers"] if entry["key"] == "arnold")
    assert arnold["available"] is True
    assert arnold["kinds"]["area"]["available_factories"] == ["aiAreaLight"]
    assert arnold["kinds"]["dome"]["available"] is True
    corona = next(entry for entry in data["providers"] if entry["key"] == "corona")
    assert corona["available"] is True
    assert corona["kinds"]["area"]["available_factories"] == ["CoronaLight"]
    assert corona["kinds"]["sky"]["available"] is False
    assert arnold["shapes"]["disk"] == 1
    assert arnold["controls"]["intensity"] == ["intensity", "aiIntensity"]


def test_capabilities_mark_providers_without_factories_unavailable(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    runtime.CoronaLight = None
    runtime.CoronaSun = None

    result = _load_action("action_lighting_capabilities.py").main(providers=["corona"])

    corona = result["data"]["providers"][0]
    assert corona["available"] is False
    assert corona["kinds"]["area"]["available_factories"] == []
    assert corona["kinds"]["area"]["factory_candidates"] == ["CoronaLight"]


def test_capabilities_route_by_requested_renderer_and_reject_unknown_providers(monkeypatch):
    _install_pymxs(monkeypatch, _ProviderRuntime("Default_Scanline_Renderer"))

    scanline = _load_action("action_lighting_capabilities.py").main(renderer="Default_Scanline_Renderer")
    assert scanline["data"]["default_provider"] == "photometric"

    corona = _load_action("action_lighting_capabilities.py").main(renderer="Corona")
    assert corona["data"]["default_provider"] == "corona"

    bad = _load_action("action_lighting_capabilities.py").main(providers=["fstorm"])
    assert bad["success"] is False
    assert bad["data"]["unsupported"] == ["fstorm"]


def test_capabilities_probe_reports_real_host_support_and_leaves_no_light(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_lighting_capabilities.py").main(providers=["arnold"], probe=True)

    assert result["success"] is True, result
    probe = result["data"]["providers"][0]["probe"]
    assert probe["status"] == "ok"
    area = probe["kinds"]["area"]
    assert area["rolled_back"] is True
    assert area["controls"]["intensity"]["exposed"] is True
    assert area["controls"]["intensity"]["attributes"] == ["intensity"]
    assert area["controls"]["radius"]["exposed"] is True
    assert area["shapes"]["exposed"] is True
    assert area["shapes"]["accepted"] == [0, 1, 2, 3]
    assert runtime.objects == []


# ---------------------------------------------------------------------------
# Batch creation
# ---------------------------------------------------------------------------


def test_create_arnold_light_verifies_shape_units_size_and_temperature(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    runtime.light_class = _ArnoldAreaLightWithKelvin

    result = _load_action("action_create_renderer_light.py").main(
        name="KeyLight",
        provider="arnold",
        shape="disk",
        units="lm",
        intensity=4.0,
        exposure=2.0,
        color=[255, 240, 220],
        color_temperature=5600.0,
        cast_shadows=False,
        size_u=40.0,
        size_v=20.0,
        radius=3.0,
        samples=3,
        spread=0.8,
        normalize_color=False,
        position=[0, -100, 50],
    )

    assert result["success"] is True, result
    light = runtime.getNodeByName("KeyLight")
    assert light.type == 1
    assert light.units == 2
    assert light.intensity == 4.0
    assert light.exposure == 2.0
    assert light.castShadows is False
    assert light.U_size == 40.0
    assert light.V_size == 20.0
    assert light.radius == 3.0
    assert light.samples == 3
    assert light.spread == 0.8
    assert light.normalize is False
    assert light.color_temperature == 5600.0
    assert light.use_color_temperature is True
    assert light.position == [0.0, -100.0, 50.0]
    summary = result["data"]["lights"][0]
    assert summary["provider"] == "arnold"
    assert summary["shape"] == "disk"
    assert summary["units"] == "lm"
    assert summary["color"] == [255, 240, 220]


def test_create_corona_light_uses_corona_property_names(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Corona"))

    result = _load_action("action_create_renderer_light.py").main(
        name="CoronaKey",
        provider="corona",
        shape="sphere",
        units="cd",
        intensity=25.0,
        size_u=12.0,
        size_v=6.0,
        color_temperature=3200.0,
    )

    assert result["success"] is True, result
    light = runtime.getNodeByName("CoronaKey")
    assert light.shapeType == 2
    assert light.intensityUnits == 3
    assert light.width == 12.0
    assert light.length == 6.0
    assert light.temperature == 3200.0
    assert light.colorMode in (1, True)


def test_create_photometric_area_light_uses_native_area_properties(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Default_Scanline_Renderer"))

    result = _load_action("action_create_renderer_light.py").main(
        provider="photometric",
        lights=[
            {
                "name": "AreaRect",
                "shape": "rectangle",
                "units": "lm",
                "intensity": 1500.0,
                "size_u": 30.0,
                "size_v": 15.0,
                "cast_shadows": True,
                "color_temperature": 4000.0,
            }
        ],
    )

    assert result["success"] is True, result
    light = runtime.getNodeByName("AreaRect")
    assert light.type == 0
    assert light.mr_Width == 30.0
    assert light.mr_Length == 15.0
    assert light.shadowsOn is True
    assert light.kelvin == 4000.0
    assert result["data"]["lights"][0]["provider"] == "photometric"


def test_create_renderer_light_accepts_a_raw_enum_index(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(
        name="RawShape", provider="arnold", shape_value=2, units_value=1
    )

    assert result["success"] is True, result
    light = runtime.getNodeByName("RawShape")
    assert light.type == 2
    assert light.units == 1
    assert result["data"]["lights"][0]["shape"] == "cylinder"
    assert result["data"]["lights"][0]["units"] == "w"


def test_create_renderer_light_routes_auto_to_the_active_renderer(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(name="AutoLight", shape="quad")

    assert result["success"] is True, result
    assert result["data"]["provider"] == "arnold"
    assert type(runtime.getNodeByName("AutoLight")).__name__ == "_ArnoldAreaLight"


def test_create_renderer_light_wires_a_texture_with_color_space(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    hdri = tmp_path / "studio.hdr"
    hdri.write_text("hdr", encoding="utf-8")

    result = _load_action("action_create_renderer_light.py").main(
        name="DomeLight",
        provider="arnold",
        kind="dome",
        intensity=1.5,
        texture_path=str(hdri),
        color_space="sRGB",
    )

    assert result["success"] is True, result
    light = runtime.getNodeByName("DomeLight")
    assert light.texmap.filename == str(hdri)
    assert light.texmap.color_space == "sRGB"
    assert result["data"]["lights"][0]["texture"] == str(hdri)


def test_create_renderer_light_targets_a_photometric_light(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Default_Scanline_Renderer"))

    class _TargetedFreeLight(_FreeLight):
        def __init__(self, handle: int, name: str = "") -> None:
            super().__init__(handle, name)
            self.target = _Light(handle + 500, name + ".Target")

    runtime.FreeLight = lambda: runtime._make(_TargetedFreeLight)

    result = _load_action("action_create_renderer_light.py").main(
        name="TargetedLight",
        provider="photometric",
        kind="photometric",
        intensity=900.0,
        target_position=[10, 20, 30],
    )

    assert result["success"] is True, result
    light = runtime.getNodeByName("TargetedLight")
    assert light.target.position == [10.0, 20.0, 30.0]


def test_create_renderer_light_batch_rolls_back_when_one_light_fails(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(
        provider="arnold",
        lights=[
            {"name": "Good", "shape": "quad"},
            {"name": "Bad", "shape": "triangle"},
        ],
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is True
    assert result["data"]["errors"][0]["index"] == 1
    assert runtime.objects == []


def test_create_renderer_light_rejects_malformed_values_before_creating_anything(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(
        provider="arnold",
        lights=[{"name": "Bright", "intensity": "bright", "shape": "triangle", "color": [1, 2]}],
    )

    assert result["success"] is False
    fields = {entry["field"] for entry in result["data"]["errors"][0]["fields"]}
    assert fields == {"intensity", "shape", "color"}
    assert runtime.objects == []


def test_create_renderer_light_rejects_mixed_providers_and_unknown_providers(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    mixed = _load_action("action_create_renderer_light.py").main(
        lights=[{"provider": "arnold", "name": "A"}, {"provider": "corona", "name": "B"}]
    )
    assert mixed["success"] is False
    assert mixed["data"]["failure_reason"] == "mixed_providers_unsupported"

    unknown = _load_action("action_create_renderer_light.py").main(provider="fstorm", name="C")
    assert unknown["success"] is False
    assert unknown["data"]["failure_reason"] == "unknown_provider"
    assert runtime.objects == []


def test_create_renderer_light_rejects_more_than_the_batch_limit(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(
        provider="arnold",
        lights=[{"name": "Light{}".format(index)} for index in range(33)],
    )

    assert result["success"] is False
    assert result["data"]["maximum"] == 32
    assert runtime.objects == []


def test_create_renderer_light_rejects_mixed_batch_and_single_arguments(monkeypatch):
    _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(lights=[{"name": "A"}], name="Single")

    assert result["success"] is False
    assert "not both" in result["message"]


def test_create_renderer_light_fails_when_the_factory_is_missing(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    runtime.aiAreaLight = None

    result = _load_action("action_create_renderer_light.py").main(provider="arnold", name="Missing")

    assert result["success"] is False
    assert result["data"]["errors"][0]["data"]["failure_reason"] == "light_factory_unavailable"


def test_create_renderer_light_fails_when_the_host_ignores_a_control(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    runtime.light_class = _SilentArnoldLight

    result = _load_action("action_create_renderer_light.py").main(provider="arnold", name="SilentLight", intensity=5.0)

    assert result["success"] is False
    failures = result["data"]["errors"][0]["data"]["failures"]
    assert {entry["field"] for entry in failures} == {"intensity"}
    assert all(entry["requested"] == 5.0 for entry in failures)
    assert [entry["actual"] for entry in failures if "actual" in entry] == [1.0]
    assert runtime.objects == []


def test_create_renderer_light_fails_when_a_control_is_unsupported(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(
        provider="arnold", name="NoKelvin", color_temperature=5000.0
    )

    assert result["success"] is False
    failure = result["data"]["errors"][0]["data"]["failures"][0]
    assert failure["field"] == "color_temperature"
    assert "color_temperature" in failure["candidates"]
    assert runtime.objects == []


def test_create_renderer_light_rejects_unsupported_kind(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    result = _load_action("action_create_renderer_light.py").main(provider="arnold", kind="laser", name="Nope")

    assert result["success"] is False
    assert result["data"]["errors"][0]["fields"][0]["field"] == "kind"
    assert runtime.objects == []


# ---------------------------------------------------------------------------
# set_light_properties extension
# ---------------------------------------------------------------------------


def test_set_light_properties_updates_shape_units_and_size_on_an_arnold_light(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    created = _load_action("action_create_renderer_light.py").main(name="KeyLight", provider="arnold")
    assert created["success"] is True, created

    updated = _load_action("action_set_light_properties.py").main(
        light_name="KeyLight",
        shape="sphere",
        units="cd",
        size_u=8.0,
        size_v=4.0,
        intensity=2.0,
        color=[10, 20, 30],
        shadows=False,
        exposure=1.0,
    )

    assert updated["success"] is True, updated
    light = runtime.getNodeByName("KeyLight")
    assert light.type == 3
    assert light.units == 3
    assert light.U_size == 8.0
    assert light.V_size == 4.0
    assert light.intensity == 2.0
    assert light.castShadows is False
    assert updated["data"]["provider"] == "arnold"
    assert "shape" in updated["data"]["changed_fields"]
    assert "shadows" in updated["data"]["changed_fields"]


def test_set_light_properties_reports_and_reverts_a_rejected_control(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    created = _load_action("action_create_renderer_light.py").main(name="KeyLight", provider="arnold", intensity=1.0)
    assert created["success"] is True, created

    updated = _load_action("action_set_light_properties.py").main(
        light_name="KeyLight", intensity=7.5, color_temperature=5000.0
    )

    assert updated["success"] is False
    assert updated["data"]["failure_reason"] == "light_readback_failed"
    failures = {entry["field"] for entry in updated["data"]["failures"]}
    assert "color_temperature" in failures
    # The accepted control was rolled back rather than left half applied.
    assert runtime.getNodeByName("KeyLight").intensity == 1.0


def test_set_light_properties_recolors_the_texture_already_wired(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    hdri = tmp_path / "studio.hdr"
    hdri.write_text("hdr", encoding="utf-8")
    created = _load_action("action_create_renderer_light.py").main(
        name="DomeLight",
        provider="arnold",
        texture_path=str(hdri),
        color_space="Raw",
    )
    assert created["success"] is True, created

    updated = _load_action("action_set_light_properties.py").main(light_name="DomeLight", color_space="sRGB")

    assert updated["success"] is True, updated
    light = runtime.getNodeByName("DomeLight")
    assert light.texmap.filename == str(hdri)
    assert light.texmap.color_space == "sRGB"
    assert updated["data"]["light"]["color_space"] == "sRGB"


def test_set_light_properties_reports_a_missing_texture_slot_for_color_space(monkeypatch):
    _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    created = _load_action("action_create_renderer_light.py").main(name="Bare", provider="arnold")
    assert created["success"] is True, created

    updated = _load_action("action_set_light_properties.py").main(light_name="Bare", color_space="sRGB")

    assert updated["success"] is False
    failure = updated["data"]["failures"][0]
    assert failure["field"] == "color_space"
    assert "no texture slot" in failure["error"]


def test_set_light_properties_restores_a_recolored_texture_on_failure(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))
    hdri = tmp_path / "studio.hdr"
    hdri.write_text("hdr", encoding="utf-8")
    created = _load_action("action_create_renderer_light.py").main(
        name="DomeLight", provider="arnold", texture_path=str(hdri), color_space="Raw"
    )
    assert created["success"] is True, created

    updated = _load_action("action_set_light_properties.py").main(
        light_name="DomeLight", color_space="sRGB", color_temperature=5000.0
    )

    # The Arnold double has no Kelvin property, so the whole call fails and the
    # color space written earlier in the same call is put back.
    assert updated["success"] is False
    assert runtime.getNodeByName("DomeLight").texmap.color_space == "Raw"
    restored = updated["data"]["failures"][0]["restored_previous_values"]
    assert [entry["field"] for entry in restored] == ["color_space"]


def test_set_light_properties_still_updates_host_native_lights(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Default_Scanline_Renderer"))
    runtime.objects.append(_OmniLight(runtime._next_handle, "Omni01"))
    runtime._next_handle += 1

    updated = _load_action("action_set_light_properties.py").main(
        light_name="Omni01", enabled=False, intensity=0.25, color=[128, 160, 255], shadows=True
    )

    assert updated["success"] is True, updated
    light = runtime.getNodeByName("Omni01")
    assert light.enabled is False
    assert light.multiplier == 0.25
    assert updated["data"]["changed_light_count"] == 1
    assert updated["data"]["provider"] == "standard"


def test_set_light_properties_rejects_a_shape_the_light_cannot_take(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _ProviderRuntime("Default_Scanline_Renderer"))
    runtime.objects.append(_OmniLight(runtime._next_handle, "Omni01"))
    runtime._next_handle += 1

    updated = _load_action("action_set_light_properties.py").main(light_name="Omni01", shape="rectangle")

    assert updated["success"] is False
    assert updated["data"]["failures"][0]["field"] == "shape"
    assert runtime.getNodeByName("Omni01").multiplier == 1.0


def test_set_light_properties_requires_a_target_and_a_request(monkeypatch):
    _install_pymxs(monkeypatch, _ProviderRuntime("Arnold"))

    missing = _load_action("action_set_light_properties.py").main(light_name="Nope", intensity=1.0)
    assert missing["success"] is False

    empty = _load_action("action_set_light_properties.py").main(light_name="Nope")
    assert empty["success"] is False


# ---------------------------------------------------------------------------
# Provider plumbing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "class_name,expected",
    [
        ("aiAreaLight", "arnold"),
        ("aiSkyDomeLight", "arnold"),
        ("CoronaLight", "corona"),
        ("CoronaSun", "corona"),
        ("mrAreaOmni", "photometric"),
        ("FreeLight", "photometric"),
        ("mr_Sun", "photometric"),
        ("VRayLight", "vray"),
        ("OmniLight", "standard"),
    ],
)
def test_detect_provider_maps_native_classes(class_name, expected):
    class _Node:
        className = class_name

    runtime = _ProviderRuntime()
    runtime.classOf = lambda node: node.className
    assert providers.detect_provider(runtime, _Node()) == expected


def test_detect_provider_covers_every_declared_token():
    """Every token in the detection table must be reachable after normalization."""
    runtime = _ProviderRuntime()
    for token, expected in providers._NODE_CLASS_PROVIDERS:
        node = _Light(1, "probe")
        node.className = token
        runtime.classOf = lambda node: node.className
        assert providers.detect_provider(runtime, node) == expected, token


def test_tool_metadata_matches_observed_behavior():
    """A tool that can build and delete nodes must not advertise read-only."""
    import yaml

    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    capabilities = by_name["lighting_capabilities"]

    # probe=true builds one light per factory and deletes it again.
    assert capabilities["read_only"] is False
    assert capabilities["annotations"]["read_only_hint"] is False
    assert capabilities["side_effects"]["creates"] is True
    assert capabilities["side_effects"]["deletes"] is True

    # The units and shape enums must accept every token a provider declares,
    # otherwise a provider capability is unreachable through the schema.
    declared_units: set = set()
    declared_shapes: set = set()
    for provider in providers.PROVIDERS.values():
        declared_units.update(provider.units)
        declared_shapes.update(provider.shapes)
    for tool_name in ("create_renderer_light", "set_light_properties"):
        properties = by_name[tool_name]["input_schema"]["properties"]
        assert not declared_units - set(properties["units"]["enum"]), tool_name
        assert not declared_shapes - set(properties["shape"]["enum"]), tool_name


def test_vray_provider_maps_raw_enum_indices_onto_the_declared_tables(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())

    result = _load_action("action_create_renderer_light.py").main(
        provider="vray", name="VrayRaw", shape_value=2, units_value=2
    )

    assert result["success"] is True, result
    light = runtime.getNodeByName("VrayRaw")
    assert light.type == 2
    assert light.units == 2
    assert result["data"]["lights"][0]["shape_value"] == 2
    assert result["data"]["lights"][0]["units_value"] == 2


def test_vray_provider_rejects_raw_enum_indices_outside_the_table(monkeypatch):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())

    result = _load_action("action_create_renderer_light.py").main(provider="vray", name="VrayRaw", shape_value=99)

    assert result["success"] is False
    fields = result["data"]["errors"][0]["fields"]
    assert [entry["field"] for entry in fields] == ["shape_value"]
    assert runtime.objects == []


def test_vray_provider_delegates_and_rejects_generic_only_fields(monkeypatch, tmp_path):
    runtime = _install_pymxs(monkeypatch, _VRayRuntime())

    created = _load_action("action_create_renderer_light.py").main(
        provider="vray", name="VrayRect", shape="rectangle", intensity=3.0
    )
    assert created["success"] is True, created
    assert runtime.getNodeByName("VrayRect").multiplier == 3.0

    rejected = _load_action("action_create_renderer_light.py").main(provider="vray", name="VrayBad", radius=2.0)
    assert rejected["success"] is False
    assert rejected["data"]["failure_reason"] == "light_spec_invalid"
