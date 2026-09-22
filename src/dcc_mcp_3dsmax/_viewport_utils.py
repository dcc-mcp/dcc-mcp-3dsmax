"""Viewport, frame buffer, and interactive preview utilities for 3ds Max.

Every helper here reports what the host actually did. A capture that cannot be
cropped to the region the caller asked for, a viewport option the host refuses,
a view switch that did not take effect, or a preview state that cannot be read
back is reported as a failure or an explicit warning row -- never as a success
with the failure quietly dropped.

The host surface is reached through a small set of named contracts (documented
per helper) that are probed in order, so the whole module is exercisable
without a 3ds Max host.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._camera_light_utils import attr_present
from dcc_mcp_3dsmax._render_utils import (
    SETTING_APPLIED,
    SETTING_REJECTED,
    SETTING_UNVERIFIED,
    artifact_info,
    atomic_capture_target,
    current_renderer,
    render_error,
    render_success,
)
from dcc_mcp_3dsmax._scene_utils import resolve_node_object

# ---------------------------------------------------------------------------
# Frame buffer regions
# ---------------------------------------------------------------------------

FRAME_BUFFER_SOURCES = ("auto", "screen", "max", "vray", "corona", "fstorm")

# One entry per frame buffer the adapter knows how to locate. ``renderer_names``
# is matched against the class name of the active renderer so ``source=auto``
# can pick the frame buffer the host is actually using.
FRAME_BUFFER_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "max": {
        "label": "3ds Max render frame window",
        "renderer_names": ("defaultscanline", "scanline", "art", "iray", "quicksilver"),
        "rect_attrs": ("frameBufferRect", "vfbRect", "vfb_rect", "renderFrameRect"),
        "rect_callables": ("getFrameBufferRect", "getVfbRect", "vfbRect", "frameBufferRect"),
        "open_callables": ("showFrameBuffer", "openFrameBuffer", "showVFB", "displayFrameBuffer"),
        "open_attrs": ("showVFB", "rendShowVFB", "showFrameBuffer"),
    },
    "vray": {
        "label": "V-Ray frame buffer",
        "renderer_names": ("vray",),
        "rect_attrs": ("vfbRect", "vrayVfbRect", "vrayVFB_rect"),
        "rect_callables": ("getVfbRect", "vrayVfbGetRect", "vrayVFBGetRect"),
        "open_callables": ("showVFB", "openVFB", "vrayShowVFB"),
        "open_attrs": ("showVFB", "vfbOn"),
    },
    "corona": {
        "label": "Corona frame buffer",
        "renderer_names": ("corona",),
        "rect_attrs": ("vfbRect", "coronaVfbRect"),
        "rect_callables": ("getVfbRect", "coronaVfbGetRect"),
        "open_callables": ("showVFB", "openVFB", "coronaShowVFB"),
        "open_attrs": ("showVFB", "vfbShow"),
    },
    "fstorm": {
        "label": "FStorm frame buffer",
        "renderer_names": ("fstorm",),
        "rect_attrs": ("vfbRect", "fstormVfbRect"),
        "rect_callables": ("getVfbRect", "fstormVfbGetRect"),
        "open_callables": ("showVFB", "openVFB", "fstormShowVFB"),
        "open_attrs": ("showVFB",),
    },
}

SCREEN_CAPTURE_CALLABLES = ("captureScreen", "capture_screen", "captureDesktop", "capture_desktop")


def frame_buffer_target(runtime: Any, source: str = "auto") -> Dict[str, Any]:
    """Resolve which frame buffer to capture and where it sits on screen.

    Returns ``source``, ``provider``, ``rect`` (normalized or ``None``), plus
    the ``probed`` contracts and every ``warning`` raised while probing.
    """
    target: Dict[str, Any] = {
        "source": source,
        "provider": None,
        "label": None,
        "rect": None,
        "probed": [],
        "warnings": [],
        "errors": [],
    }
    if source not in FRAME_BUFFER_SOURCES:
        target["errors"].append(
            "Unsupported frame buffer source {!r}; supported: {}".format(source, ", ".join(FRAME_BUFFER_SOURCES))
        )
        return target
    if source == "screen":
        target["label"] = "3ds Max desktop"
        return target

    key, provider, error = resolve_frame_buffer_source(runtime, source)
    if error is not None:
        target["errors"].append(error)
        return target
    target["provider"] = key
    target["label"] = provider["label"]
    rect, probed, warnings = probe_frame_buffer_rect(runtime, provider)
    target["rect"] = rect
    target["probed"] = probed
    target["warnings"] = warnings
    return target


def resolve_frame_buffer_source(runtime: Any, source: str) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    """Return ``(provider_key, provider, error)`` for a requested source."""
    if source != "auto":
        return source, FRAME_BUFFER_PROVIDERS[source], None
    renderer = current_renderer(runtime)
    renderer_name = type(renderer).__name__.lower() if renderer is not None else ""
    for key in ("vray", "corona", "fstorm", "max"):
        provider = FRAME_BUFFER_PROVIDERS[key]
        for alias in provider["renderer_names"]:
            if alias and alias in renderer_name:
                return key, provider, None
    return "max", FRAME_BUFFER_PROVIDERS["max"], None


def probe_frame_buffer_rect(
    runtime: Any, provider: Mapping[str, Any]
) -> Tuple[Optional[Dict[str, int]], List[str], List[str]]:
    """Probe the host for the on-screen rectangle of one frame buffer."""
    probed: List[str] = []
    warnings: List[str] = []
    renderer = current_renderer(runtime)
    targets: List[Any] = [renderer] if renderer is not None else []
    targets.append(runtime)
    for target in targets:
        owner = "renderer" if target is renderer else "runtime"
        for attribute in provider["rect_attrs"]:
            if not attr_present(runtime, target, attribute):
                continue
            probed.append("{}.{}".format(owner, attribute))
            value = _safe_getattr(target, attribute)
            if value is None:
                continue
            rect, reason = coerce_rect(value, "{}.{}".format(owner, attribute))
            if rect is not None:
                return rect, probed, warnings
            warnings.append(reason or "{} did not report a usable region".format(attribute))
        for name in provider["rect_callables"]:
            func = _safe_getattr(target, name)
            if not callable(func):
                continue
            probed.append("{}.{}()".format(owner, name))
            try:
                value = func()
            except Exception as exc:  # noqa: BLE001 - a probe failure is reported, not assumed.
                warnings.append("{}() failed: {}".format(name, exc))
                continue
            if value is None:
                continue
            rect, reason = coerce_rect(value, "{}()".format(name))
            if rect is not None:
                return rect, probed, warnings
            warnings.append(reason or "{}() did not report a usable region".format(name))
    return None, probed, warnings


def coerce_rect(value: Any, contract: str) -> Tuple[Optional[Dict[str, int]], Optional[str]]:
    """Normalize a host rectangle into ``{left, top, right, bottom, w, h}``."""
    if value is None:
        return None, "{} reported no region".format(contract)
    if isinstance(value, Mapping):
        left = _first_number(value, ("left", "x"))
        top = _first_number(value, ("top", "y"))
        right = _first_number(value, ("right",))
        bottom = _first_number(value, ("bottom",))
        width = _first_number(value, ("width", "w"))
        height = _first_number(value, ("height", "h"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 4:
        numbers = [_as_number(item) for item in value]
        if any(number is None for number in numbers):
            return None, "{} reported a non-numeric region: {!r}".format(contract, value)
        left, top, right, bottom = numbers
        width = right - left
        height = bottom - top
    else:
        left = _first_attr_number(value, ("left", "x"))
        top = _first_attr_number(value, ("top", "y"))
        right = _first_attr_number(value, ("right",))
        bottom = _first_attr_number(value, ("bottom",))
        width = _first_attr_number(value, ("width", "w"))
        height = _first_attr_number(value, ("height", "h"))
    if width is None and right is not None and left is not None:
        width = right - left
    if height is None and bottom is not None and top is not None:
        height = bottom - top
    if right is None and width is not None and left is not None:
        right = left + width
    if bottom is None and height is not None and top is not None:
        bottom = top + height
    if None in (left, top, right, bottom, width, height):
        return None, "{} reported an incomplete region: {!r}".format(contract, value)
    if width <= 0 or height <= 0:
        return None, "{} reported an empty region: {!r}".format(contract, value)
    rect = {
        "left": int(left),
        "top": int(top),
        "right": int(right),
        "bottom": int(bottom),
        "width": int(width),
        "height": int(height),
    }
    return rect, None


def capture_screen(
    runtime: Any,
    output_path: Path,
    *,
    source: str = "auto",
    crop: bool = True,
    open_frame_buffer: bool = True,
) -> Dict[str, Any]:
    """Capture the desktop, optionally cropped to a renderer frame buffer.

    A requested crop the host cannot honour is a failure: capturing the whole
    desktop instead would look like a frame buffer capture while it is not.
    """
    path = Path(str(output_path)).expanduser()
    target = frame_buffer_target(runtime, source)
    warnings: List[str] = list(target["warnings"])
    if target["errors"]:
        return render_error("; ".join(target["errors"]), source=source, probed=target["probed"], warnings=warnings)
    rect = target["rect"]
    if source != "screen":
        if rect is None:
            message = "Could not resolve the {} region on this host".format(target["label"] or "frame buffer")
            if crop:
                return render_error(
                    message + "; pass source='screen' to capture the full desktop",
                    source=source,
                    provider=target["provider"],
                    probed=target["probed"],
                    warnings=warnings,
                )
            warnings.append(message + "; capturing the full desktop instead")
        elif open_frame_buffer:
            warnings.extend(_open_frame_buffer(runtime, target["provider"]))

    capture = _first_callable(runtime, SCREEN_CAPTURE_CALLABLES)
    if capture is None:
        return render_error(
            "No screen capture operation is available on this host",
            probed=list(SCREEN_CAPTURE_CALLABLES),
            warnings=warnings,
        )

    requested_rect = rect if (crop and rect is not None) else None
    temp_path = atomic_capture_target(path)
    try:
        result, capture_error = _invoke_screen_capture(capture, temp_path, requested_rect)
        if capture_error is not None:
            return render_error(
                capture_error,
                artifact=artifact_info(path),
                source=source,
                provider=target["provider"],
                requested_rect=requested_rect,
                warnings=warnings,
            )
        if not temp_path.is_file():
            return render_error(
                "Screen capture did not produce a file",
                artifact=artifact_info(path),
                source=source,
                warnings=warnings,
            )
        if temp_path.stat().st_size <= 0:
            return render_error(
                "Screen capture produced an empty file",
                artifact=artifact_info(path),
                source=source,
                warnings=warnings,
            )
        reported = reported_rect(result)
        if requested_rect is not None and reported is not None and not rects_match(requested_rect, reported):
            return render_error(
                "Captured region differs from the requested frame buffer region",
                artifact=artifact_info(path),
                source=source,
                provider=target["provider"],
                requested_rect=requested_rect,
                captured_rect=reported,
                warnings=warnings,
            )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    return render_success(
        "Captured {}".format(target["label"] or "the 3ds Max desktop"),
        artifact=artifact_info(path),
        source=source,
        provider=target["provider"],
        rect=requested_rect,
        cropped=requested_rect is not None,
        captured_rect=reported,
        probed=target["probed"],
        warnings=warnings,
    )


def _invoke_screen_capture(
    capture: Callable[..., Any], path: Path, rect: Optional[Dict[str, int]]
) -> Tuple[Any, Optional[str]]:
    """Call the host screen capture, honouring the requested region or failing."""
    if rect is None:
        try:
            return capture(str(path)), None
        except Exception as exc:  # noqa: BLE001 - a host failure is reported.
            return None, "Screen capture failed: {}".format(exc)
    try:
        return capture(str(path), rect=rect), None
    except TypeError:
        pass
    except Exception as exc:  # noqa: BLE001 - a host failure is reported.
        return None, "Screen capture failed: {}".format(exc)
    for args in ((str(path), rect), (rect, str(path))):
        try:
            return capture(*args), None
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001 - a host failure is reported.
            return None, "Screen capture failed: {}".format(exc)
    return None, (
        "The host screen capture accepts no region, so the capture cannot be cropped to the "
        "frame buffer; pass crop=false to capture the full desktop"
    )


def _open_frame_buffer(runtime: Any, provider_key: Optional[str]) -> List[str]:
    """Bring a frame buffer window to the front so it can be captured."""
    warnings: List[str] = []
    if provider_key is None:
        return warnings
    provider = FRAME_BUFFER_PROVIDERS[provider_key]
    for name in provider["open_callables"]:
        func = _safe_getattr(runtime, name)
        if callable(func):
            try:
                func()
            except Exception as exc:  # noqa: BLE001 - reported, never silently skipped.
                warnings.append("Could not open the frame buffer with {}: {}".format(name, exc))
            return warnings
    for attribute in provider["open_attrs"]:
        if not attr_present(runtime, runtime, attribute):
            continue
        try:
            setattr(runtime, attribute, True)
        except Exception as exc:  # noqa: BLE001 - reported, never silently skipped.
            warnings.append("Could not open the frame buffer with {}: {}".format(attribute, exc))
        return warnings
    warnings.append(
        "The host exposes no {} open contract; capturing the window where it is".format(provider["label"])
    )
    return warnings


def reported_rect(result: Any) -> Optional[Dict[str, int]]:
    """Extract the region a capture reported, when it reports one."""
    if result is None:
        return None
    if isinstance(result, Mapping):
        if "rect" in result:
            rect, _ = coerce_rect(result["rect"], "capture result")
            return rect
        rect, _ = coerce_rect(result, "capture result")
        return rect
    for attribute in ("rect", "region", "captureRect", "capture_rect"):
        value = _safe_getattr(result, attribute)
        if value is not None:
            rect, _ = coerce_rect(value, attribute)
            return rect
    return None


def rects_match(requested: Mapping[str, int], actual: Mapping[str, int], tolerance: int = 1) -> bool:
    """Report whether a captured region matches the requested one."""
    for key in ("left", "top", "width", "height"):
        if key not in actual:
            continue
        try:
            if abs(int(actual[key]) - int(requested[key])) > tolerance:
                return False
        except (TypeError, ValueError):
            return False
    return True


# ---------------------------------------------------------------------------
# Multi-view capture
# ---------------------------------------------------------------------------

MULTI_VIEWS = ("front", "back", "left", "right", "top", "bottom", "perspective", "orthographic", "iso", "user")
VIEW_SETTERS = ("setView", "setType", "setViewType", "setViewportType")
VIEW_GETTERS = ("getView", "getType", "getViewType", "getViewportType")
TM_GETTERS = ("getTM", "getViewTM", "getViewTransform", "GetTM")
TM_SETTERS = ("setTM", "setViewTM", "setViewTransform", "SetTM")


def active_viewport(runtime: Any) -> Any:
    """Return the host object that owns the active viewport state."""
    for attribute in ("viewport", "activeViewport", "active_viewport"):
        viewport = _safe_getattr(runtime, attribute)
        if viewport is not None:
            return viewport
    return None


def viewport_state(runtime: Any, viewport: Any) -> Dict[str, Any]:
    """Snapshot the current view so it can be restored and verified."""
    state: Dict[str, Any] = {"view": None, "view_contract": None, "tm": None, "tm_contract": None, "readable": False}
    for name in VIEW_GETTERS:
        func = _safe_getattr(viewport, name)
        if not callable(func):
            continue
        try:
            value = func()
        except Exception:  # noqa: BLE001 - try the next contract.
            continue
        state["view"] = value
        state["view_contract"] = name
        state["readable"] = True
        break
    for name in TM_GETTERS:
        func = _safe_getattr(viewport, name)
        if not callable(func):
            continue
        try:
            value = func()
        except Exception:  # noqa: BLE001 - try the next contract.
            continue
        state["tm"] = value
        state["tm_contract"] = name
        state["readable"] = True
        break
    return state


def capture_multi_view(
    runtime: Any,
    output_dir: Path,
    *,
    views: Sequence[str],
    composite: bool = True,
    sheet_path: Optional[Path] = None,
    columns: Optional[int] = None,
    stem: str = "view",
    extension: str = ".png",
    capture: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Capture a set of standard views without leaving the user's view moved.

    The user's view is snapshotted before the first switch. If it cannot be
    read back, the tool refuses to move it at all: an unverifiable restore is
    not a restore.
    """
    viewport = active_viewport(runtime)
    if viewport is None:
        return render_error("No active viewport is available on this host", probed=["viewport", "activeViewport"])
    if not views:
        return render_error("At least one view is required", supported_views=list(MULTI_VIEWS))
    unsupported = [view for view in views if view.lower() not in MULTI_VIEWS]
    if unsupported:
        return render_error(
            "Unsupported view(s): {}".format(", ".join(sorted(unsupported))),
            supported_views=list(MULTI_VIEWS),
        )
    shooter = capture or _viewport_capture(runtime)
    if not callable(shooter):
        return render_error("No viewport capture operation is available", probed=list(VIEWPORT_CAPTURE_CALLABLES))

    original = viewport_state(runtime, viewport)
    if not original["readable"]:
        return render_error(
            "The current view cannot be read back, so it cannot be restored; refusing to switch views",
            probed={"getters": list(VIEW_GETTERS), "transforms": list(TM_GETTERS)},
        )

    captures: List[Dict[str, Any]] = []
    warnings: List[str] = []
    errors: List[Dict[str, Any]] = []
    for view in views:
        name = view.lower()
        switched, switch_error = set_viewport_view(viewport, name)
        if switch_error is not None:
            errors.append({"view": name, "error": switch_error})
            break
        if not switched:
            errors.append({"view": name, "error": "Could not verify the view switch to {}".format(name)})
            break
        tile_path = output_dir / "{}_{}{}".format(stem, name, extension)
        result, capture_error = _capture_tile(shooter, tile_path)
        if capture_error is not None:
            errors.append({"view": name, "error": capture_error})
            break
        captures.append({"view": name, **result})

    restored, restore_warnings, restore_error = restore_viewport_view(runtime, viewport, original)
    warnings.extend(restore_warnings)
    if restore_error is not None:
        errors.append({"view": "<restore>", "error": restore_error})

    sheet_info: Optional[Dict[str, Any]] = None
    composite_failed = False
    if composite and not errors:
        sheet_target = sheet_path or (output_dir / "multi_view{}".format(extension))
        sheet_info, composite_warning = compose_contact_sheet(
            [Path(item["path"]) for item in captures], sheet_target, columns=columns
        )
        if composite_warning is not None:
            warnings.append(composite_warning)
            composite_failed = sheet_info is None

    data = {
        "captures": captures,
        "capture_count": len(captures),
        "sheet": sheet_info,
        "view_restored": restored,
        "original_view": {"view": _jsonable(original["view"]), "contract": original["view_contract"]},
        "supported_views": list(MULTI_VIEWS),
        "warnings": warnings,
        "errors": errors,
    }
    if errors:
        return render_error(
            "Multi-view capture did not complete: {}".format(errors[0]["error"]),
            **data,
        )
    if composite_failed:
        # The tiles are on disk, so this is not a failure, but the caller asked
        # for a contact sheet: the message has to say it was not composed.
        return render_success(
            "Captured {} view(s); the contact sheet was not composed".format(len(captures)), **data
        )
    return render_success("Captured {} view(s)".format(len(captures)), **data)


VIEWPORT_CAPTURE_CALLABLES = ("captureViewport", "capture_viewport", "captureScreen", "capture_screen")


def _viewport_capture(runtime: Any) -> Any:
    for attribute in VIEWPORT_CAPTURE_CALLABLES:
        func = _safe_getattr(runtime, attribute)
        if callable(func):
            return func
    viewport = active_viewport(runtime)
    if viewport is not None:
        for attribute in ("captureBitmap", "capture", "getViewportDib"):
            func = _safe_getattr(viewport, attribute)
            if callable(func):
                return func
    return None


def _capture_tile(shooter: Callable[..., Any], tile_path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    temp_path = atomic_capture_target(tile_path)
    try:
        try:
            shooter(str(temp_path))
        except Exception as exc:  # noqa: BLE001 - a host failure is reported.
            return None, "Viewport capture failed: {}".format(exc)
        if not temp_path.is_file():
            return None, "Viewport capture did not produce a file"
        if temp_path.stat().st_size <= 0:
            return None, "Viewport capture produced an empty file"
        os.replace(temp_path, tile_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return {"path": str(tile_path), "artifact": artifact_info(tile_path)}, None


def set_viewport_view(viewport: Any, view: str) -> Tuple[bool, Optional[str]]:
    """Switch the viewport to a standard view and verify the switch."""
    warnings: List[str] = []
    for name in VIEW_SETTERS:
        func = _safe_getattr(viewport, name)
        if not callable(func):
            continue
        try:
            func(view)
        except Exception as exc:  # noqa: BLE001 - try the next contract.
            warnings.append("{} failed: {}".format(name, exc))
            continue
        readback = _read_view(viewport)
        if readback is None:
            warnings.append("{} did not report the resulting view".format(name))
            continue
        if _view_matches(readback, view):
            return True, None
        warnings.append("view read back {!r} after switching to {!r}".format(readback, view))
    if warnings:
        return False, "; ".join(warnings)
    return False, "The host exposes no view switch contract (probed: {})".format(", ".join(VIEW_SETTERS))


def restore_viewport_view(
    runtime: Any, viewport: Any, state: Mapping[str, Any]
) -> Tuple[bool, List[str], Optional[str]]:
    """Restore a snapshotted view and verify the restore by readback.

    Both halves of the snapshot are written back -- the transform and the view
    token -- and the restore only counts when reading the viewport back shows
    the state the caller started from. A host that restores one half but not
    the other would otherwise leave the user looking through a changed view.
    """
    warnings: List[str] = []
    if state.get("tm") is not None:
        for name in TM_SETTERS:
            func = _safe_getattr(viewport, name)
            if not callable(func):
                continue
            try:
                func(state["tm"])
                break
            except Exception as exc:  # noqa: BLE001 - try the next contract.
                warnings.append("{} failed: {}".format(name, exc))
                continue
        else:
            warnings.append("No transform restore contract (probed: {})".format(", ".join(TM_SETTERS)))
    if state.get("view") is not None:
        _switched, error = set_viewport_view(viewport, _view_token(state["view"]))
        if error is not None:
            warnings.append(error)
    after = viewport_state(runtime, viewport)
    if not after.get("readable"):
        return False, warnings, "The active view could not be read back to verify the restore"
    if _state_matches(state, after):
        return True, warnings, None
    return False, warnings, "The active view still reads {!r} after restoring {!r}".format(
        after.get("view") if after.get("view") is not None else after.get("tm"),
        state.get("view") if state.get("view") is not None else state.get("tm"),
    )


def _read_view(viewport: Any) -> Any:
    for name in VIEW_GETTERS:
        func = _safe_getattr(viewport, name)
        if not callable(func):
            continue
        try:
            return func()
        except Exception:  # noqa: BLE001 - try the next contract.
            continue
    return None


def _read_tm(viewport: Any) -> Any:
    for name in TM_GETTERS:
        func = _safe_getattr(viewport, name)
        if not callable(func):
            continue
        try:
            return func()
        except Exception:  # noqa: BLE001 - try the next contract.
            continue
    return None


def _view_token(value: Any) -> str:
    """Normalize a host view token (``#view_front``) into a bare name."""
    text = str(value).strip().lstrip("#").lower()
    for prefix in ("view_", "viewport_", "type_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def _view_matches(readback: Any, view: str) -> bool:
    if readback is None:
        return False
    return _view_token(readback) == _view_token(view)


def _tm_matches(readback: Any, expected: Any) -> bool:
    if readback is expected:
        return True
    left = _tm_values(readback)
    right = _tm_values(expected)
    if left is None or right is None:
        return False
    if len(left) != len(right):
        return False
    return all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6) for a, b in zip(left, right))


def _tm_values(value: Any) -> Optional[List[float]]:
    if value is None:
        return None
    numbers: List[float] = []
    for attribute in ("row1", "row2", "row3", "row4"):
        row = getattr(value, attribute, None)
        if row is None:
            continue
        for axis in ("x", "y", "z"):
            numbers.append(_as_float(getattr(row, axis, None)))
    if numbers:
        return numbers
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            number = _as_float(item)
            if number is None:
                return None
            numbers.append(number)
        return numbers
    return None


def compose_contact_sheet(
    tiles: Sequence[Path], sheet_path: Path, *, columns: Optional[int] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Tile captured views into one contact sheet.

    Compositing needs Pillow, which a 3ds Max host may not ship. A missing
    compositor is reported as a warning with the tiles left in place instead of
    being reported as a composed sheet.
    """
    if not tiles:
        return None, "No captured views to compose"
    try:
        from PIL import Image  # noqa: PLC0415 - optional host dependency.
    except ImportError:
        return None, "Pillow is not available on this host, so the contact sheet was not composed"
    images = []
    try:
        images = [Image.open(str(tile)) for tile in tiles]
        tile_width = max(image.width for image in images)
        tile_height = max(image.height for image in images)
        per_row = columns if columns and columns > 0 else max(1, int(math.ceil(math.sqrt(len(images)))))
        rows = int(math.ceil(len(images) / float(per_row)))
        sheet = Image.new("RGB", (tile_width * per_row, tile_height * rows), (24, 24, 24))
        for index, image in enumerate(images):
            column = index % per_row
            row = index // per_row
            sheet.paste(image.convert("RGB"), (column * tile_width, row * tile_height))
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(str(sheet_path))
    except Exception as exc:  # noqa: BLE001 - a failed composite is a warning, never a fake success.
        return None, "Could not compose the contact sheet: {}".format(exc)
    finally:
        for image in images:
            try:
                image.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the reported result.
                pass
    return artifact_info(sheet_path), None


# ---------------------------------------------------------------------------
# Dedicated agent viewport
# ---------------------------------------------------------------------------

AGENT_VIEWPORT_FACTORIES = (
    "createExtendedViewport",
    "CreateExtendedViewport",
    "createFloatingViewport",
    "CreateFloatingViewport",
)
AGENT_VIEWPORT_LOOKUPS = ("getExtendedViewport", "getViewportByName", "findViewport")
AGENT_VIEWPORT_COUNT_ATTRS = ("extendedViewportCount", "viewportCount", "numViewports")
AGENT_VIEWPORT_CLOSE_METHODS = ("close", "Close", "destroy")
AGENT_VIEWPORT_CLOSE_CALLABLES = ("closeExtendedViewport", "closeViewport", "closeFloatingViewport")

# Keyed by ``(id(runtime), name)``: a viewport belongs to the host that created it.
_AGENT_VIEWPORTS: Dict[Tuple[int, str], Any] = {}


def reset_agent_viewports() -> None:
    """Forget every agent viewport this process created (tests, scene reloads)."""
    _AGENT_VIEWPORTS.clear()


def forget_agent_viewport(runtime: Any, name: str) -> None:
    """Drop one agent viewport from the local registry."""
    _AGENT_VIEWPORTS.pop(_registry_key(runtime, name), None)


def _registry_key(runtime: Any, name: str) -> Tuple[int, str]:
    """Key the registry by host identity as well as name.

    A viewport belongs to the host that created it. Keying by name alone would
    hand a viewport from a previous session (or a previous host object) to a
    caller that asks for the same name later.
    """
    return (id(runtime), str(name))


def find_agent_viewport(runtime: Any, name: str) -> Any:
    """Return the agent viewport with ``name`` when it still exists.

    The host lookup wins; the local registry only covers hosts that expose no
    lookup contract at all.
    """
    found = host_agent_viewport(runtime, name)
    if found is not None:
        return found
    return _AGENT_VIEWPORTS.get(_registry_key(runtime, name))


def host_agent_viewport(runtime: Any, name: str) -> Any:
    """Ask the host whether it still exposes the named agent viewport."""
    for lookup in AGENT_VIEWPORT_LOOKUPS:
        func = _safe_getattr(runtime, lookup)
        if not callable(func):
            continue
        try:
            found = func(name)
        except TypeError:
            try:
                found = func(name=name)
            except Exception:  # noqa: BLE001 - fall through to the local registry.
                continue
        except Exception:  # noqa: BLE001 - fall through to the local registry.
            continue
        if found is not None:
            return found
    return None


def agent_viewport_status(runtime: Any, name: str) -> Dict[str, Any]:
    """Report whether a dedicated agent viewport exists and what it holds."""
    viewport = find_agent_viewport(runtime, name)
    return render_success(
        "Agent viewport status",
        name=name,
        exists=viewport is not None,
        viewport=viewport_summary(viewport) if viewport is not None else None,
        probed={"factories": list(AGENT_VIEWPORT_FACTORIES), "lookups": list(AGENT_VIEWPORT_LOOKUPS)},
        warnings=[],
    )


def ensure_agent_viewport(
    runtime: Any,
    name: str = "dcc_mcp_agent_viewport",
    *,
    options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Create (or reuse) a dedicated agent viewport without moving the user's.

    The user's active view is snapshotted up front and compared afterwards. A
    host that cannot report its own view is reported as unverified rather than
    assumed untouched.
    """
    active = active_viewport(runtime)
    before = viewport_state(runtime, active) if active is not None else None
    existing = find_agent_viewport(runtime, name)
    created = False
    warnings: List[str] = []
    if existing is not None:
        viewport = existing
    else:
        viewport, factory, create_error = _create_agent_viewport(runtime, name)
        if create_error is not None:
            return render_error(
                create_error,
                name=name,
                probed={"factories": list(AGENT_VIEWPORT_FACTORIES)},
                warnings=warnings,
            )
        created = True
        if viewport is not None:
            _AGENT_VIEWPORTS[_registry_key(runtime, name)] = viewport
            if host_agent_viewport(runtime, name) is None:
                warnings.append(
                    "Created the agent viewport with {}; the host exposes no lookup that reports it back".format(
                        factory
                    )
                )
    if viewport is None:
        return render_error(
            "The host created no agent viewport object",
            name=name,
            probed={"factories": list(AGENT_VIEWPORT_FACTORIES)},
            warnings=warnings,
        )

    rows: List[Dict[str, Any]] = []
    if options:
        rows = apply_viewport_options(runtime, viewport, options)

    intact, intact_warnings, intact_error = _verify_user_view(runtime, active, before)
    warnings.extend(intact_warnings)
    errors = [row for row in rows if row["status"] == SETTING_REJECTED]
    data = {
        "name": name,
        "created": created,
        "viewport": viewport_summary(viewport),
        "options": rows,
        "applied": [row["option"] for row in rows if row["status"] == SETTING_APPLIED],
        "rejected": [row["option"] for row in rows if row["status"] == SETTING_REJECTED],
        "user_view_intact": intact,
        "warnings": warnings,
    }
    if intact_error is not None:
        data["errors"] = [{"setting": "user_view", "error": intact_error}]
        return render_error(intact_error, **data)
    if errors:
        data["errors"] = errors
        return render_error("The agent viewport rejected {} option(s)".format(len(errors)), **data)
    return render_success("Agent viewport is ready", **data)


def close_agent_viewport(runtime: Any, name: str = "dcc_mcp_agent_viewport") -> Dict[str, Any]:
    """Close the dedicated agent viewport and verify it is gone."""
    viewport = find_agent_viewport(runtime, name)
    if viewport is None:
        return render_success("No agent viewport named {} is open".format(name), name=name, closed=False)
    warnings: List[str] = []
    closed = False
    for method in AGENT_VIEWPORT_CLOSE_METHODS:
        func = _safe_getattr(viewport, method)
        if callable(func):
            try:
                func()
                closed = True
            except Exception as exc:  # noqa: BLE001 - reported, never silently skipped.
                warnings.append("{} failed: {}".format(method, exc))
            break
    if not closed:
        for attribute in AGENT_VIEWPORT_CLOSE_CALLABLES:
            func = _safe_getattr(runtime, attribute)
            if not callable(func):
                continue
            try:
                func(viewport)
                closed = True
            except Exception as exc:  # noqa: BLE001 - reported, never silently skipped.
                warnings.append("{} failed: {}".format(attribute, exc))
            break
    if not closed:
        # A viewport that is still open must never be reported as closed: the
        # message and the flag would both say the opposite of the truth, and
        # the next ensure would create a second one.
        return render_error(
            "Could not close the agent viewport",
            name=name,
            closed=False,
            probed={"methods": list(AGENT_VIEWPORT_CLOSE_METHODS), "runtime": list(AGENT_VIEWPORT_CLOSE_CALLABLES)},
            warnings=warnings,
            errors=[{"setting": "close", "error": "; ".join(warnings) or "no close contract on this host"}],
        )
    if host_agent_viewport(runtime, name) is not None:
        return render_error(
            "The host still reports the agent viewport after closing it",
            name=name,
            closed=False,
            warnings=warnings,
        )
    forget_agent_viewport(runtime, name)
    return render_success("Closed the agent viewport", name=name, closed=True, warnings=warnings)


def _create_agent_viewport(runtime: Any, name: str) -> Tuple[Any, Optional[str], Optional[str]]:
    """Create a floating viewport through the first factory the host exposes."""
    probed: List[str] = []
    for factory_name in AGENT_VIEWPORT_FACTORIES:
        factory = _safe_getattr(runtime, factory_name)
        if not callable(factory):
            continue
        probed.append(factory_name)
        for args, kwargs in (((), {"name": name}), ((name,), {}), ((), {})):
            try:
                viewport = factory(*args, **kwargs)
            except TypeError:
                continue
            except Exception as exc:  # noqa: BLE001 - reported, never silently skipped.
                return None, factory_name, "{} failed: {}".format(factory_name, exc)
            if viewport is not None:
                return viewport, factory_name, None
    if not probed:
        return None, None, (
            "The host exposes no extended/floating viewport factory, so no dedicated agent "
            "viewport can be created (probed: {})".format(", ".join(AGENT_VIEWPORT_FACTORIES))
        )
    return None, None, "Every agent viewport factory returned nothing (probed: {})".format(", ".join(probed))


def _verify_user_view(
    runtime: Any, active: Any, before: Optional[Mapping[str, Any]]
) -> Tuple[Optional[bool], List[str], Optional[str]]:
    """Confirm the user's active view was left where it was."""
    if active is None:
        return None, ["No active viewport to compare"], None
    if before is None or not before.get("readable"):
        return None, ["The original view could not be read, so leaving it untouched is unverified"], None
    after = viewport_state(runtime, active)
    if not after.get("readable"):
        return None, ["The active view could not be read back after creating the agent viewport"], None
    if not _state_matches(before, after):
        restored, warnings, error = restore_viewport_view(runtime, active, before)
        message = "Creating the agent viewport changed the active view"
        if error is not None:
            return False, warnings, message + "; restoring it failed: {}".format(error)
        if not restored:
            return False, warnings, message + "; it could not be restored"
        return True, warnings + [message + "; the original view was restored"], None
    return True, [], None


def _state_matches(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    if before.get("view") is not None and after.get("view") is not None:
        return _view_matches(after["view"], _view_token(before["view"]))
    if before.get("tm") is not None and after.get("tm") is not None:
        return _tm_matches(after["tm"], before["tm"])
    return False


def viewport_summary(viewport: Any) -> Dict[str, Any]:
    """Describe a viewport object without assuming any host contract."""
    summary: Dict[str, Any] = {"type": type(viewport).__name__}
    for key, attributes in (
        ("name", ("name", "viewportName")),
        ("view", ("view", "type", "viewType")),
        ("shading", ("shadingMode", "shading_mode", "renderLevel")),
    ):
        for attribute in attributes:
            value = _safe_getattr(viewport, attribute)
            if value is not None:
                summary[key] = _jsonable(value)
                break
    return summary


# ---------------------------------------------------------------------------
# Viewport options (verified writes)
# ---------------------------------------------------------------------------

SHADING_MODES = ("realistic", "shaded", "consistent_colors", "wireframe", "facets", "flat", "smooth")
VIEWPORT_LAYOUTS = ("single", "two_horizontal", "two_vertical", "four")

VIEWPORT_OPTIONS: Dict[str, Dict[str, Any]] = {
    "shading": {
        "attrs": ("shadingMode", "shading_mode", "renderLevel", "render_level", "shading"),
        "callables": ("setShadingMode", "setRenderLevel", "setShading"),
        "matcher": "text",
        "enum": SHADING_MODES,
    },
    "layout": {
        "attrs": ("layout", "viewportLayout"),
        "callables": ("setLayout", "setViewportLayout"),
        "matcher": "text",
        "enum": VIEWPORT_LAYOUTS,
    },
    "edged_faces": {
        "attrs": ("edgedFaces", "edgeFaces", "showEdgedFaces"),
        "callables": ("setEdgedFaces", "showEdgedFaces"),
        "matcher": "bool",
    },
    "grid": {
        "attrs": ("showGrid", "gridVisible", "grid"),
        "callables": ("setGridVisibility", "showGrid"),
        "matcher": "bool",
    },
    "safe_frame": {
        "attrs": ("showSafeFrame", "safeFrame", "safeFrameOn"),
        "callables": ("setSafeFrame", "showSafeFrame"),
        "matcher": "bool",
    },
    "statistics": {
        "attrs": ("showStatistics", "statistics", "statisticsOn"),
        "callables": ("setStatistics", "showStatistics"),
        "matcher": "bool",
    },
}


def apply_viewport_options(
    runtime: Any, viewport: Any, options: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Apply viewport options, verifying every single one by readback."""
    rows: List[Dict[str, Any]] = []
    for option, value in options.items():
        if option == "camera":
            rows.append(_apply_viewport_camera(runtime, viewport, value))
            continue
        rows.append(apply_viewport_option(runtime, viewport, option, value))
    return rows


def apply_viewport_option(runtime: Any, viewport: Any, option: str, value: Any) -> Dict[str, Any]:
    """Write one viewport option and report whether the host kept it."""
    descriptor = VIEWPORT_OPTIONS.get(option)
    if descriptor is None:
        return _option_row(option, SETTING_REJECTED, value, None, "Unsupported viewport option", [], [])
    if descriptor.get("enum") is not None and isinstance(value, str):
        normalized = value.strip().lower()
        if normalized not in descriptor["enum"]:
            return _option_row(
                option,
                SETTING_REJECTED,
                value,
                None,
                "Unsupported value; supported: {}".format(", ".join(descriptor["enum"])),
                [],
                [],
            )
    warnings: List[str] = []
    probed: List[str] = []
    for attribute in descriptor["attrs"]:
        if not attr_present(runtime, viewport, attribute):
            continue
        probed.append(attribute)
        current = _safe_getattr(viewport, attribute)
        if callable(current):
            continue
        try:
            setattr(viewport, attribute, value)
        except Exception as exc:  # noqa: BLE001 - a host rejection is reported.
            warnings.append("Could not set {}: {}".format(attribute, exc))
            continue
        readback = _safe_getattr(viewport, attribute)
        if _option_matches(readback, value, descriptor["matcher"]):
            return _option_row(option, SETTING_APPLIED, value, readback, None, probed, warnings)
        warnings.append("{} read back {!r} after writing {!r}".format(attribute, readback, value))
    for name in descriptor["callables"]:
        func = _safe_getattr(viewport, name)
        if not callable(func):
            continue
        probed.append(name + "()")
        try:
            func(value)
        except Exception as exc:  # noqa: BLE001 - a host rejection is reported.
            warnings.append("{} failed: {}".format(name, exc))
            continue
        readback = _read_viewport_option(viewport, descriptor)
        if readback is None:
            return _option_row(
                option,
                SETTING_UNVERIFIED,
                value,
                None,
                "{} did not report the value back after writing {!r}".format(name, value),
                probed,
                warnings,
            )
        if _option_matches(readback, value, descriptor["matcher"]):
            return _option_row(option, SETTING_APPLIED, value, readback, None, probed, warnings)
        warnings.append("{} read back {!r} after writing {!r}".format(name, readback, value))
    message = "; ".join(warnings) if warnings else "No attribute on this viewport accepted {}".format(option)
    return _option_row(option, SETTING_REJECTED, value, None, message, probed, warnings)


def _apply_viewport_camera(runtime: Any, viewport: Any, node: Any) -> Dict[str, Any]:
    """Point a viewport at a camera and verify the assignment."""
    warnings: List[str] = []
    probed: List[str] = []
    for name in ("setCamera", "setViewCamera"):
        func = _safe_getattr(viewport, name)
        if not callable(func):
            continue
        probed.append(name + "()")
        try:
            func(node)
        except Exception as exc:  # noqa: BLE001 - a host rejection is reported.
            warnings.append("{} failed: {}".format(name, exc))
            continue
        readback = _read_viewport_camera(viewport)
        if readback is not None and _same_camera(readback, node):
            return _option_row("camera", SETTING_APPLIED, _camera_label(node), _camera_label(readback), None, probed, warnings)
        warnings.append("viewport camera read back {!r} after assigning {!r}".format(readback, node))
    for attribute in ("camera", "activeCamera"):
        if not attr_present(runtime, viewport, attribute):
            continue
        probed.append(attribute)
        try:
            setattr(viewport, attribute, node)
        except Exception as exc:  # noqa: BLE001 - a host rejection is reported.
            warnings.append("Could not set {}: {}".format(attribute, exc))
            continue
        readback = _safe_getattr(viewport, attribute)
        if _same_camera(readback, node):
            return _option_row(
                "camera", SETTING_APPLIED, _camera_label(node), _camera_label(readback), None, probed, warnings
            )
        warnings.append("{} read back {!r} after assigning {!r}".format(attribute, readback, node))
    message = "; ".join(warnings) if warnings else "No camera contract on this viewport accepted the camera"
    return _option_row("camera", SETTING_REJECTED, _camera_label(node), None, message, probed, warnings)


def _read_viewport_camera(viewport: Any) -> Any:
    for name in ("getCamera", "camera", "activeCamera"):
        value = _safe_getattr(viewport, name)
        if callable(value) and name == "getCamera":
            try:
                return value()
            except Exception:  # noqa: BLE001 - an unreadable camera is reported.
                continue
        if value is not None and not callable(value):
            return value
    return None


def _read_viewport_option(viewport: Any, descriptor: Mapping[str, Any]) -> Any:
    for attribute in descriptor["attrs"]:
        value = _safe_getattr(viewport, attribute)
        if value is not None and not callable(value):
            return value
    return None


def _option_matches(readback: Any, value: Any, matcher: str) -> bool:
    if matcher == "bool":
        if readback is None:
            return False
        return bool(readback) == bool(value)
    if matcher == "text":
        if readback is None:
            return False
        if isinstance(readback, str) and isinstance(value, str):
            return readback.strip().lower() == value.strip().lower()
        return readback == value
    return readback == value


def _option_row(
    option: str,
    status: str,
    requested: Any,
    actual: Any,
    message: Optional[str],
    probed: Sequence[str],
    warnings: Sequence[str],
) -> Dict[str, Any]:
    return {
        "option": option,
        "status": status,
        "requested": _jsonable(requested),
        "actual": _jsonable(actual),
        "error": message if status == SETTING_REJECTED else None,
        "warning": message if status == SETTING_UNVERIFIED else None,
        "warnings": list(warnings),
        "probed": list(probed),
    }


def _camera_label(camera: Any) -> Any:
    if camera is None:
        return None
    return _jsonable(getattr(camera, "name", camera))


def _same_camera(candidate: Any, node: Any) -> bool:
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


# ---------------------------------------------------------------------------
# V-Ray interactive preview (IPR)
# ---------------------------------------------------------------------------

IPR_START_CALLABLES = ("vrayStartIPR", "vray_start_ipr", "startIPR", "start_ipr")
IPR_STOP_CALLABLES = ("vrayStopIPR", "vray_stop_ipr", "stopIPR", "stop_ipr")
IPR_REFRESH_CALLABLES = ("vrayRefreshIPR", "vrayUpdateIPR", "refreshIPR", "updateIPR")
IPR_STATE_CALLABLES = ("vrayIsIPRRunning", "isIPRRunning", "vrayGetIPRState", "getIPRState")
IPR_STATE_ATTRS = ("IPRRunning", "iprRunning", "isIPRRunning", "ipr_running")


def vray_ipr(runtime: Any, action: str = "status") -> Dict[str, Any]:
    """Start, stop, refresh, or query the V-Ray interactive preview."""
    if action not in ("status", "start", "stop", "refresh"):
        return render_error(
            "Unsupported V-Ray IPR action", action=action, supported_actions=["status", "start", "stop", "refresh"]
        )
    renderer = current_renderer(runtime)
    state, state_contract = ipr_state(runtime, renderer)
    data: Dict[str, Any] = {
        "action": action,
        "running": state,
        "state_contract": state_contract,
        "renderer": type(renderer).__name__ if renderer is not None else None,
        "warnings": [],
    }
    if action == "status":
        if state is None:
            data["warnings"].append(
                "The host exposes no V-Ray IPR state contract, so the preview state is unknown (probed: {})".format(
                    ", ".join(list(IPR_STATE_CALLABLES) + list(IPR_STATE_ATTRS))
                )
            )
            return render_success("V-Ray IPR state is unknown on this host", **data)
        return render_success("V-Ray IPR is {}".format("running" if state else "stopped"), **data)

    callables = {
        "start": IPR_START_CALLABLES,
        "stop": IPR_STOP_CALLABLES,
        "refresh": IPR_REFRESH_CALLABLES,
    }[action]
    func, owner = _find_callable_on(renderer, runtime, ipr_callable_names(action))
    if func is None:
        return render_error(
            "No V-Ray IPR {} contract is available on this host".format(action),
            action=action,
            probed=list(callables),
            renderer=data["renderer"],
        )
    try:
        func()
    except Exception as exc:  # noqa: BLE001 - a host rejection is reported.
        return render_error(
            "V-Ray IPR {} failed: {}".format(action, exc),
            action=action,
            contract=owner,
            exception_type=type(exc).__name__,
        )
    after, after_contract = ipr_state(runtime, renderer)
    data["running"] = after
    data["state_contract"] = after_contract
    data["contract"] = owner
    if after_contract is None:
        data["warnings"].append(
            "The host exposes no V-Ray IPR state contract, so the {} result is unverified".format(action)
        )
        return render_success("V-Ray IPR {} was requested but not verified".format(action), **data)
    expected = action in ("start", "refresh")
    if bool(after) is not expected:
        return render_error(
            "V-Ray IPR {} did not change the preview state".format(action),
            action=action,
            contract=owner,
            expected_running=expected,
            actual_running=bool(after),
            warnings=data["warnings"],
        )
    return render_success(
        "V-Ray IPR {} complete; the preview is {}".format(action, "running" if after else "stopped"), **data
    )


def ipr_state(runtime: Any, renderer: Any) -> Tuple[Optional[bool], Optional[str]]:
    """Read the V-Ray IPR running state, or ``(None, None)`` when unreadable."""
    targets: List[Any] = [renderer] if renderer is not None else []
    targets.append(runtime)
    for target in targets:
        for name in IPR_STATE_CALLABLES:
            func = _safe_getattr(target, name)
            if not callable(func):
                continue
            try:
                return bool(func()), name
            except Exception:  # noqa: BLE001 - try the next contract.
                continue
        for attribute in IPR_STATE_ATTRS:
            if not attr_present(runtime, target, attribute):
                continue
            value = _safe_getattr(target, attribute)
            if value is None:
                continue
            return bool(value), attribute
    return None, None


def ipr_callable_names(action: str) -> Tuple[str, ...]:
    """Return the host contracts that perform one V-Ray IPR action."""
    return {
        "start": IPR_START_CALLABLES,
        "stop": IPR_STOP_CALLABLES,
        "refresh": IPR_REFRESH_CALLABLES,
    }.get(action, ())


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _first_callable(owner: Any, names: Sequence[str]) -> Any:
    for name in names:
        func = _safe_getattr(owner, name)
        if callable(func):
            return func
    return None


def _find_callable_on(
    renderer: Any, runtime: Any, names: Sequence[str], extra: Sequence[str] = ()
) -> Tuple[Any, Optional[str]]:
    """Find an IPR contract on the active renderer first, then on the runtime."""
    candidates = tuple(names) + tuple(extra)
    owners: List[Tuple[str, Any]] = []
    if renderer is not None:
        owners.append(("renderer", renderer))
    owners.append(("runtime", runtime))
    for label, owner in owners:
        for name in candidates:
            func = _safe_getattr(owner, name)
            if callable(func):
                return func, "{}:{}".format(label, name)
    return None, None


def _safe_getattr(owner: Any, name: str) -> Any:
    try:
        return getattr(owner, name)
    except Exception:  # noqa: BLE001 - a missing contract is not an error.
        return None


def _first_number(mapping: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        if key in mapping:
            number = _as_number(mapping[key])
            if number is not None:
                return number
    return None


def _first_attr_number(owner: Any, keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        number = _as_number(_safe_getattr(owner, key))
        if number is not None:
            return number
    return None


def _as_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    return _as_number(value)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def resolve_viewport_camera(runtime: Any, camera_name: Optional[str]) -> Tuple[Optional[Any], Optional[str]]:
    """Resolve a camera node by name, reporting why it failed when it does."""
    if camera_name is None or not str(camera_name).strip():
        return None, None
    result, node = resolve_node_object(runtime, node_name=camera_name, handle=None)
    if node is None:
        return None, str(result.get("message", "Camera '{}' could not be resolved".format(camera_name)))
    return node, None
