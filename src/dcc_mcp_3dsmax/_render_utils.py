"""Helpers for 3ds Max render and viewport skill scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import is_camera_node, iter_scene_nodes, node_identity, resolve_node_object

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
PREVIEW_EXTENSIONS = {".avi", ".mp4", ".mov"}
QUALITY_PRESETS = {
    "draft": {"sampling": 0.25, "antialiasing": False},
    "preview": {"sampling": 0.5, "antialiasing": True},
    "final": {"sampling": 1.0, "antialiasing": True},
}


def render_success(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent success envelope."""
    return {"success": True, "status": "success", "message": message, "data": data}


def render_error(message: str, **data: Any) -> Dict[str, Any]:
    """Return a consistent error envelope."""
    return {"success": False, "status": "error", "message": message, "data": data}


def validate_output_path(
    output_path: str, *, allowed_extensions: Sequence[str], overwrite: bool
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Validate an output path and overwrite behavior."""
    path = Path(output_path).expanduser()
    allowed = {extension.lower() for extension in allowed_extensions}
    if path.suffix.lower() not in allowed:
        return None, render_error("Unsupported output extension", path=str(path), allowed_extensions=sorted(allowed))
    if not path.parent.exists():
        return None, render_error("Output directory does not exist", path=str(path), parent=str(path.parent))
    if path.exists() and not path.is_file():
        return None, render_error("Output path exists and is not a file", path=str(path))
    if path.exists() and not overwrite:
        return None, render_error("Output file already exists; pass overwrite=true to replace it", path=str(path))
    return path, None


def artifact_info(path: Path) -> Dict[str, Any]:
    """Return generated artifact metadata."""
    exists = path.exists()
    return {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "exists": exists,
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }


def render_settings(runtime: Any) -> Dict[str, Any]:
    """Return common render settings."""
    renderer = current_renderer(runtime)
    return {
        "width": int(getattr(runtime, "renderWidth", 0) or 0),
        "height": int(getattr(runtime, "renderHeight", 0) or 0),
        "frame_start": int(getattr(runtime, "animationRangeStart", getattr(runtime, "frameStart", 0)) or 0),
        "frame_end": int(getattr(runtime, "animationRangeEnd", getattr(runtime, "frameEnd", 0)) or 0),
        "output_path": str(getattr(runtime, "rendOutputFilename", "") or ""),
        "camera": _camera_name(getattr(runtime, "activeCamera", None)),
        "quality_preset": str(getattr(runtime, "renderQualityPreset", "") or ""),
        "renderer": type(renderer).__name__,
    }


def current_renderer(runtime: Any) -> Any:
    """Return the active renderer across native and test runtime contracts."""
    renderers = getattr(runtime, "renderers", None)
    if renderers is not None:
        try:
            renderer = renderers.current
        except Exception:  # noqa: BLE001
            renderer = None
        if renderer is not None:
            return renderer
    return getattr(runtime, "currentRenderer", None)


def scene_render_stats(runtime: Any) -> Dict[str, Any]:
    """Return scene-level render statistics."""
    nodes = iter_scene_nodes(runtime)
    cameras = [node for node in nodes if is_camera_node(node, runtime=runtime)]
    materials = []
    for node in nodes:
        material = getattr(node, "material", None)
        if material is not None and material not in materials:
            materials.append(material)
    settings = render_settings(runtime)
    return {
        "node_count": len(nodes),
        "camera_count": len(cameras),
        "material_count": len(materials),
        "frame_count": max(0, settings["frame_end"] - settings["frame_start"] + 1),
        "resolution": {"width": settings["width"], "height": settings["height"]},
    }


def capture_viewport(runtime: Any, output_path: Path) -> Dict[str, Any]:
    """Capture the viewport through host-provided helpers."""
    for attr in ("captureViewport", "capture_viewport"):
        capture = getattr(runtime, attr, None)
        if callable(capture):
            capture(str(output_path))
            return render_success("Captured viewport", artifact=artifact_info(output_path))
    viewport = getattr(runtime, "viewport", None)
    capture = getattr(viewport, "captureBitmap", None) if viewport is not None else None
    if callable(capture):
        capture(str(output_path))
        return render_success("Captured viewport", artifact=artifact_info(output_path))
    return render_error("No viewport capture operation is available", artifact=artifact_info(output_path))


def create_preview(
    runtime: Any, output_path: Path, *, start_frame: Optional[int], end_frame: Optional[int]
) -> Dict[str, Any]:
    """Create a preview/playblast artifact through host-provided helpers."""
    preview = getattr(runtime, "createPreview", None) or getattr(runtime, "create_preview", None)
    if callable(preview):
        preview(str(output_path), start_frame=start_frame, end_frame=end_frame)
        return render_success("Created preview", artifact=artifact_info(output_path))
    return render_error("No preview generation operation is available", artifact=artifact_info(output_path))


def render_scene(
    runtime: Any,
    output_path: Path,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    camera_name: Optional[str] = None,
    camera_handle: Optional[int] = None,
) -> Dict[str, Any]:
    """Render the current scene to an image file through host-provided helpers."""
    if width is not None and height is not None:
        set_resolution(runtime, width, height)
    if camera_name is not None or camera_handle is not None:
        camera_result = set_camera(runtime, camera_name=camera_name, camera_handle=camera_handle)
        if not camera_result.get("success"):
            return camera_result

    set_render_output(runtime, output_path=str(output_path), save_file=True)
    renderer = (
        getattr(runtime, "render", None)
        or getattr(runtime, "renderScene", None)
        or getattr(runtime, "render_scene", None)
    )
    if not callable(renderer):
        return render_error("No render operation is available", artifact=artifact_info(output_path))

    render_kwargs = {"outputfile": str(output_path), "vfb": False}
    active_camera = getattr(runtime, "activeCamera", None)
    if active_camera is not None:
        render_kwargs["camera"] = active_camera
    try:
        try:
            result = renderer(**render_kwargs)
        except TypeError:
            try:
                result = renderer(str(output_path))
            except TypeError:
                result = renderer()
    except Exception as exc:  # noqa: BLE001
        return render_error(
            "Render failed",
            artifact=artifact_info(output_path),
            exception_type=type(exc).__name__,
            error=str(exc),
        )

    if result is False:
        return render_error("Render did not complete", artifact=artifact_info(output_path))
    if not output_path.exists():
        return render_error("Render did not produce an output file", artifact=artifact_info(output_path))
    return render_success("Rendered scene", artifact=artifact_info(output_path), settings=render_settings(runtime))


def set_render_output(
    runtime: Any, *, output_path: Optional[str] = None, save_file: Optional[bool] = None
) -> Dict[str, Any]:
    """Set common render output options."""
    if output_path is not None:
        runtime.rendOutputFilename = str(Path(output_path).expanduser())
    if save_file is not None:
        runtime.rendSaveFile = bool(save_file)
    return render_success("Updated render output options", settings=render_settings(runtime))


def set_frame_range(runtime: Any, start_frame: int, end_frame: int) -> Dict[str, Any]:
    """Set animation/render frame range."""
    if int(end_frame) < int(start_frame):
        return render_error(
            "end_frame must be greater than or equal to start_frame", start_frame=start_frame, end_frame=end_frame
        )
    runtime.animationRangeStart = int(start_frame)
    runtime.animationRangeEnd = int(end_frame)
    runtime.frameStart = int(start_frame)
    runtime.frameEnd = int(end_frame)
    return render_success("Updated frame range", settings=render_settings(runtime))


def set_resolution(runtime: Any, width: int, height: int) -> Dict[str, Any]:
    """Set render resolution."""
    runtime.renderWidth = int(width)
    runtime.renderHeight = int(height)
    return render_success("Updated render resolution", settings=render_settings(runtime))


def set_camera(
    runtime: Any, *, camera_name: Optional[str] = None, camera_handle: Optional[int] = None
) -> Dict[str, Any]:
    """Set the active render camera."""
    result, camera = resolve_node_object(runtime, node_name=camera_name, handle=camera_handle)
    if camera is None:
        return render_error(result.get("message", "Camera could not be resolved"), resolution=result)
    if not is_camera_node(camera, runtime=runtime):
        return render_error("Resolved node is not a camera", node=node_identity(camera))
    runtime.activeCamera = camera
    viewport = getattr(runtime, "viewport", None)
    if viewport is not None:
        setter = getattr(viewport, "setCamera", None)
        if callable(setter):
            try:
                setter(camera)
            except Exception:  # noqa: BLE001
                pass
        try:
            viewport.camera = camera
        except Exception:  # noqa: BLE001
            pass
    return render_success("Updated render camera", camera=node_identity(camera), settings=render_settings(runtime))


def set_quality_preset(runtime: Any, preset: str) -> Dict[str, Any]:
    """Set a render quality preset."""
    if preset not in QUALITY_PRESETS:
        return render_error("Unsupported quality preset", preset=preset, supported_presets=sorted(QUALITY_PRESETS))
    runtime.renderQualityPreset = preset
    for key, value in QUALITY_PRESETS[preset].items():
        try:
            setattr(runtime, "render_{}".format(key), value)
        except Exception:  # noqa: BLE001
            pass
    return render_success("Updated render quality preset", preset=preset, settings=render_settings(runtime))


def _camera_name(camera: Any) -> Optional[str]:
    if camera is None:
        return None
    return str(getattr(camera, "name", ""))
