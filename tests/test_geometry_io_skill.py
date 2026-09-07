"""Tests for the bundled 3ds Max geometry I/O skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-geometry-io"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNode:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.isHidden = False
        self.parent = None


class _FakeRuntime:
    def __init__(self) -> None:
        self.hero = _FakeNode("hero_box", 42)
        self.helper = _FakeNode("helper", 43)
        self.objects = [self.hero, self.helper]
        self.selection = [self.hero]
        self.sceneMaterials = ["mat_body", "mat_trim"]
        self.FBXIMP = "FBXIMP"
        self.FBXEXP = "FBXEXP"
        self.OBJEXP = "OBJEXP"
        self.import_params = []
        self.export_params = []
        self.import_calls = []
        self.export_calls = []

    def Name(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
        return "#{}".format(value)

    def FbxImporterSetParam(self, key, value):  # noqa: N802 - mirrors pymxs runtime naming.
        self.import_params.append((key, value))
        return "OK"

    def FbxExporterSetParam(self, key, value):  # noqa: N802 - mirrors pymxs runtime naming.
        self.export_params.append((key, value))
        return "OK"

    def importFile(self, file_path, *args, **kwargs):  # noqa: N802 - mirrors pymxs runtime naming.
        path = Path(file_path)
        node = _FakeNode(path.stem, max(item.handle for item in self.objects) + 1)
        self.objects.append(node)
        self.import_calls.append({"file_path": str(path), "args": args, "kwargs": kwargs})
        return True

    def exportFile(self, output_path, *args, **kwargs):  # noqa: N802 - mirrors pymxs runtime naming.
        path = Path(output_path)
        path.write_text("exported geometry", encoding="utf-8")
        self.export_calls.append({"output_path": str(path), "args": args, "kwargs": kwargs})
        return True


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_validate_geometry_file_checks_extension_and_existence(tmp_path):
    action = _load_action("action_validate_geometry_file.py")
    fbx_path = tmp_path / "asset.fbx"
    fbx_path.write_text("fbx", encoding="utf-8")
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("notes", encoding="utf-8")

    valid = action.main(str(fbx_path), expected_format="fbx")
    bad_extension = action.main(str(unsupported))
    missing = action.main(str(tmp_path / "missing.obj"))

    assert valid["success"] is True
    assert valid["data"]["format"] == "fbx"
    assert valid["data"]["file"]["size_bytes"] == 3
    assert bad_extension["success"] is False
    assert "Unsupported" in bad_extension["message"]
    assert missing["success"] is False
    assert "does not exist" in missing["message"]


def test_import_fbx_returns_created_nodes_and_applies_options(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    fbx_path = tmp_path / "hero_asset.fbx"
    fbx_path.write_text("fbx", encoding="utf-8")
    action = _load_action("action_import_fbx.py")

    result = action.main(str(fbx_path), mode="merge", units="cm", up_axis="Z", include_animation=False)

    assert result["success"] is True
    assert result["data"]["created_count"] == 1
    assert result["data"]["created_nodes"][0]["node_name"] == "hero_asset"
    assert ("Mode", "#merge") in runtime.import_params
    assert ("ConvertUnit", "cm") in runtime.import_params
    assert ("UpAxis", "Z") in runtime.import_params
    assert ("Animation", False) in runtime.import_params
    assert runtime.import_calls[0]["kwargs"]["using"] == "FBXIMP"


def test_export_fbx_validates_overwrite_and_returns_counts(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    output_path = tmp_path / "scene.fbx"
    output_path.write_text("existing", encoding="utf-8")
    action = _load_action("action_export_fbx.py")

    blocked = action.main(str(output_path), selected_only=True, overwrite=False)
    exported = action.main(
        str(output_path),
        selected_only=True,
        overwrite=True,
        units="cm",
        up_axis="Y",
        include_animation=False,
        embed_textures=True,
        ascii=True,
    )

    assert blocked["success"] is False
    assert "already exists" in blocked["message"]
    assert exported["success"] is True
    assert exported["data"]["exported_node_count"] == 1
    assert exported["data"]["material_count"] == 2
    assert exported["data"]["file"]["size_bytes"] == len("exported geometry")
    assert runtime.export_calls[0]["kwargs"]["selectedOnly"] is True
    assert runtime.export_calls[0]["kwargs"]["using"] == "FBXEXP"
    assert ("EmbedTextures", True) in runtime.export_params
    assert ("ASCII", True) in runtime.export_params


def test_generic_import_and_obj_export_run_through_adapter_executor(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    obj_input = tmp_path / "prop.obj"
    obj_input.write_text("obj", encoding="utf-8")
    obj_output = tmp_path / "scene.obj"

    from dcc_mcp_3dsmax._executor import run_skill_script

    imported = run_skill_script(str(SKILL_DIR / "action_import_geometry.py"), {"file_path": str(obj_input)})
    exported = run_skill_script(
        str(SKILL_DIR / "action_export_obj.py"),
        {"output_path": str(obj_output), "selected_only": False, "overwrite": False},
    )

    assert imported["success"] is True
    assert imported["data"]["format"] == "obj"
    assert runtime.import_calls[0]["kwargs"] == {}
    assert exported["success"] is True
    assert exported["data"]["format"] == "obj"
    assert exported["data"]["exported_node_count"] == 3
    assert runtime.export_calls[0]["kwargs"]["selectedOnly"] is False
    assert runtime.export_calls[0]["kwargs"]["using"] == "OBJEXP"


@pytest.mark.parametrize("raises", [False, True])
def test_partial_import_failure_reports_created_nodes(monkeypatch, tmp_path, raises):
    runtime = _install_fake_pymxs(monkeypatch)
    source = tmp_path / "partial.obj"
    source.write_text("obj", encoding="utf-8")

    def partial_import(*args, **kwargs):
        runtime.objects.append(_FakeNode("partial", 44))
        if raises:
            raise RuntimeError("Importer failed after creating a node")
        return False

    runtime.importFile = partial_import
    result = _load_action("action_import_geometry.py").main(str(source))
    assert result["success"] is False
    assert result["data"]["created_count"] == 1
    assert result["data"]["created_nodes"][0]["node_name"] == "partial"
    assert "before retrying" in result["data"]["recovery"]
    assert len(runtime.objects) == 3  # Reporting must not delete partial scene work.


@pytest.mark.parametrize("empty_file", [False, True])
def test_export_without_output_bytes_is_failure(monkeypatch, tmp_path, empty_file):
    runtime = _install_fake_pymxs(monkeypatch)
    output = tmp_path / "missing.obj"

    def incomplete_export(*args, **kwargs):
        if empty_file:
            output.touch()
        return True

    runtime.exportFile = incomplete_export
    result = _load_action("action_export_obj.py").main(str(output))
    assert result["success"] is False
    assert not result["data"]["file"]["size_bytes"]
