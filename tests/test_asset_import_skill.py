"""Tests for the bundled 3ds Max asset import skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-asset-import"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNode:
    def __init__(self, name: str, handle: int, bounds: tuple[list[float], list[float]]) -> None:
        self.name = name
        self.handle = handle
        self.min = bounds[0]
        self.max = bounds[1]


class _FakeRuntime:
    def __init__(self, created: list[_FakeNode]) -> None:
        self.objects = []
        self._created = created

    def importFile(self, file_path, _no_prompt, using=None):  # noqa: N802 - mirrors pymxs runtime naming.
        _ = (file_path, _no_prompt, using)
        self.objects.extend(self._created)
        return True


class _FakeArnoldUsdNode:
    def __init__(self) -> None:
        self.name = "Arnold_USD_Object001"
        self.handle = 77
        self.filename = ""
        self.objectpath = ""
        self.frame = 0.0
        self.UpAxis = -1


class _FakeArnoldRuntime(_FakeRuntime):
    def __init__(self) -> None:
        super().__init__([])
        self.created_usd_nodes = []

    def Arnold_USD_Object(self):  # noqa: N802 - mirrors the MAXtoA class name.
        node = _FakeArnoldUsdNode()
        self.objects.append(node)
        self.created_usd_nodes.append(node)
        return node


def _install_fake_pymxs(monkeypatch, runtime):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))


def test_import_to_scene_returns_created_nodes_and_bounds(monkeypatch, tmp_path):
    asset = tmp_path / "hero_asset.fbx"
    asset.write_text("asset", encoding="utf-8")
    runtime = _FakeRuntime(
        [
            _FakeNode("asset_geo_A", 42, ([0.0, 0.0, 0.0], [1.0, 2.0, 3.0])),
            _FakeNode("asset_geo_B", 43, ([-2.0, -1.0, 0.0], [4.0, 5.0, 6.0])),
        ]
    )
    _install_fake_pymxs(monkeypatch, runtime)

    result = _load_action("action_import_to_scene.py").main(
        descriptor={"name": "Hero Asset", "path": str(asset), "format": "fbx"},
        group_name="HeroRig",
    )

    assert result["success"] is True
    assert result["data"]["imported_node_names"] == ["HeroRig_01", "HeroRig_02"]
    assert result["data"]["bounding_box"]["min"] == [-2.0, -1.0, 0.0]
    assert result["data"]["bounding_box"]["max"] == [4.0, 5.0, 6.0]


def test_import_to_scene_rejects_missing_files(monkeypatch, tmp_path):
    runtime = _FakeRuntime([])
    _install_fake_pymxs(monkeypatch, runtime)

    result = _load_action("action_import_to_scene.py").main(
        descriptor={"name": "Missing", "path": str(tmp_path / "missing.obj"), "format": "obj"}
    )

    assert result["success"] is False
    assert "does not exist" in result["message"]


def test_create_arnold_usd_procedural_sets_stage_contract(monkeypatch, tmp_path):
    stage = tmp_path / "signal_forge.usda"
    stage.write_text("#usda 1.0", encoding="utf-8")
    runtime = _FakeArnoldRuntime()
    _install_fake_pymxs(monkeypatch, runtime)

    result = _load_action("action_create_arnold_usd_procedural.py").main(
        file_path=str(stage),
        name="SignalForgeUSD",
        object_path="/World/SignalForge",
        frame=72.0,
        up_axis="y",
    )

    node = runtime.created_usd_nodes[0]
    assert result["success"] is True
    assert result["data"]["file_path"] == str(stage.resolve())
    assert result["data"]["node"]["node_name"] == "SignalForgeUSD"
    assert result["data"]["object_path"] == "/World/SignalForge"
    assert result["data"]["frame"] == 72.0
    assert result["data"]["up_axis"] == "y"
    assert node.filename == str(stage.resolve())
    assert node.objectpath == "/World/SignalForge"
    assert node.frame == 72.0
    assert node.UpAxis == 1


def test_create_arnold_usd_procedural_rejects_missing_file(monkeypatch, tmp_path):
    runtime = _FakeArnoldRuntime()
    _install_fake_pymxs(monkeypatch, runtime)

    result = _load_action("action_create_arnold_usd_procedural.py").main(file_path=str(tmp_path / "missing.usd"))

    assert result["success"] is False
    assert "does not exist" in result["message"]
    assert runtime.created_usd_nodes == []


def test_create_arnold_usd_procedural_rejects_unsupported_extension(monkeypatch, tmp_path):
    stage = tmp_path / "signal_forge.fbx"
    stage.write_text("asset", encoding="utf-8")
    runtime = _FakeArnoldRuntime()
    _install_fake_pymxs(monkeypatch, runtime)

    result = _load_action("action_create_arnold_usd_procedural.py").main(file_path=str(stage))

    assert result["success"] is False
    assert "Unsupported USD file extension" in result["message"]
    assert runtime.created_usd_nodes == []


def test_create_arnold_usd_procedural_requires_maxtoa(monkeypatch, tmp_path):
    stage = tmp_path / "signal_forge.usdc"
    stage.write_bytes(b"PXR-USDC")
    runtime = _FakeRuntime([])
    _install_fake_pymxs(monkeypatch, runtime)

    result = _load_action("action_create_arnold_usd_procedural.py").main(file_path=str(stage))

    assert result["success"] is False
    assert "MAXtoA" in result["message"]
