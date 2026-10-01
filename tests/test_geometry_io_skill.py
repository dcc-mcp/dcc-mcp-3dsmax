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


class _FakeTime:
    def __init__(self, frame: float) -> None:
        self.frame = float(frame)


class _FakeInterval:
    def __init__(self, start: float, end: float) -> None:
        self.start = _FakeTime(start)
        self.end = _FakeTime(end)


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
        self.frameRate = 30.0
        self.animationRange = _FakeInterval(0, 100)
        self.timeline_calls = []

    def Name(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
        return "#{}".format(value)

    def Interval(self, start, end):  # noqa: N802 - mirrors pymxs runtime naming.
        self.timeline_calls.append((start, end))
        return _FakeInterval(start, end)

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


# --------------------------------------------------------------- FBX timeline
# The 3ds Max FBX importer keeps the scene's own time settings, so a 24 fps
# clip imported into a 30 fps scene no longer matches its source frame for
# frame. Import must align the timeline first, or say so.
def _clip_fbx(tmp_path, name="clip.fbx", frame_rate=24.0, start_frame=1, end_frame=24):
    from test_fbx_time import _binary_fbx, _span

    time_mode = {24.0: 11, 30.0: 6, 25.0: 10}.get(frame_rate, 11)
    start, stop = _span(start_frame, end_frame, frame_rate)
    return _binary_fbx(tmp_path / name, time_mode=time_mode, start=start, stop=stop)


def test_import_fbx_adopts_the_source_timeline(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    path = _clip_fbx(tmp_path, frame_rate=24.0, start_frame=1, end_frame=24)

    result = _load_action("action_import_fbx.py").main(str(path))

    assert result["success"] is True
    assert result["data"]["warnings"] == []
    timeline = result["data"]["timeline"]
    assert timeline["applied"] is True
    assert timeline["source"]["frame_rate"] == 24.0
    assert timeline["scene_before"] == {"frame_rate": 30.0, "frame_start": 0, "frame_end": 100}
    assert timeline["scene_after"] == {"frame_rate": 24.0, "frame_start": 1, "frame_end": 24}
    assert timeline["frame_rate_matches"] is True
    assert runtime.frameRate == 24.0
    assert runtime.timeline_calls == [(1, 24)]


def test_import_fbx_reports_the_timeline_for_a_non_fbx_import(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    source = tmp_path / "prop.obj"
    source.write_text("obj", encoding="utf-8")

    result = _load_action("action_import_geometry.py").main(str(source))

    assert result["success"] is True
    assert result["data"]["timeline"] is None
    assert runtime.timeline_calls == []  # Non-FBX imports never touch the timeline.


def test_import_fbx_warns_when_the_source_timeline_cannot_be_read(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    path = tmp_path / "clip.fbx"
    path.write_text("not really an fbx", encoding="utf-8")

    result = _load_action("action_import_fbx.py").main(str(path))

    assert result["success"] is True
    assert runtime.frameRate == 30.0
    assert runtime.timeline_calls == []
    warnings = result["data"]["warnings"]
    assert any("Could not read the FBX source time settings" in warning for warning in warnings)
    assert result["data"]["timeline"]["applied"] is False


def test_import_fbx_off_mode_leaves_the_timeline_and_warns(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    path = _clip_fbx(tmp_path, frame_rate=24.0, start_frame=1, end_frame=24)

    result = _load_action("action_import_fbx.py").main(str(path), timeline_mode="off")

    assert result["success"] is True
    assert runtime.frameRate == 30.0
    assert runtime.timeline_calls == []
    warnings = result["data"]["warnings"]
    assert any("do not correspond 1:1" in warning for warning in warnings)
    assert result["data"]["timeline"]["frame_rate_matches"] is False


def test_import_fbx_union_mode_keeps_the_wider_range(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.animationRange = _FakeInterval(0, 200)
    path = _clip_fbx(tmp_path, frame_rate=24.0, start_frame=1, end_frame=24)

    result = _load_action("action_import_fbx.py").main(str(path), timeline_mode="union")

    assert result["data"]["timeline"]["scene_after"] == {"frame_rate": 24.0, "frame_start": 0, "frame_end": 200}
    assert result["data"]["timeline"]["range_covers_source"] is True


def test_import_fbx_keeps_the_timeline_when_animation_is_skipped(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    path = _clip_fbx(tmp_path, frame_rate=24.0, start_frame=1, end_frame=24)

    result = _load_action("action_import_fbx.py").main(str(path), include_animation=False)

    assert runtime.frameRate == 30.0
    assert runtime.timeline_calls == []
    assert result["data"]["warnings"]
    assert result["data"]["timeline"]["applied"] is False


def test_import_fbx_rejects_an_unsupported_timeline_mode(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch)
    path = _clip_fbx(tmp_path)

    result = _load_action("action_import_fbx.py").main(str(path), timeline_mode="sideways")

    assert result["success"] is False
    assert "Unsupported FBX timeline_mode" in result["message"]
    assert result["data"]["supported_timeline_modes"] == ["off", "source", "union"]


def test_import_geometry_rejects_an_unsupported_timeline_mode(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch)
    path = _clip_fbx(tmp_path)

    result = _load_action("action_import_geometry.py").main(str(path), timeline_mode="sideways")

    assert result["success"] is False
    assert "Unsupported FBX timeline_mode" in result["message"]


def test_import_fbx_restores_the_timeline_the_importer_reset(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    path = _clip_fbx(tmp_path, frame_rate=24.0, start_frame=1, end_frame=24)

    def reset_timeline(*args, **kwargs):
        runtime.frameRate = 30.0
        runtime.animationRange = _FakeInterval(0, 100)
        return True

    runtime.importFile = reset_timeline

    result = _load_action("action_import_fbx.py").main(str(path))

    timeline = result["data"]["timeline"]
    assert runtime.frameRate == 24.0
    assert runtime.animationRange.end.frame == 24
    assert timeline["scene_after"] == {"frame_rate": 24.0, "frame_start": 1, "frame_end": 24}
    assert any("re-applied" in warning for warning in result["data"]["warnings"])


def test_import_fbx_reports_the_timeline_on_a_failed_import(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    path = _clip_fbx(tmp_path, frame_rate=24.0, start_frame=1, end_frame=24)
    runtime.importFile = lambda *args, **kwargs: False

    result = _load_action("action_import_fbx.py").main(str(path))

    assert result["success"] is False
    assert result["data"]["timeline"]["applied"] is True
    assert result["data"]["timeline"]["scene_after"] == {"frame_rate": 24.0, "frame_start": 1, "frame_end": 24}
