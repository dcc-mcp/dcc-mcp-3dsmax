"""Tests for the bundled 3ds Max render and viewport skill."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-render"
LOOKDEV_SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-lookdev"


def _load_action(script_name: str):
    return _load_action_from(SKILL_DIR, script_name)


def _load_action_from(skill_dir: Path, script_name: str):
    path = skill_dir / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Viewport:
    def __init__(self) -> None:
        self.camera = None


class _FakeNode:
    def __init__(self, name: str, handle: int, *, is_camera: bool = False, material=None) -> None:
        self.name = name
        self.handle = handle
        self.is_camera = is_camera
        self.material = material
        self.isHidden = False
        self.parent = None


class _FakeRuntime:
    def __init__(self) -> None:
        self.camera = _FakeNode("main_camera", 84, is_camera=True)
        self.mesh = _FakeNode("hero_mesh", 42, material=object())
        self.objects = [self.camera, self.mesh]
        self.viewport = _Viewport()
        self.activeCamera = self.camera
        self.renderWidth = 1280
        self.renderHeight = 720
        self.animationRangeStart = 1
        self.animationRangeEnd = 24
        self.rendOutputFilename = ""
        self.rendSaveFile = False
        self.currentRenderer = object()
        self.renderQualityPreset = "preview"
        self.last_render_kwargs = None
        self.ColorPipelineMgr = _ColorPipelineManager()

    def Name(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
        return value

    def classOf(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        return "Targetcamera" if node is self.camera else type(node).__name__

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def captureViewport(self, output_path):  # noqa: N802 - mirrors pymxs runtime naming.
        Path(output_path).write_text("viewport", encoding="utf-8")

    def createPreview(self, output_path, start_frame=None, end_frame=None):  # noqa: N802 - mirrors pymxs runtime naming.
        text = "preview:{}-{}".format(start_frame, end_frame)
        Path(output_path).write_text(text, encoding="utf-8")

    def render(self, *, outputfile, camera=None, vfb=True):
        self.last_render_kwargs = {"outputfile": outputfile, "camera": camera, "vfb": vfb}
        Path(outputfile).write_text("render", encoding="utf-8")
        return object()


class _ColorPipelineManager:
    def __init__(self) -> None:
        self.Mode = "OCIO_Default"
        self.OCIOConfigPath = ""
        self.RenderingColorSpace = "ACEScg"
        self.DataColorSpace = "Raw"
        self.Status = "Normal"
        self.Locked = True
        self._display = "Rec.1886 Rec.709 - Display"
        self._view_transform = "ACES 1.0 SDR-video"

    def GetDisplayList(self):  # noqa: N802 - mirrors MAXScript interface naming.
        return ["sRGB", "Rec.1886 Rec.709 - Display"]

    def ReInitialize(self):  # noqa: N802 - mirrors MAXScript interface naming.
        return True

    def GetViewList(self, display):  # noqa: N802 - mirrors MAXScript interface naming.
        assert display == "sRGB"
        return ["un-tone-mapped", "ACES 1.0 SDR-video"]

    def SetDefaultDisplayViewTransform(  # noqa: N802 - mirrors MAXScript interface naming.
        self, target, automatic, *, display, viewTransform
    ):
        assert target == "FrameBuffer"
        assert automatic is False
        self._display = display
        self._view_transform = viewTransform

    def GetDefaultDisplayViewTransform(  # noqa: N802 - mirrors MAXScript interface naming.
        self, target, automatic, display, viewTransform
    ):
        assert target == "FrameBuffer"
        return None, False, self._display, self._view_transform


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime, byref=lambda value: value))
    return runtime


def test_render_output_tools_validate_paths_and_create_artifacts(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch)
    capture_path = tmp_path / "viewport.png"
    capture_path.write_text("old", encoding="utf-8")
    preview_path = tmp_path / "preview.avi"

    from dcc_mcp_3dsmax._executor import run_skill_script

    blocked = _load_action("action_capture_viewport.py").main(str(capture_path), overwrite=False)
    captured = _load_action("action_capture_viewport.py").main(str(capture_path), overwrite=True)
    preview = run_skill_script(
        str(SKILL_DIR / "action_create_preview.py"),
        {"output_path": str(preview_path), "start_frame": 1, "end_frame": 12},
    )

    assert blocked["success"] is False
    assert "already exists" in blocked["message"]
    assert captured["success"] is True
    assert captured["data"]["artifact"]["size_bytes"] == len("viewport")
    assert preview["success"] is True
    assert preview["data"]["artifact"]["extension"] == ".avi"


def test_render_read_tools_return_settings_and_statistics(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    settings = _load_action("action_get_render_settings.py").main()
    stats = _load_action("action_get_scene_render_statistics.py").main()

    assert settings["success"] is True
    assert settings["data"]["settings"]["width"] == 1280
    assert settings["data"]["settings"]["camera"] == "main_camera"
    assert stats["data"]["statistics"]["camera_count"] == 1
    assert stats["data"]["statistics"]["frame_count"] == 24


def test_render_setting_mutations_update_runtime(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    output_path = tmp_path / "render.png"

    output = _load_action("action_set_render_output_options.py").main(output_path=str(output_path), save_file=True)
    bad_range = _load_action("action_set_frame_range.py").main(start_frame=20, end_frame=10)
    frame_range = _load_action("action_set_frame_range.py").main(start_frame=10, end_frame=20)
    resolution = _load_action("action_set_render_resolution.py").main(width=1920, height=1080)
    camera = _load_action("action_set_render_camera.py").main(camera_name="main_camera")
    not_camera = _load_action("action_set_render_camera.py").main(camera_name="hero_mesh")
    preset = _load_action("action_set_render_quality_preset.py").main("final")

    assert output["success"] is True
    assert runtime.rendOutputFilename == str(output_path)
    assert runtime.rendSaveFile is True
    assert bad_range["success"] is False
    assert frame_range["data"]["settings"]["frame_start"] == 10
    assert resolution["data"]["settings"]["width"] == 1920
    assert camera["success"] is True
    assert runtime.viewport.camera is runtime.camera
    assert not_camera["success"] is False
    assert preset["success"] is True
    assert runtime.renderQualityPreset == "final"


def test_render_camera_uses_runtime_class_for_pymxs_wrapper(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.camera.is_camera = False

    selected = _load_action("action_set_render_camera.py").main(camera_name="main_camera")

    assert selected["success"] is True
    assert runtime.activeCamera is runtime.camera
    assert runtime.viewport.camera is runtime.camera


def test_render_scene_uses_pymxs_outputfile_and_camera_keywords(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    output_path = tmp_path / "beauty.png"

    rendered = _load_action("action_render_scene.py").main(
        output_path=str(output_path),
        overwrite=True,
        width=960,
        height=540,
        camera_name="main_camera",
    )

    assert rendered["success"] is True
    assert runtime.last_render_kwargs == {
        "outputfile": str(output_path),
        "camera": runtime.camera,
        "vfb": False,
    }


def test_color_management_declares_verified_frame_buffer_transform(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    config = tmp_path / "studio.ocio"
    config.write_text("ocio_profile_version: 2", encoding="utf-8")
    output_path = tmp_path / "beauty.png"

    from dcc_mcp_3dsmax._executor import run_skill_script

    configured = run_skill_script(
        str(LOOKDEV_SKILL_DIR / "action_set_color_management.py"),
        {
            "ocio_config_path": str(config),
            "display": "sRGB",
            "view_transform": "un-tone-mapped",
        },
    )
    rendered = run_skill_script(
        str(SKILL_DIR / "action_render_scene.py"),
        {"output_path": str(output_path), "overwrite": True},
    )

    assert configured["success"] is True, configured
    assert configured["data"]["display"] == "sRGB"
    assert configured["data"]["view_transform"] == "un-tone-mapped"
    assert rendered["success"] is True
    assert rendered["data"]["settings"]["view_transform"] == "un-tone-mapped"
    assert runtime.ColorPipelineMgr._view_transform == "un-tone-mapped"


def test_color_management_rejects_unknown_display_without_persisting_mutation(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    config = tmp_path / "studio.ocio"
    config.write_text("ocio_profile_version: 2", encoding="utf-8")

    result = _load_action_from(LOOKDEV_SKILL_DIR, "action_set_color_management.py").main(
        ocio_config_path=str(config),
        display="Missing display",
        view_transform="un-tone-mapped",
    )

    assert result["success"] is False
    assert "Unknown display" in result["data"]["error"]
    assert runtime.ColorPipelineMgr.Mode == "OCIO_Default"
    assert runtime.ColorPipelineMgr.Locked is True
    assert runtime.ColorPipelineMgr._display == "Rec.1886 Rec.709 - Display"


def test_color_management_fails_before_mutation_when_display_snapshot_is_unavailable(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    manager = runtime.ColorPipelineMgr
    config = tmp_path / "studio.ocio"
    config.write_text("ocio_profile_version: 2", encoding="utf-8")
    setter_calls = []
    reinitialize_calls = []
    previous = {
        "mode": manager.Mode,
        "config": manager.OCIOConfigPath,
        "rendering": manager.RenderingColorSpace,
        "data": manager.DataColorSpace,
        "locked": manager.Locked,
        "display": manager._display,
        "view_transform": manager._view_transform,
        "ocio_env": os.environ.get("OCIO"),
    }

    def fail_readback(*_args):
        raise RuntimeError("display snapshot unavailable")

    def record_setter(*args, **kwargs):
        setter_calls.append((args, kwargs))

    def record_reinitialize():
        reinitialize_calls.append(True)
        return True

    manager.GetDefaultDisplayViewTransform = fail_readback
    manager.SetDefaultDisplayViewTransform = record_setter
    manager.ReInitialize = record_reinitialize

    result = _load_action_from(LOOKDEV_SKILL_DIR, "action_set_color_management.py").main(
        ocio_config_path=str(config),
        display="sRGB",
        view_transform="un-tone-mapped",
    )

    assert result["success"] is False
    assert result["data"]["reason"] == "display_view_snapshot_unavailable"
    assert result["data"]["mutation_started"] is False
    assert result["data"]["rolled_back"] is False
    assert setter_calls == []
    assert reinitialize_calls == []
    assert manager.Mode == previous["mode"]
    assert manager.OCIOConfigPath == previous["config"]
    assert manager.RenderingColorSpace == previous["rendering"]
    assert manager.DataColorSpace == previous["data"]
    assert manager.Locked == previous["locked"]
    assert manager._display == previous["display"]
    assert manager._view_transform == previous["view_transform"]
    assert os.environ.get("OCIO") == previous["ocio_env"]


def test_render_hdr_uses_pymxs_outputfile_and_camera_keywords(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch)
    output_path = tmp_path / "beauty.exr"

    rendered = _load_action("action_render_hdr.py").main(
        output_path=str(output_path),
        overwrite=True,
        width=960,
        height=540,
        camera_name="main_camera",
    )

    assert rendered["success"] is True
    assert runtime.last_render_kwargs == {
        "outputfile": str(output_path),
        "camera": runtime.camera,
        "vfb": False,
    }
