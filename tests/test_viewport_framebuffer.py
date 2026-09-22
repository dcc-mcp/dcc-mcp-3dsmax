"""Offline tests for the viewport, frame buffer, and agent viewport helpers.

The doubles model a host that exposes pymxs-style contracts. Every assertion
checks that a rejected or silently ignored write is reported as a failure, and
that a host which cannot report state is reported as unverified.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _viewport_utils as viewport_utils  # noqa: E402

VIEWPORT_SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-viewport"
RENDER_SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-render"


def _load_action(script_name: str):
    path = VIEWPORT_SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_viewport_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_render_action(script_name: str):
    """Load an action from the render skill (vray_ipr lives there)."""
    path = RENDER_SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_vp_render_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Matrix:
    def __init__(self, rows) -> None:
        for index, values in enumerate(rows, start=1):
            setattr(self, "row{}".format(index), _Point3(*values))


class _Point3:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


class _Camera:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle


class _Viewport:
    """Active viewport double: a readable view plus verified option writes."""

    def __init__(self, view: str = "perspective") -> None:
        self.view = view
        self.tm = _Matrix([(1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0)])
        self.shadingMode = "realistic"
        self.edgedFaces = False
        self.showGrid = True
        self.showSafeFrame = False
        self.showStatistics = False
        self.layout = "single"
        self.camera = None
        self.switches: list = []

    def getType(self):  # noqa: N802 - mirrors pymxs naming.
        return self.view

    def setType(self, view):  # noqa: N802 - mirrors pymxs naming.
        self.switches.append(view)
        if view == "iso":
            # Host accepted the call but did not move: must be reported.
            return None
        self.view = view

    def getTM(self):  # noqa: N802 - mirrors pymxs naming.
        return self.tm

    def setTM(self, matrix):  # noqa: N802 - mirrors pymxs naming.
        self.tm = matrix

    def setCamera(self, camera):  # noqa: N802 - mirrors pymxs naming.
        self.camera = camera

    def getCamera(self):  # noqa: N802 - mirrors pymxs naming.
        return self.camera


class _StubbornShadingViewport(_Viewport):
    """A viewport whose shading write is accepted and then ignored."""

    @property
    def shadingMode(self):  # noqa: N802 - mirrors pymxs naming.
        return "realistic"

    @shadingMode.setter
    def shadingMode(self, value):  # noqa: N802 - mirrors pymxs naming.
        return None


class _Renderer:
    """Renderer double that reports a frame buffer rectangle."""

    def __init__(self, rect: Any = None, *, ipr_running: Any = None) -> None:
        self.vfbRect = rect
        self.IPRRunning = ipr_running


def _renderer(rect: Any = None, *, name: str = "VRayRenderer", ipr_running: Any = None) -> Any:
    """Build a renderer double whose class name drives provider detection."""
    return type(name, (_Renderer,), {})(rect=rect, ipr_running=ipr_running)


class _Runtime:
    def __init__(self, *, renderer: Any = None, rect: Any = None) -> None:
        self.renderer = renderer if renderer is not None else _renderer(rect)
        self.renderers = types.SimpleNamespace(current=self.renderer)
        self.viewport = _Viewport()
        self.cameras = [_Camera("main_camera", 84)]
        self.capture_calls: list = []
        self.capture_impl = None
        self.capture_result = None
        self.capture_accepts_rect = True
        self.captureScreen = self._capture_screen
        self.activeCamera = self.cameras[0]
        self.agent_viewports: dict = {}

    def enable_agent_viewport_factory(self, *, move_user_view: bool = False, break_restore: bool = False) -> None:
        """Install an extended-viewport factory, optionally a hostile one.

        ``move_user_view`` moves the user's view while creating, and
        ``break_restore`` makes every later view write fail so the restore path
        cannot succeed.
        """

        def factory(name=None):
            if move_user_view:
                self.viewport.view = "top"
            if break_restore:
                self.viewport.setType = _raise_busy
                self.viewport.setTM = _raise_busy
            key = str(name)
            viewport = _Viewport("top")
            viewport.close = lambda: self.agent_viewports.pop(key, None)
            self.agent_viewports[key] = viewport
            return viewport

        self.createExtendedViewport = factory
        self.getExtendedViewport = lambda name: self.agent_viewports.get(str(name))

    # -- host contracts ------------------------------------------------
    def _capture_screen(self, path: str, rect: Any = None) -> Any:
        if not self.capture_accepts_rect:
            raise TypeError("_capture_screen() takes 1 positional argument")
        self.capture_calls.append({"path": path, "rect": rect})
        if self.capture_impl is not None:
            return self.capture_impl(path, rect)
        Path(path).write_text("screen", encoding="utf-8")
        return {"rect": rect} if rect is not None else None

    def captureViewport(self, path: str) -> Any:  # noqa: N802 - mirrors pymxs naming.
        Path(path).write_text("viewport", encoding="utf-8")

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs naming.
        for camera in self.cameras:
            if camera.name == name:
                return camera
        return None

    def isProperty(self, node, attribute):  # noqa: N802 - mirrors pymxs naming.
        return hasattr(node, attribute)


def _raise_busy(*args, **kwargs):
    raise RuntimeError("viewport is busy")


def _install_fake_pymxs(monkeypatch, runtime: _Runtime) -> _Runtime:
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime, byref=lambda value: value))
    return runtime


@pytest.fixture(autouse=True)
def _reset_agent_viewports():
    viewport_utils.reset_agent_viewports()
    yield
    viewport_utils.reset_agent_viewports()


# ---------------------------------------------------------------------------
# capture_screen
# ---------------------------------------------------------------------------


def test_capture_screen_crops_to_the_frame_buffer_region(monkeypatch, tmp_path):
    rect = {"left": 100, "top": 50, "width": 640, "height": 360}
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=rect))
    target = tmp_path / "vfb.png"

    result = _load_action("action_capture_screen.py").main(str(target), source="vray")

    assert result["success"] is True
    assert result["data"]["provider"] == "vray"
    assert result["data"]["cropped"] is True
    assert result["data"]["rect"]["width"] == 640
    assert runtime.capture_calls[0]["rect"]["left"] == 100
    assert target.exists()
    assert target.stat().st_size > 0


def test_capture_screen_auto_picks_the_frame_buffer_of_the_active_renderer(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 800, 600)))

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "auto.png"))

    assert result["success"] is True
    assert result["data"]["provider"] == "vray"
    assert result["data"]["rect"] == {"left": 0, "top": 0, "right": 800, "bottom": 600, "width": 800, "height": 600}


def test_capture_screen_reports_unresolvable_region_as_failure(monkeypatch, tmp_path):
    """No region means no crop: it must fail, not silently capture the desktop."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=None))
    target = tmp_path / "vfb.png"

    result = _load_action("action_capture_screen.py").main(str(target), source="corona")

    assert result["success"] is False
    assert "Could not resolve" in result["message"]
    assert result["data"]["probed"]
    assert runtime.capture_calls == []
    assert not target.exists()


def test_capture_screen_fails_when_the_host_ignores_the_region(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(10, 10, 200, 200)))
    runtime.capture_accepts_rect = False

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"))

    assert result["success"] is False
    assert "cannot be cropped" in result["message"]


def test_capture_screen_without_crop_reports_a_warning_instead_of_pretending(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=None))
    target = tmp_path / "screen.png"

    result = _load_action("action_capture_screen.py").main(str(target), source="vray", crop=False)

    assert result["success"] is True
    assert result["data"]["cropped"] is False
    assert any("capturing the full desktop" in warning for warning in result["data"]["warnings"])


def test_capture_screen_rejects_a_reported_region_that_does_not_match(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    def mismatch(path, rect):
        Path(path).write_text("screen", encoding="utf-8")
        return {"rect": {"left": 0, "top": 0, "width": 32, "height": 32}}

    runtime.capture_impl = mismatch

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"))

    assert result["success"] is False
    assert "differs" in result["message"]
    assert result["data"]["captured_rect"]["width"] == 32


def test_capture_screen_reports_an_empty_capture(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.capture_impl = lambda path, rect: Path(path).write_text("", encoding="utf-8")

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"))

    assert result["success"] is False
    assert "empty" in result["message"]


def test_capture_screen_rejects_unsupported_source_and_existing_files(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    target = tmp_path / "vfb.png"
    target.write_text("old", encoding="utf-8")
    action = _load_action("action_capture_screen.py")

    assert action.main(str(target))["success"] is False
    assert "already exists" in action.main(str(target))["message"]
    bad_source = action.main(str(tmp_path / "other.png"), source="octane")
    assert bad_source["success"] is False
    assert "Unsupported frame buffer source" in bad_source["message"]


# ---------------------------------------------------------------------------
# capture_multi_view
# ---------------------------------------------------------------------------


def test_capture_multi_view_captures_each_view_and_restores_the_user_view(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.viewport.view = "perspective"

    result = _load_action("action_capture_multi_view.py").main(
        str(tmp_path), views=["front", "top"], composite=False
    )

    assert result["success"] is True
    assert result["data"]["capture_count"] == 2
    assert result["data"]["view_restored"] is True
    assert runtime.viewport.view == "perspective"
    for name in ("view_front.png", "view_top.png"):
        assert (tmp_path / name).is_file()
        assert (tmp_path / name).stat().st_size > 0


def test_capture_multi_view_reports_an_unverified_view_switch_as_failure(monkeypatch, tmp_path):
    """A switch the host ignored would capture the wrong angle: it must fail."""
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    result = _load_action("action_capture_multi_view.py").main(
        str(tmp_path), views=["iso", "top"], composite=False
    )

    assert result["success"] is False
    assert result["data"]["errors"]
    assert result["data"]["errors"][0]["view"] == "iso"
    assert result["data"]["view_restored"] is True
    assert not (tmp_path / "view_iso.png").exists()


def test_capture_multi_view_refuses_when_the_view_cannot_be_read_back(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.viewport.getType = None
    runtime.viewport.getTM = None

    result = _load_action("action_capture_multi_view.py").main(str(tmp_path), views=["front"], composite=False)

    assert result["success"] is False
    assert "cannot be restored" in result["message"]
    assert runtime.viewport.switches == []


def test_capture_multi_view_rejects_unsupported_views_and_existing_tiles(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    action = _load_action("action_capture_multi_view.py")
    (tmp_path / "view_front.png").write_text("old", encoding="utf-8")

    unsupported = action.main(str(tmp_path), views=["front", "sideways"], composite=False)
    assert unsupported["success"] is False
    assert "Unsupported view" in unsupported["message"]

    existing = action.main(str(tmp_path), views=["front"], composite=False)
    assert existing["success"] is False
    assert "already exist" in existing["message"]
    assert runtime.viewport.switches == []


def test_capture_multi_view_reports_a_missing_compositor(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    monkeypatch.setitem(sys.modules, "PIL", None)

    result = _load_action("action_capture_multi_view.py").main(str(tmp_path), views=["front"], composite=True)

    assert result["success"] is True
    assert result["data"]["sheet"] is None
    assert any("Pillow is not available" in warning for warning in result["data"]["warnings"])
    assert (tmp_path / "view_front.png").is_file()


def test_capture_multi_view_composes_a_contact_sheet_when_pillow_exists(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    monkeypatch.setitem(sys.modules, "PIL", _fake_pillow())
    sheet = tmp_path / "sheet.png"

    result = _load_action("action_capture_multi_view.py").main(
        str(tmp_path), views=["front", "left", "top"], composite=True, sheet_path=str(sheet), columns=2
    )

    assert result["success"] is True
    assert result["data"]["sheet"]["path"] == str(sheet)
    assert result["data"]["sheet"]["exists"] is True
    # Three 4x3 tiles in two columns give an 8x6 sheet.
    assert sheet.read_text(encoding="utf-8") == "sheet:8x6"


class _FakeImage:
    def __init__(self, width: int, height: int, note: str = "") -> None:
        self.width = width
        self.height = height
        self.note = note
        self.pastes = []

    def convert(self, mode: str) -> "_FakeImage":
        return self

    def paste(self, image: "_FakeImage", box) -> None:
        self.pastes.append((image, box))

    def close(self) -> None:
        return None


def _fake_pillow() -> types.ModuleType:
    module = types.ModuleType("PIL")

    class _Image:
        @staticmethod
        def open(path: str) -> _FakeImage:
            return _FakeImage(4, 3, path)

        @staticmethod
        def new(mode: str, size, color) -> _FakeImage:
            image = _FakeImage(size[0], size[1])
            image.mode = mode
            image.color = color
            return image

    module.Image = _Image

    def _paste_and_save(image: _FakeImage, path: str) -> None:
        Path(path).write_text("sheet:{}x{}".format(image.width, image.height), encoding="utf-8")

    _FakeImage.save = _paste_and_save  # type: ignore[attr-defined]
    return module


# ---------------------------------------------------------------------------
# agent_viewport
# ---------------------------------------------------------------------------


def test_agent_viewport_creation_fails_loudly_without_a_factory(monkeypatch):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    result = _load_action("action_agent_viewport.py").main(action="ensure")

    assert result["success"] is False
    assert "no extended/floating viewport factory" in result["message"]
    assert result["data"]["probed"]["factories"]


def test_agent_viewport_creates_a_viewport_without_moving_the_user_view(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.viewport.view = "perspective"
    runtime.enable_agent_viewport_factory()

    result = _load_action("action_agent_viewport.py").main(action="ensure", shading="wireframe")

    assert result["success"] is True
    assert result["data"]["created"] is True
    assert result["data"]["user_view_intact"] is True
    assert result["data"]["applied"] == ["shading"]
    assert runtime.viewport.view == "perspective"

    again = _load_action("action_agent_viewport.py").main(action="ensure")
    assert again["data"]["created"] is False


def test_agent_viewport_reports_a_host_that_moves_the_user_view(monkeypatch):
    """A view that moved and was restored is reported as a warning."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.viewport.view = "perspective"
    runtime.enable_agent_viewport_factory(move_user_view=True)

    result = _load_action("action_agent_viewport.py").main(action="ensure")

    assert result["success"] is True
    assert result["data"]["user_view_intact"] is True
    assert runtime.viewport.view == "perspective"
    assert any("changed the active view" in warning for warning in result["data"]["warnings"])


def test_agent_viewport_fails_when_the_moved_view_cannot_be_restored(monkeypatch):
    """A view that moved and cannot be restored is a failure, not a warning."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.viewport.view = "perspective"
    runtime.enable_agent_viewport_factory(move_user_view=True, break_restore=True)

    result = _load_action("action_agent_viewport.py").main(action="ensure")

    assert result["success"] is False
    assert result["data"]["user_view_intact"] is False
    assert "changed the active view" in result["message"]


def test_agent_viewport_close_verifies_the_host_released_it(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.enable_agent_viewport_factory()
    action = _load_action("action_agent_viewport.py")
    action.main(action="ensure")
    handle = runtime.agent_viewports["dcc_mcp_agent_viewport"]
    closed: list = []

    def close():
        closed.append(True)
        runtime.agent_viewports.pop("dcc_mcp_agent_viewport", None)

    handle.close = close

    result = action.main(action="close")

    assert result["success"] is True
    assert result["data"]["closed"] is True
    assert closed == [True]
    assert action.main(action="status")["data"]["exists"] is False


def test_agent_viewport_close_reports_a_host_that_keeps_it(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.enable_agent_viewport_factory()
    action = _load_action("action_agent_viewport.py")
    action.main(action="ensure")
    handle = runtime.agent_viewports["dcc_mcp_agent_viewport"]
    handle.close = lambda: None
    # A host that keeps reporting the viewport after close() must be reported.
    runtime.getExtendedViewport = lambda name: handle

    result = action.main(action="close")

    assert result["success"] is False
    assert "still reports the agent viewport" in result["message"]


# ---------------------------------------------------------------------------
# set_viewport
# ---------------------------------------------------------------------------


def test_agent_viewport_close_reports_a_close_that_raises(monkeypatch):
    """A refused close must fail: success would say the opposite of the truth."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.enable_agent_viewport_factory()
    action = _load_action("action_agent_viewport.py")
    action.main(action="ensure")
    handle = runtime.agent_viewports["dcc_mcp_agent_viewport"]
    handle.close = _raise_busy

    result = action.main(action="close")

    assert result["success"] is False
    assert result["data"]["closed"] is False
    assert "Could not close the agent viewport" in result["message"]
    assert any("viewport is busy" in warning for warning in result["data"]["warnings"])
    # The viewport is still open, so the registry must still know about it.
    assert action.main(action="status")["data"]["exists"] is True


def test_agent_viewport_close_reports_a_host_with_no_close_contract(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.enable_agent_viewport_factory()
    action = _load_action("action_agent_viewport.py")
    action.main(action="ensure")
    handle = runtime.agent_viewports["dcc_mcp_agent_viewport"]
    handle.close = None
    runtime.getExtendedViewport = lambda name: None

    result = action.main(action="close")

    assert result["success"] is False
    assert result["data"]["closed"] is False
    assert result["data"]["probed"]["methods"]


def test_agent_viewport_registry_is_scoped_to_the_host(monkeypatch):
    """A viewport from one host must not be handed to another."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.enable_agent_viewport_factory()
    action = _load_action("action_agent_viewport.py")
    action.main(action="ensure")

    other = _Runtime(rect=(0, 0, 640, 480))
    assert viewport_utils.find_agent_viewport(other, "dcc_mcp_agent_viewport") is None
    assert viewport_utils.find_agent_viewport(runtime, "dcc_mcp_agent_viewport") is not None


def test_set_viewport_applies_options_and_reports_the_readback(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    agent = _Viewport("top")
    runtime.createExtendedViewport = lambda name=None: agent
    runtime.getExtendedViewport = lambda name: agent

    result = _load_action("action_set_viewport.py").main(
        target="agent", shading="shaded", grid=False, edged_faces=True
    )

    assert result["success"] is True
    assert sorted(result["data"]["applied"]) == ["edged_faces", "grid", "shading"]
    assert agent.shadingMode == "shaded"
    assert agent.showGrid is False
    assert agent.edgedFaces is True
    rows = {row["option"]: row for row in result["data"]["options"]}
    assert rows["shading"]["actual"] == "shaded"
    assert rows["grid"]["actual"] is False


def test_set_viewport_reports_a_rejected_option_as_failure(monkeypatch):
    """A host that keeps the old value must not be reported as a success."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    agent = _Viewport("top")

    agent = _StubbornShadingViewport()
    runtime.createExtendedViewport = lambda name=None: agent
    runtime.getExtendedViewport = lambda name: agent

    result = _load_action("action_set_viewport.py").main(target="agent", shading="wireframe")

    assert result["success"] is False
    assert result["data"]["rejected"] == ["shading"]
    assert result["data"]["errors"][0]["requested"] == "wireframe"


def test_set_viewport_rejects_unsupported_values(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    agent = _Viewport("top")
    runtime.createExtendedViewport = lambda name=None: agent
    runtime.getExtendedViewport = lambda name: agent
    action = _load_action("action_set_viewport.py")

    shading = action.main(target="agent", shading="cartoon")
    assert shading["success"] is False
    assert "Unsupported shading mode" in shading["message"]

    layout = action.main(target="agent", layout="nine")
    assert layout["success"] is False
    assert "Unsupported viewport layout" in layout["message"]


def test_set_viewport_never_falls_back_to_the_user_viewport(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    result = _load_action("action_set_viewport.py").main(target="agent", grid=False)

    assert result["success"] is False
    assert "No agent viewport" in result["message"]
    assert runtime.viewport.showGrid is True


def test_set_viewport_assigns_and_verifies_a_camera(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    agent = _Viewport("top")
    runtime.createExtendedViewport = lambda name=None: agent
    runtime.getExtendedViewport = lambda name: agent

    result = _load_action("action_set_viewport.py").main(target="agent", camera_name="main_camera")

    assert result["success"] is True
    assert agent.camera.name == "main_camera"
    assert result["data"]["options"][0]["actual"] == "main_camera"

    missing = _load_action("action_set_viewport.py").main(target="agent", camera_name="ghost_camera")
    assert missing["success"] is False


def test_capture_multi_view_protects_the_contact_sheet(monkeypatch, tmp_path):
    """The contact sheet is an output of this call, so overwrite guards it too."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    action = _load_action("action_capture_multi_view.py")
    (tmp_path / "multi_view.png").write_text("old", encoding="utf-8")

    result = action.main(str(tmp_path), views=["front"], composite=True)

    assert result["success"] is False
    assert "already exist" in result["message"]
    assert any("multi_view.png" in path for path in result["data"]["existing"])
    assert runtime.viewport.switches == []


def test_capture_multi_view_validates_the_contact_sheet_path(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    action = _load_action("action_capture_multi_view.py")

    bad_extension = action.main(str(tmp_path), views=["front"], sheet_path=str(tmp_path / "sheet.txt"))
    assert bad_extension["success"] is False
    assert "extension" in bad_extension["message"]

    missing_dir = action.main(str(tmp_path), views=["front"], sheet_path=str(tmp_path / "nope" / "sheet.png"))
    assert missing_dir["success"] is False
    assert "directory does not exist" in missing_dir["message"]


def test_capture_screen_default_path_is_reusable(monkeypatch, tmp_path):
    """The default target is scratch space: default calls must not collide."""
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    action = _load_action("action_capture_screen.py")

    first = action.main()
    second = action.main()

    assert first["success"] is True and second["success"] is True
    assert first["data"]["artifact"]["path"] == second["data"]["artifact"]["path"]


def test_capture_screen_still_guards_an_explicit_path(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    target = tmp_path / "vfb.png"
    target.write_text("old", encoding="utf-8")

    result = _load_action("action_capture_screen.py").main(str(target))

    assert result["success"] is False
    assert "already exists" in result["message"]


def test_set_viewport_counts_only_verified_writes_as_applied(monkeypatch):
    """An unverified write is reported in warnings, not counted as applied."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    class _SilentViewport(_Viewport):
        def __init__(self) -> None:
            super().__init__("perspective")
            self.calls: list = []

        def setGridVisibility(self, value):  # noqa: N802 - mirrors pymxs naming.
            self.calls.append(value)

    silent = _SilentViewport()
    del silent.showGrid
    runtime.viewport = silent

    result = _load_action("action_set_viewport.py").main(target="active", grid=False, edged_faces=True)

    assert result["success"] is True
    assert result["data"]["applied"] == ["edged_faces"]
    assert silent.calls == [False]
    assert any("did not report" in warning for warning in result["data"]["warnings"])


def test_set_viewport_active_target_reports_unverified_writes(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    class _SilentViewport(_Viewport):
        def __init__(self) -> None:
            super().__init__("perspective")
            self.calls: list = []

        def setStatistics(self, value):  # noqa: N802 - mirrors pymxs naming.
            self.calls.append(value)
            return None

    silent = _SilentViewport()
    del silent.showStatistics
    runtime.viewport = silent

    result = _load_action("action_set_viewport.py").main(target="active", statistics=True)

    assert result["success"] is True
    assert silent.calls == [True]
    assert any("did not report the value back" in warning for warning in result["data"]["warnings"])


# ---------------------------------------------------------------------------
# Failure paths that must never look like a success
# ---------------------------------------------------------------------------


def test_capture_screen_reports_a_host_without_a_capture_contract(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    del runtime.captureScreen

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"))

    assert result["success"] is False
    assert "No screen capture operation" in result["message"]
    assert result["data"]["probed"]


def test_capture_screen_reports_a_capture_exception(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    def boom(path, rect=None):
        raise RuntimeError("device is busy")

    runtime.captureScreen = boom

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"))

    assert result["success"] is False
    assert "device is busy" in result["message"]


def test_capture_screen_reports_a_capture_that_writes_nothing(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.captureScreen = lambda path, rect=None: None
    target = tmp_path / "vfb.png"

    result = _load_action("action_capture_screen.py").main(str(target))

    assert result["success"] is False
    assert "empty file" in result["message"]
    assert not target.exists()


def test_capture_screen_reports_a_capture_that_removes_the_file(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    def vanish(path, rect=None):
        Path(path).unlink()

    runtime.captureScreen = vanish
    target = tmp_path / "vfb.png"

    result = _load_action("action_capture_screen.py").main(str(target))

    assert result["success"] is False
    assert "did not produce a file" in result["message"]
    assert not target.exists()


def test_capture_multi_view_rejects_an_empty_view_list(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    result = viewport_utils.capture_multi_view(runtime, tmp_path, views=[], composite=False)

    assert result["success"] is False
    assert "At least one view" in result["message"]


def test_capture_multi_view_reports_a_missing_viewport(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    del runtime.viewport

    result = _load_action("action_capture_multi_view.py").main(str(tmp_path), views=["front"], composite=False)

    assert result["success"] is False
    assert "No active viewport" in result["message"]


def test_capture_multi_view_reports_a_missing_capture_contract(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    monkeypatch.delattr(type(runtime), "captureViewport")
    del runtime.captureScreen

    result = _load_action("action_capture_multi_view.py").main(str(tmp_path), views=["front"], composite=False)

    assert result["success"] is False
    assert "No viewport capture operation" in result["message"]
    assert runtime.viewport.switches == []


def test_capture_multi_view_reports_an_empty_tile(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.captureViewport = lambda path: Path(path).write_text("", encoding="utf-8")

    result = _load_action("action_capture_multi_view.py").main(str(tmp_path), views=["front"], composite=False)

    assert result["success"] is False
    assert result["data"]["errors"][0]["view"] == "front"
    assert result["data"]["view_restored"] is True


def test_agent_viewport_reports_unsupported_action_and_shading(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.enable_agent_viewport_factory()
    action = _load_action("action_agent_viewport.py")

    bad_action = action.main(action="pause")
    assert bad_action["success"] is False
    assert "Unsupported agent viewport action" in bad_action["message"]

    bad_shading = action.main(action="ensure", shading="cartoon")
    assert bad_shading["success"] is False
    assert "Unsupported shading mode" in bad_shading["message"]


def test_agent_viewport_reports_a_factory_that_returns_nothing(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.createExtendedViewport = lambda name=None: None
    runtime.getExtendedViewport = lambda name: None

    result = _load_action("action_agent_viewport.py").main(action="ensure")

    assert result["success"] is False
    assert "returned nothing" in result["message"]


def test_agent_viewport_reports_a_factory_that_raises(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    def factory(name=None):
        raise RuntimeError("viewport limit reached")

    runtime.createExtendedViewport = factory
    runtime.getExtendedViewport = lambda name: None

    result = _load_action("action_agent_viewport.py").main(action="ensure")

    assert result["success"] is False
    assert "viewport limit reached" in result["message"]


def test_agent_viewport_reports_an_unresolvable_camera(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.enable_agent_viewport_factory()

    result = _load_action("action_agent_viewport.py").main(action="ensure", camera_name="ghost_camera")

    assert result["success"] is False
    assert result["data"]["camera_name"] == "ghost_camera"


def test_agent_viewport_reports_a_rejected_option(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.createExtendedViewport = lambda name=None: _StubbornShadingViewport()
    runtime.getExtendedViewport = lambda name: _StubbornShadingViewport()

    result = _load_action("action_agent_viewport.py").main(action="ensure", shading="wireframe")

    assert result["success"] is False
    assert result["data"]["rejected"] == ["shading"]


def test_set_viewport_reports_an_unknown_target(monkeypatch):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    result = _load_action("action_set_viewport.py").main(target="ghost", grid=False)

    assert result["success"] is False
    assert "Unsupported viewport target" in result["message"]


def test_frame_buffer_probe_reports_an_empty_region(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 0, 0)))

    target = viewport_utils.frame_buffer_target(runtime, "vray")

    assert target["rect"] is None
    assert any("empty region" in warning for warning in target["warnings"])


def test_coerce_rect_accepts_objects_and_rejects_garbage():
    rect, reason = viewport_utils.coerce_rect(_RectBox(4, 8, 100, 50), "test")
    assert rect == {"left": 4, "top": 8, "right": 104, "bottom": 58, "width": 100, "height": 50}
    assert reason is None

    bad, reason = viewport_utils.coerce_rect({"left": 0, "top": 0}, "test")
    assert bad is None
    assert "incomplete region" in reason

    bad, reason = viewport_utils.coerce_rect(["a", 1, 2, 3], "test")
    assert bad is None
    assert "non-numeric" in reason


class _RectBox:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _Flag:
    """Descriptor that records writes to a runtime attribute."""

    def __init__(self) -> None:
        self.opened: list = []

    def __set__(self, instance, value):
        self.opened.append(value)

    def __get__(self, instance, owner=None):
        return False


def test_capture_multi_view_reports_a_camera_that_was_moved(monkeypatch, tmp_path):
    """Orbiting leaves the view token untouched: only the transform changes.

    Comparing the token alone would report the user's camera as restored while
    it is looking somewhere else, with no warning at all.
    """
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.viewport.view = "perspective"
    original_tm = runtime.viewport.tm
    moved = _Matrix([(2, 0, 0), (0, 2, 0), (0, 0, 2), (10, 10, 10)])

    def capture_and_move(path):
        runtime.viewport.tm = moved
        Path(path).write_text("viewport", encoding="utf-8")

    runtime.captureViewport = capture_and_move
    # A host that accepts setTM and ignores it: the transform stays moved.
    runtime.viewport.setTM = lambda matrix: None

    result = _load_action("action_capture_multi_view.py").main(str(tmp_path), views=["top"], composite=False)

    assert result["success"] is False
    assert result["data"]["view_restored"] is False
    assert "still reads" in result["message"]
    assert runtime.viewport.tm is not original_tm


def test_capture_multi_view_restores_both_the_view_and_the_transform(monkeypatch, tmp_path):
    """A host that restores both halves is reported as restored."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))
    runtime.viewport.view = "perspective"
    original_tm = runtime.viewport.tm

    result = _load_action("action_capture_multi_view.py").main(
        str(tmp_path), views=["top", "front"], composite=False
    )

    assert result["success"] is True
    assert result["data"]["view_restored"] is True
    assert runtime.viewport.view == "perspective"
    assert runtime.viewport.tm is original_tm


def test_vray_ipr_maps_host_state_tokens(monkeypatch):
    """A value contract like "stopped" must not be coerced with bool()."""

    class _TokenRenderer(_Renderer):
        def __init__(self) -> None:
            super().__init__()
            self.state = "#stopped"
            self.started = []
            self.stopped = []

        def vrayGetIPRState(self):  # noqa: N802 - mirrors pymxs naming.
            return self.state

        def startIPR(self):  # noqa: N802 - mirrors pymxs naming.
            self.started.append("start")
            self.state = "#running"

        def stopIPR(self):  # noqa: N802 - mirrors pymxs naming.
            self.stopped.append("stop")
            self.state = "#stopped"

    renderer = _TokenRenderer()
    _install_fake_pymxs(monkeypatch, _Runtime(renderer=renderer))
    action = _load_render_action("action_vray_ipr.py")

    status = action.main("status")
    assert status["success"] is True
    assert status["data"]["running"] is False

    started = action.main("start")
    assert started["success"] is True
    assert started["data"]["running"] is True

    stopped = action.main("stop")
    assert stopped["success"] is True
    assert stopped["data"]["running"] is False


def test_vray_ipr_reports_an_uninterpretable_state_as_unknown(monkeypatch):
    """A host value this adapter cannot read is unknown, not a guess."""

    class _OddRenderer(_Renderer):
        def vrayGetIPRState(self):  # noqa: N802 - mirrors pymxs naming.
            return "rendering-frame-3"

    _install_fake_pymxs(monkeypatch, _Runtime(renderer=_OddRenderer()))

    result = _load_render_action("action_vray_ipr.py").main("status")

    assert result["success"] is True
    assert result["data"]["running"] is None
    assert any("cannot interpret" in warning for warning in result["data"]["warnings"])


def test_opening_the_frame_buffer_reports_a_failure(monkeypatch, tmp_path):
    """A frame buffer that cannot be brought forward is a warning, not silence."""
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    def boom():
        raise RuntimeError("vfb is busy")

    runtime.showVFB = boom

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"), source="vray")

    assert result["success"] is True
    assert any("vfb is busy" in warning for warning in result["data"]["warnings"])
    assert runtime.capture_calls


def test_opening_the_frame_buffer_reports_a_host_without_a_contract(monkeypatch, tmp_path):
    _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"), source="fstorm")

    assert result["success"] is True
    assert any("no FStorm frame buffer open contract" in warning for warning in result["data"]["warnings"])


def test_opening_the_frame_buffer_through_an_attribute(monkeypatch, tmp_path):
    runtime = _install_fake_pymxs(monkeypatch, _Runtime(rect=(0, 0, 640, 480)))

    # monkeypatch instead of assigning onto the class so the descriptor does
    # not leak into any later test.
    flag = _Flag()
    monkeypatch.setattr(type(runtime), "showVFB", flag, raising=False)

    result = _load_action("action_capture_screen.py").main(str(tmp_path / "vfb.png"), source="vray")

    assert result["success"] is True
    assert flag.opened == [True]
