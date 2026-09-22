"""Advanced render utilities: HDR, multi-pass/AOV, and renderer configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._render_utils import (
    apply_runtime_setting,
    artifact_info,
    current_renderer,
    render_error,
    render_settings,
    render_success,
    set_camera,
    set_render_output,
    set_resolution,
    setting_matches,
    summarize_setting_results,
)

# ---------------------------------------------------------------------------
# HDR render helpers
# ---------------------------------------------------------------------------

HDR_IMAGE_EXTENSIONS = {".exr", ".hdr", ".tif", ".tiff"}
HDR_FORMATS = {"exr": "exr", "hdr": "hdr", "tif": "tif"}


def render_hdr_scene(
    runtime: Any,
    output_path: Path,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    camera_name: Optional[str] = None,
    camera_handle: Optional[int] = None,
    hdr_format: str = "exr",
    bit_depth: int = 16,
    compression: Optional[str] = None,
) -> Dict[str, Any]:
    """Render to an HDR/EXR image with high dynamic range output settings."""
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
    fmt_result = configure_hdr_output_format(
        runtime, hdr_format=hdr_format, bit_depth=bit_depth, compression=compression
    )
    if fmt_result["errors"]:
        return render_error(
            "Could not configure the HDR output format",
            artifact=artifact_info(output_path),
            format_settings=fmt_result,
            format_warnings=fmt_result["warnings"],
        )
    renderer = _find_renderer(runtime)
    if not callable(renderer):
        return render_error(
            "No render operation is available",
            artifact=artifact_info(output_path),
            format_warnings=fmt_result["warnings"],
        )
    try:
        result = _render_to_output(runtime, renderer, output_path)
    except Exception as exc:  # noqa: BLE001
        return render_error(
            "HDR render failed",
            artifact=artifact_info(output_path),
            exception_type=type(exc).__name__,
            error=str(exc),
            format_warnings=fmt_result["warnings"],
        )
    if result is False:
        return render_error(
            "HDR render did not complete",
            artifact=artifact_info(output_path),
            format_warnings=fmt_result["warnings"],
        )
    if not output_path.exists():
        return render_error(
            "HDR render did not produce an output file",
            artifact=artifact_info(output_path),
            format_warnings=fmt_result["warnings"],
        )
    return render_success(
        "Rendered HDR output",
        artifact=artifact_info(output_path),
        hdr_format=hdr_format,
        bit_depth=bit_depth,
        settings=render_settings(runtime),
        format_settings=fmt_result,
        format_warnings=fmt_result["warnings"],
    )


def configure_hdr_output_format(
    runtime: Any,
    *,
    hdr_format: str,
    bit_depth: Optional[int],
    compression: Optional[str],
) -> Dict[str, Any]:
    """Set HDR output format options and report what the host accepted.

    A bit depth the host silently ignores would make the render look like HDR
    output while it is not, so every knob is verified and reported instead of
    being assumed.
    """
    warnings: List[str] = []
    setter_found = False
    for setter_attr in ("setOutputFormat", "setOutputFileFormat", "SetOutputFormat"):
        setter = getattr(runtime, setter_attr, None)
        if callable(setter):
            setter_found = True
            try:
                setter(hdr_format)
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not set output format: {}".format(exc))
            break
    if not setter_found:
        warnings.append(
            "The host exposes no output format setter; the output extension decides the format"
        )
    results = []
    if bit_depth is not None:
        results.append(apply_runtime_setting(runtime, "outputBitDepth", int(bit_depth), label="bit_depth"))
    if compression is not None:
        results.append(apply_runtime_setting(runtime, "outputCompression", compression, label="compression"))
    summary = summarize_setting_results(results)
    summary["warnings"] = warnings + summary["warnings"]
    summary["format"] = hdr_format
    return summary


# ---------------------------------------------------------------------------
# Multi-pass / AOV render helpers
# ---------------------------------------------------------------------------

COMMON_RENDER_ELEMENTS = {
    "diffuse": "Diffuse",
    "specular": "Specular",
    "reflection": "Reflection",
    "refraction": "Refraction",
    "shadow": "Shadow",
    "ambient_occlusion": "AO",
    "normal": "Normal",
    "z_depth": "ZDepth",
    "alpha": "Alpha",
    "global_illumination": "GI",
    "lighting": "Lighting",
    "self_illumination": "SelfIllumination",
    "background": "Background",
}


def render_multi_pass(
    runtime: Any,
    output_path: Path,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    camera_name: Optional[str] = None,
    camera_handle: Optional[int] = None,
    elements: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Render the scene with multiple render elements (AOVs)."""
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
    resolved_elements = _resolve_elements(elements)
    active_count, element_warnings = _enable_render_elements(runtime, resolved_elements)
    renderer = _find_renderer(runtime)
    if not callable(renderer):
        return render_error(
            "No render operation is available",
            artifact=artifact_info(output_path),
            element_warnings=element_warnings,
        )
    try:
        result = _render_to_output(runtime, renderer, output_path)
    except Exception as exc:  # noqa: BLE001
        return render_error(
            "Multi-pass render failed",
            artifact=artifact_info(output_path),
            exception_type=type(exc).__name__,
            error=str(exc),
            element_warnings=element_warnings,
        )
    if result is False:
        return render_error(
            "Multi-pass render did not complete",
            artifact=artifact_info(output_path),
            element_warnings=element_warnings,
        )
    if not output_path.exists():
        return render_error(
            "Multi-pass render did not produce an output file",
            artifact=artifact_info(output_path),
            element_warnings=element_warnings,
        )
    return render_success(
        "Rendered multi-pass output",
        artifact=artifact_info(output_path),
        elements_requested=sorted(resolved_elements.keys()),
        elements_enabled=active_count,
        element_warnings=element_warnings,
        settings=render_settings(runtime),
    )


def _resolve_elements(requested: Optional[Sequence[str]]) -> Dict[str, str]:
    if not requested:
        return dict(COMMON_RENDER_ELEMENTS)
    resolved = {}
    for name in requested:
        key = name.lower().replace(" ", "_").replace("-", "_")
        label = COMMON_RENDER_ELEMENTS.get(key)
        if label:
            resolved[key] = label
        else:
            resolved[name] = name
    return resolved


def _enable_render_elements(runtime: Any, elements: Dict[str, str]) -> Tuple[int, list]:
    """Try to enable render elements on the host."""
    active = 0
    warnings = []
    re_mgr = _render_element_manager(runtime)
    if re_mgr is None:
        return 0, ["Render element manager is not available on this host"]
    for slug, label in elements.items():
        try:
            re_mgr.enable_element(label)
            active += 1
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not enable element {}: {}".format(slug, exc))
    return active, warnings


def _render_element_manager(runtime: Any) -> Any:
    for attr in ("renderElementManager", "RenderElementManager", "renderElements"):
        try:
            mgr = getattr(runtime, attr, None)
            if mgr is not None:
                return mgr
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------------------
# Renderer configuration
# ---------------------------------------------------------------------------

RENDERER_TYPES = {"scanline", "arnold", "vray", "art"}


def set_renderer(runtime: Any, renderer_type: str) -> Dict[str, Any]:
    """Switch the active renderer to the specified type."""
    if renderer_type not in RENDERER_TYPES:
        return render_error(
            "Unsupported renderer type",
            renderer_type=renderer_type,
            supported_renderers=sorted(RENDERER_TYPES),
        )
    renderer_class = _find_renderer_class(runtime, renderer_type)
    if renderer_class is None:
        return render_error("Renderer class not found", renderer_type=renderer_type)
    try:
        renderer_instance = renderer_class()
        _set_current_renderer(runtime, renderer_instance)
    except Exception as exc:  # noqa: BLE001
        return render_error(
            "Could not activate renderer",
            renderer_type=renderer_type,
            exception_type=type(exc).__name__,
            error=str(exc),
        )
    return render_success(
        "Activated renderer",
        renderer_type=renderer_type,
        settings=render_settings(runtime),
    )


def _find_renderer_class(runtime: Any, renderer_type: str) -> Any:
    names = _renderer_class_names(renderer_type)
    for name in names:
        try:
            cls = getattr(runtime, name, None)
            if cls is not None:
                return cls
        except Exception:  # noqa: BLE001
            continue
    return None


def _renderer_class_names(renderer_type: str) -> tuple:
    mapping = {
        "scanline": ("DefaultScanlineRenderer", "ScanlineRenderer"),
        "arnold": ("Arnold", "ArnoldRenderer"),
        "vray": ("VRayRenderer",),
        "art": ("ARTRenderer",),
    }
    return mapping.get(renderer_type, ())


def configure_renderer(runtime: Any, *, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply generic renderer parameter overrides and verify each by readback."""
    renderer = current_renderer(runtime)
    if renderer is None:
        return render_error("No active renderer to configure")
    applied: List[str] = []
    verified: List[str] = []
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    # Snapshot before the first write so a rejected setting cannot leave the
    # batch half-applied: the renderer is restored to its previous state.
    previous: Dict[str, Any] = {}
    for key in settings:
        if _read_setting(renderer, key) is not None:
            previous[key] = _read_setting(renderer, key)
    for key, value in settings.items():
        try:
            setattr(renderer, key, value)
        except Exception as exc:  # noqa: BLE001 - an explicit host rejection is a failure.
            errors.append({"setting": key, "requested": value, "error": str(exc)})
            continue
        applied.append(key)
        readback = _read_setting(renderer, key)
        if readback is None:
            warnings.append("Could not read back {} to verify the value".format(key))
            continue
        if not setting_matches(readback, value):
            errors.append({"setting": key, "requested": value, "actual": readback})
            continue
        verified.append(key)
    data = {
        "renderer_type": type(renderer).__name__,
        "settings_applied": applied,
        "settings_verified": verified,
        "settings_requested": list(settings),
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        data["rollback"] = _restore_settings(renderer, previous)
        return render_error("Could not apply every renderer setting", **data)
    return render_success("Configured renderer", **data)


def _restore_settings(renderer: Any, previous: Mapping[str, Any]) -> Dict[str, Any]:
    """Restore renderer settings captured before a failed batch."""
    restored: List[str] = []
    failed: List[Dict[str, Any]] = []
    for key, value in previous.items():
        try:
            setattr(renderer, key, value)
        except Exception as exc:  # noqa: BLE001 - report, never mask, a failed restore.
            failed.append({"setting": key, "error": str(exc)})
            continue
        restored.append(key)
    return {"rolled_back": not failed, "restored": restored, "failed": failed}


def _read_setting(renderer: Any, key: str) -> Any:
    try:
        return getattr(renderer, key)
    except Exception:  # noqa: BLE001 - unverifiable settings are reported as warnings.
        return None


def _set_current_renderer(runtime: Any, renderer: Any) -> None:
    """Activate a renderer using the native pymxs contract when available."""
    renderers = getattr(runtime, "renderers", None)
    if renderers is not None:
        try:
            renderers.current = renderer
            return
        except Exception:  # noqa: BLE001
            pass
    runtime.currentRenderer = renderer


def _find_renderer(runtime: Any) -> Any:
    return (
        getattr(runtime, "render", None)
        or getattr(runtime, "renderScene", None)
        or getattr(runtime, "render_scene", None)
    )


def _render_to_output(runtime: Any, renderer: Any, output_path: Path) -> Any:
    """Invoke pymxs render with native output keywords before legacy fallbacks."""
    kwargs = {"outputfile": str(output_path), "vfb": False}
    active_camera = getattr(runtime, "activeCamera", None)
    if active_camera is not None:
        kwargs["camera"] = active_camera
    try:
        return renderer(**kwargs)
    except TypeError:
        try:
            return renderer(str(output_path))
        except TypeError:
            return renderer()
