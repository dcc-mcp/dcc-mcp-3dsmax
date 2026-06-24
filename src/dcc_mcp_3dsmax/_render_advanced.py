"""Advanced render utilities: HDR, multi-pass/AOV, and renderer configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._render_utils import (
    artifact_info,
    render_error,
    render_settings,
    render_success,
    set_camera,
    set_render_output,
    set_resolution,
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
        set_resolution(runtime, width, height)
    if camera_name is not None or camera_handle is not None:
        camera_result = set_camera(runtime, camera_name=camera_name, camera_handle=camera_handle)
        if not camera_result.get("success"):
            return camera_result
    set_render_output(runtime, output_path=str(output_path), save_file=True)
    fmt_warnings = _configure_hdr_output_format(
        runtime, hdr_format=hdr_format, bit_depth=bit_depth, compression=compression
    )
    renderer = _find_renderer(runtime)
    if not callable(renderer):
        return render_error(
            "No render operation is available",
            artifact=artifact_info(output_path),
            warnings=fmt_warnings,
        )
    try:
        try:
            result = renderer(str(output_path))
        except TypeError:
            result = renderer()
    except Exception as exc:  # noqa: BLE001
        return render_error(
            "HDR render failed",
            artifact=artifact_info(output_path),
            exception_type=type(exc).__name__,
            error=str(exc),
            warnings=fmt_warnings,
        )
    if result is False:
        return render_error(
            "HDR render did not complete",
            artifact=artifact_info(output_path),
            warnings=fmt_warnings,
        )
    if not output_path.exists():
        return render_error(
            "HDR render did not produce an output file",
            artifact=artifact_info(output_path),
            warnings=fmt_warnings,
        )
    return render_success(
        "Rendered HDR output",
        artifact=artifact_info(output_path),
        hdr_format=hdr_format,
        bit_depth=bit_depth,
        settings=render_settings(runtime),
        format_warnings=fmt_warnings,
    )


def _configure_hdr_output_format(
    runtime: Any,
    *,
    hdr_format: str,
    bit_depth: int,
    compression: Optional[str],
) -> list:
    """Attempt to set HDR output format through the host API."""
    warnings = []
    for setter_attr in ("setOutputFormat", "setOutputFileFormat", "SetOutputFormat"):
        setter = getattr(runtime, setter_attr, None)
        if callable(setter):
            try:
                setter(hdr_format)
            except Exception as exc:  # noqa: BLE001
                warnings.append("Could not set output format: {}".format(exc))
            break
    for attr, value in [("outputBitDepth", bit_depth), ("outputCompression", compression)]:
        if value is not None:
            try:
                setattr(runtime, attr, value)
            except Exception:  # noqa: BLE001
                pass
    return warnings


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
        set_resolution(runtime, width, height)
    if camera_name is not None or camera_handle is not None:
        camera_result = set_camera(runtime, camera_name=camera_name, camera_handle=camera_handle)
        if not camera_result.get("success"):
            return camera_result
    set_render_output(runtime, output_path=str(output_path), save_file=True)
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
        try:
            result = renderer(str(output_path))
        except TypeError:
            result = renderer()
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
        runtime.currentRenderer = renderer_instance
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
        "arnold": ("ArnoldRenderer",),
        "vray": ("VRayRenderer",),
        "art": ("ARTRenderer",),
    }
    return mapping.get(renderer_type, ())


def configure_renderer(runtime: Any, *, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply generic renderer parameter overrides."""
    applied = 0
    warnings = []
    renderer = getattr(runtime, "currentRenderer", None)
    if renderer is None:
        return render_error("No active renderer to configure")
    for key, value in settings.items():
        try:
            setattr(renderer, key, value)
            applied += 1
        except Exception as exc:  # noqa: BLE001
            warnings.append("Could not set {}: {}".format(key, exc))
    return render_success(
        "Configured renderer",
        renderer_type=type(renderer).__name__,
        settings_applied=applied,
        settings_requested=len(settings),
        warnings=warnings,
    )


def _find_renderer(runtime: Any) -> Any:
    return (
        getattr(runtime, "render", None)
        or getattr(runtime, "renderScene", None)
        or getattr(runtime, "render_scene", None)
    )
