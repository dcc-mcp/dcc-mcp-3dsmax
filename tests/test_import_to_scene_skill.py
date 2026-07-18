"""Tests for the asset import to scene skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from dcc_mcp_core.asset_import import (
    AssetAttribution,
    AssetDescriptor,
    AssetFileVariant,
    AxisHint,
    ImportToSceneRequest,
    ImportToSceneResult,
    ImportWarning,
    MaterialMode,
    PlacementHint,
    UnitHint,
)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-import-to-scene"


def _load_action():
    path = SKILL_DIR / "action_import_to_scene.py"
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNode:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.pos = None
        self.rotation = None
        self.scale = None
        self.material = None
        self.parent = None
        self.user_properties = {}
        self.layer = None


class _FakeLayer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.nodes = []

    def addNode(self, node):  # noqa: N802 - mirrors pymxs naming.
        self.nodes.append(node)


class _FakeMaterial:
    def __init__(self) -> None:
        self.diffuse = None


class _FakeRuntime:
    def __init__(self) -> None:
        self.objects = [_FakeNode("existing_root", 1)]
        self.layers = {}

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return (float(x), float(y), float(z))

    def eulerAngles(self, x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return (float(x), float(y), float(z))

    def StandardMaterial(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _FakeMaterial()


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def _descriptor(variant_a: AssetFileVariant, variant_b: AssetFileVariant | None = None) -> AssetDescriptor:
    variants = [variant_a] + ([variant_b] if variant_b is not None else [])
    return AssetDescriptor(
        asset_id="assets/hero/ship",
        variants=variants,
        attribution=AssetAttribution(source_url="https://example.invalid/ship", license_spdx="CC-BY-4.0"),
        unit_hint=UnitHint.CENTIMETER,
        meters_per_unit=0.01,
        up_axis=AxisHint.Y,
        scale_hint=1.0,
        tags=["hero", "ship"],
        extra={"source": "asset-source"},
    )


def test_contract_round_trips_through_dict_serialization():
    request = ImportToSceneRequest(
        descriptor=_descriptor(
            AssetFileVariant(local_path="C:/assets/ship.fbx", format="fbx", preferred=True),
            AssetFileVariant(local_path="C:/assets/ship.obj", format="obj"),
        ),
        material_mode=MaterialMode.DEFAULT_GRAY,
        placement=PlacementHint(translate=[1, 2, 3], rotate=[10, 20, 30], scale=[2, 2, 2], parent_name="root"),
        target_collection="Imported",
        skip_existing=True,
        extra={"include_animation": False, "fbx_mode": "merge"},
    )
    restored = ImportToSceneRequest.from_dict(request.to_dict())
    result = ImportToSceneResult(
        success=True,
        imported_nodes=["ship_body", "ship_rig"],
        warnings=[ImportWarning(code="missing_texture", message="Texture missing")],
        extra={"format": "fbx"},
    )
    restored_result = ImportToSceneResult.from_dict(result.to_dict())

    assert restored == request
    assert restored_result == result


def test_import_to_scene_prefers_fbx_and_applies_overrides(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    action = _load_action()
    calls = []

    def fake_fbx_main(**kwargs):
        calls.append(("fbx", kwargs))
        runtime.objects.extend([_FakeNode("ship_body", 2), _FakeNode("ship_rig", 3)])
        return {"success": True, "status": "success", "message": "Imported FBX", "data": {"warnings": []}}

    def fake_geometry_main(**kwargs):
        calls.append(("geometry", kwargs))
        raise AssertionError("geometry importer should not be used for preferred fbx")

    monkeypatch.setattr(
        action,
        "_load_action_module",
        lambda script_name: types.SimpleNamespace(
            main=fake_fbx_main if script_name == "action_import_fbx.py" else fake_geometry_main
        ),
    )

    request = ImportToSceneRequest(
        descriptor=_descriptor(
            AssetFileVariant(local_path="C:/assets/ship.fbx", format="fbx", preferred=True),
            AssetFileVariant(local_path="C:/assets/ship.obj", format="obj"),
        ),
        material_mode=MaterialMode.SKIP,
        placement=PlacementHint(translate=[10, 20, 30], rotate=[5, 15, 25], scale=[2, 2, 2]),
        target_collection="Imported",
        extra={"include_animation": False},
    )

    envelope = action.main(request.to_dict())
    imported = ImportToSceneResult.from_dict(envelope["data"])

    assert envelope["success"] is True
    assert calls[0][0] == "fbx"
    assert calls[0][1]["units"] == "cm"
    assert calls[0][1]["up_axis"] == "Y"
    assert calls[0][1]["include_animation"] is False
    assert imported.imported_nodes == ["ship_body", "ship_rig"]
    assert runtime.objects[-2].pos == (10.0, 20.0, 30.0)
    assert runtime.objects[-2].rotation == (5.0, 15.0, 25.0)
    assert runtime.objects[-2].scale == (2.0, 2.0, 2.0)
    assert runtime.objects[-2].material is None
    assert runtime.objects[-2].layer == "Imported"
    assert runtime.layers["Imported"].nodes[-1].name == "ship_rig"


def test_import_to_scene_routes_obj_through_generic_import(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    action = _load_action()
    calls = []

    def fake_fbx_main(**kwargs):
        calls.append(("fbx", kwargs))
        raise AssertionError("fbx importer should not be used for obj")

    def fake_geometry_main(**kwargs):
        calls.append(("geometry", kwargs))
        runtime.objects.append(_FakeNode("prop", 2))
        return {"success": True, "status": "success", "message": "Imported OBJ", "data": {"warnings": []}}

    monkeypatch.setattr(
        action,
        "_load_action_module",
        lambda script_name: types.SimpleNamespace(
            main=fake_fbx_main if script_name == "action_import_fbx.py" else fake_geometry_main
        ),
    )

    request = ImportToSceneRequest(
        descriptor=AssetDescriptor(
            asset_id="assets/prop",
            variants=[AssetFileVariant(local_path="C:/assets/prop.obj", format="obj", preferred=True)],
            unit_hint=UnitHint.UNITLESS,
            meters_per_unit=1.0,
            up_axis=AxisHint.Z,
        ),
        material_mode=MaterialMode.AS_AUTHORED,
        skip_existing=False,
    )

    envelope = action.main(request.to_dict())
    imported = ImportToSceneResult.from_dict(envelope["data"])

    assert envelope["success"] is True
    assert calls[0][0] == "geometry"
    assert calls[0][1]["format"] == "obj"
    assert imported.imported_nodes == ["prop"]
