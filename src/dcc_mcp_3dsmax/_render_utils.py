"""Helpers for 3ds Max render and viewport skill scripts."""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._scene_utils import is_camera_node, iter_scene_nodes, node_identity, resolve_node_object

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
PREVIEW_EXTENSIONS = {".avi", ".mp4", ".mov"}

# Outcome of a single render-setting write. A write is only ``applied`` when the
# host reads the requested value back; ``unverified`` means the host accepted
# the call but the value cannot be read back, and ``rejected`` means the host
# refused the write or kept a different value.
SETTING_APPLIED = "applied"
SETTING_UNVERIFIED = "unverified"
SETTING_REJECTED = "rejected"

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
    from dcc_mcp_3dsmax._lookdev_utils import get_display_view_transform

    try:
        import pymxs

        byref = getattr(pymxs, "byref", None)
    except ImportError:
        byref = None
    display_view = get_display_view_transform(runtime, byref=byref)
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
        "display_view": display_view,
        "view_transform": display_view["view_transform"],
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
    """Capture the viewport through host-provided helpers.

    The capture is written to a temporary file in the destination directory and
    atomically renamed into place only after it is confirmed non-empty, so a
    success response always means the target file exists and is fully flushed.
    """
    capture = _viewport_capture(runtime)
    if not callable(capture):
        return render_error("No viewport capture operation is available", artifact=artifact_info(output_path))

    temp_path = _atomic_capture_target(output_path)
    try:
        capture(str(temp_path))
        if not temp_path.exists() or not temp_path.is_file():
            return render_error(
                "Viewport capture did not produce a file", artifact=artifact_info(output_path)
            )
        if temp_path.stat().st_size <= 0:
            return render_error(
                "Viewport capture produced an empty file",
                artifact=artifact_info(output_path),
                size_bytes=0,
            )
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    return render_success("Captured viewport", artifact=artifact_info(output_path))


def _viewport_capture(runtime: Any) -> Any:
    """Return the first available viewport capture callable on the runtime."""
    for attr in ("captureViewport", "capture_viewport"):
        capture = getattr(runtime, attr, None)
        if callable(capture):
            return capture
    viewport = getattr(runtime, "viewport", None)
    capture = getattr(viewport, "captureBitmap", None) if viewport is not None else None
    if callable(capture):
        return capture
    return None


def _atomic_capture_target(output_path: Path) -> Path:
    """Create a sibling temporary file for an atomic capture-then-rename."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=output_path.name + ".",
        suffix=output_path.suffix,
        dir=str(output_path.parent),
    )
    os.close(fd)
    return Path(temp_name)


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
        resolution_result = set_resolution(runtime, width, height)
        if not resolution_result.get("success"):
            return resolution_result
    if camera_name is not None or camera_handle is not None:
        camera_result = set_camera(runtime, camera_name=camera_name, camera_handle=camera_handle)
        if not camera_result.get("success"):
            return camera_result

    output_result = set_render_output(runtime, output_path=str(output_path), save_file=True)
    if not output_result.get("success"):
        return output_result
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


def setting_matches(readback: Any, value: Any) -> bool:
    """Report whether a read-back value equals the requested one."""
    if isinstance(value, bool) or isinstance(readback, bool):
        return bool(readback) == bool(value)
    if isinstance(value, (int, float)):
        try:
            return math.isclose(float(readback), float(value), rel_tol=1e-6, abs_tol=1e-6)
        except (TypeError, ValueError):
            return False
    return str(readback) == str(value)


def _read_runtime_setting(runtime: Any, attribute: str) -> Any:
    """Read one runtime setting, reporting ``None`` when it cannot be read."""
    try:
        return getattr(runtime, attribute)
    except Exception:  # noqa: BLE001 - an unreadable setting is reported, never assumed.
        return None


def _setting_row(
    name: str,
    attribute: str,
    status: str,
    requested: Any,
    actual: Any,
    message: Optional[str],
    *,
    optional: bool,
) -> Dict[str, Any]:
    """Build one per-setting result row.

    ``optional`` marks a value the caller did not ask for (a preset-derived
    knob, for example). A host that refuses it is reported as unverified
    instead of failing the whole call, but it is still reported.
    """
    if status == SETTING_REJECTED and optional:
        status = SETTING_UNVERIFIED
    row: Dict[str, Any] = {
        "setting": name,
        "attribute": attribute,
        "status": status,
        "requested": requested,
        "actual": actual,
        "error": message if status == SETTING_REJECTED else None,
        "warning": message if status == SETTING_UNVERIFIED else None,
    }
    return row


def apply_runtime_setting(
    runtime: Any,
    attribute: str,
    value: Any,
    *,
    label: Optional[str] = None,
    optional: bool = False,
    compare: Optional[Callable[[Any, Any], bool]] = None,
) -> Dict[str, Any]:
    """Write one runtime render setting and verify the host kept it.

    ``pymxs`` wrappers accept unknown properties without persisting them, so a
    bare ``setattr`` is not evidence that anything changed. Every write is read
    back and classified as applied, unverified, or rejected. ``compare``
    replaces the default equality test for values that need one (paths, node
    wrappers); it receives ``(readback, requested)``.
    """
    name = label or attribute
    try:
        setattr(runtime, attribute, value)
    except Exception as exc:  # noqa: BLE001 - an explicit host rejection is a failure.
        return _setting_row(
            name,
            attribute,
            SETTING_REJECTED,
            value,
            None,
            "Could not set {}: {}".format(attribute, exc),
            optional=optional,
        )
    readback = _read_runtime_setting(runtime, attribute)
    if readback is None:
        return _setting_row(
            name,
            attribute,
            SETTING_UNVERIFIED,
            value,
            None,
            "Could not read back {} to verify the value".format(attribute),
            optional=optional,
        )
    if not (compare(readback, value) if compare is not None else setting_matches(readback, value)):
        return _setting_row(
            name,
            attribute,
            SETTING_REJECTED,
            value,
            readback,
            "{} read back {!r} after writing {!r}".format(attribute, readback, value),
            optional=optional,
        )
    return _setting_row(name, attribute, SETTING_APPLIED, value, readback, None, optional=optional)


def summarize_setting_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Collapse per-setting write results into applied / errors / warnings."""
    warnings = [row["warning"] for row in results if row["status"] == SETTING_UNVERIFIED and row.get("warning")]
    return {
        "applied": [row["setting"] for row in results if row["status"] == SETTING_APPLIED],
        "applied_count": sum(1 for row in results if row["status"] == SETTING_APPLIED),
        "unverified": [row["setting"] for row in results if row["status"] == SETTING_UNVERIFIED],
        "errors": [row for row in results if row["status"] == SETTING_REJECTED],
        "warnings": warnings,
        "setting_results": [dict(row) for row in results],
    }


# Public alias: viewport/IPR helpers capture into a sibling temp file and
# rename it into place so a success always means a flushed, non-empty file.
atomic_capture_target = _atomic_capture_target


def _same_node(candidate: Any, node: Any) -> bool:
    """Compare two node wrappers by identity, handle, or name."""
    if candidate is node:
        return True
    for attribute in ("handle", "name"):
        expected = getattr(node, attribute, None)
        actual = getattr(candidate, attribute, None)
        if expected is None or actual is None:
            continue
        if attribute == "handle":
            try:
                return int(actual) == int(expected)
            except (TypeError, ValueError):
                continue
        return str(actual) == str(expected)
    return False


def _same_path(readback: Any, value: Any) -> bool:
    """Compare two output paths the way the host stores them."""
    return setting_matches(_normalize_path(readback), _normalize_path(value))


def _normalize_path(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(Path(str(value)).expanduser())))


def set_render_output(
    runtime: Any, *, output_path: Optional[str] = None, save_file: Optional[bool] = None
) -> Dict[str, Any]:
    """Set common render output options."""
    results = []
    if output_path is not None:
        results.append(
            apply_runtime_setting(
                runtime,
                "rendOutputFilename",
                str(Path(output_path).expanduser()),
                label="output_path",
                compare=_same_path,
            )
        )
    if save_file is not None:
        results.append(apply_runtime_setting(runtime, "rendSaveFile", bool(save_file), label="save_file"))
    summary = summarize_setting_results(results)
    data = dict(summary)
    data["settings"] = render_settings(runtime)
    if summary["errors"]:
        return render_error("Could not update every render output option", **data)
    return render_success("Updated render output options", **data)


def set_frame_range(runtime: Any, start_frame: int, end_frame: int) -> Dict[str, Any]:
    """Set animation/render frame range."""
    if int(end_frame) < int(start_frame):
        return render_error(
            "end_frame must be greater than or equal to start_frame", start_frame=start_frame, end_frame=end_frame
        )
    results = [
        apply_runtime_setting(runtime, "animationRangeStart", int(start_frame), label="start_frame"),
        apply_runtime_setting(runtime, "animationRangeEnd", int(end_frame), label="end_frame"),
        apply_runtime_setting(runtime, "frameStart", int(start_frame), label="render_start_frame"),
        apply_runtime_setting(runtime, "frameEnd", int(end_frame), label="render_end_frame"),
    ]
    summary = summarize_setting_results(results)
    data = dict(summary)
    data["settings"] = render_settings(runtime)
    if summary["errors"]:
        return render_error("Could not update the frame range", start_frame=start_frame, end_frame=end_frame, **data)
    return render_success("Updated frame range", **data)


def set_resolution(runtime: Any, width: int, height: int) -> Dict[str, Any]:
    """Set render resolution."""
    results = [
        apply_runtime_setting(runtime, "renderWidth", int(width), label="width"),
        apply_runtime_setting(runtime, "renderHeight", int(height), label="height"),
    ]
    summary = summarize_setting_results(results)
    data = dict(summary)
    data["settings"] = render_settings(runtime)
    if summary["errors"]:
        return render_error("Could not update the render resolution", width=width, height=height, **data)
    return render_success("Updated render resolution", **data)


def set_camera(
    runtime: Any, *, camera_name: Optional[str] = None, camera_handle: Optional[int] = None
) -> Dict[str, Any]:
    """Set the active render camera."""
    result, camera = resolve_node_object(runtime, node_name=camera_name, handle=camera_handle)
    if camera is None:
        return render_error(result.get("message", "Camera could not be resolved"), resolution=result)
    if not is_camera_node(camera, runtime=runtime):
        return render_error("Resolved node is not a camera", node=node_identity(camera))
    # A camera is a wrapper, not a scalar: compare by identity, handle, or name.
    results = [apply_runtime_setting(runtime, "activeCamera", camera, label="camera", compare=_same_node)]
    # ``summarize_setting_results`` already reports the setting warnings; this
    # list only carries failures from the viewport follow-up below.
    warnings = []
    viewport = getattr(runtime, "viewport", None)
    if viewport is not None:
        setter = getattr(viewport, "setCamera", None)
        if callable(setter):
            try:
                setter(camera)
            except Exception as exc:  # noqa: BLE001 - report, never hide, a viewport failure.
                warnings.append("Could not update the viewport camera: {}".format(exc))
        try:
            viewport.camera = camera
        except Exception as exc:  # noqa: BLE001 - report, never hide, a viewport failure.
            warnings.append("Could not assign the viewport camera: {}".format(exc))
    summary = summarize_setting_results(results)
    data = dict(summary)
    data["warnings"] = summary["warnings"] + warnings
    data["camera"] = node_identity(camera)
    data["settings"] = render_settings(runtime)
    if summary["errors"]:
        return render_error("Could not update the render camera", **data)
    return render_success("Updated render camera", **data)


def set_quality_preset(runtime: Any, preset: str) -> Dict[str, Any]:
    """Set a render quality preset."""
    if preset not in QUALITY_PRESETS:
        return render_error("Unsupported quality preset", preset=preset, supported_presets=sorted(QUALITY_PRESETS))
    results = [apply_runtime_setting(runtime, "renderQualityPreset", preset, label="preset")]
    for key, value in QUALITY_PRESETS[preset].items():
        # The preset name is what the caller asked for; the sampling and
        # antialiasing knobs behind it are best effort and only reported.
        results.append(apply_runtime_setting(runtime, "render_{}".format(key), value, label=key, optional=True))
    summary = summarize_setting_results(results)
    data = dict(summary)
    data["preset"] = preset
    data["settings"] = render_settings(runtime)
    if summary["errors"]:
        return render_error("Could not apply the render quality preset", **data)
    return render_success("Updated render quality preset", **data)


def _camera_name(camera: Any) -> Optional[str]:
    if camera is None:
        return None
    return str(getattr(camera, "name", ""))
