"""FBX timeline inspection and scene alignment for geometry imports.

3ds Max's FBX importer does not adopt the time settings stored in an FBX file.
Importing a 24 fps clip into a scene that still runs at 30 fps silently
resamples the animation, so frame N in 3ds Max is no longer frame N in the
source: poses stop lining up and the import still reports success.

This module reads the ``GlobalSettings`` block of an FBX file with the standard
library only (no FBX SDK, so it also works inside the 3ds Max Python runtime)
and aligns the scene timeline before ``importFile`` runs. Only the node tree up
to ``GlobalSettings`` is walked, so the cost does not depend on how much
geometry the file contains.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# FBX stores absolute time in "KTime" units; one second is exactly this many
# units, which is what lets non-integer frame rates stay exact.
KTIME_UNITS_PER_SECOND = 46186158000

_BINARY_MAGIC = b"Kaydara FBX Binary  \x00"
_ASCII_SCAN_BYTES = 262144
_TIME_SETTING_KEYS = ("TimeMode", "TimeSpanStart", "TimeSpanStop", "CustomFrameRate")

# FbxTime::EMode values, mirrored from fbxsdk/core/base/fbxtime.h.
TIME_MODE_DEFAULT = 0
TIME_MODE_MILLISECONDS = 12
TIME_MODE_CUSTOM = 14
TIME_MODE_FRAME_RATES = {
    1: 120.0,  # eFrames120
    2: 100.0,  # eFrames100
    3: 60.0,  # eFrames60
    4: 50.0,  # eFrames50
    5: 48.0,  # eFrames48
    6: 30.0,  # eFrames30
    7: 30.0,  # eFrames30Drop
    8: 30000.0 / 1001.0,  # eNTSCDropFrame
    9: 30000.0 / 1001.0,  # eNTSCFullFrame
    10: 25.0,  # ePAL
    11: 24.0,  # eFrames24
    13: 24.0,  # eFilmFullFrame
    15: 96.0,  # eFrames96
    16: 72.0,  # eFrames72
    17: 60000.0 / 1001.0,  # eFrames59dot94
    18: 120000.0 / 1001.0,  # eFrames119dot88
}
TIME_MODE_NAMES = {
    0: "eDefaultMode",
    1: "eFrames120",
    2: "eFrames100",
    3: "eFrames60",
    4: "eFrames50",
    5: "eFrames48",
    6: "eFrames30",
    7: "eFrames30Drop",
    8: "eNTSCDropFrame",
    9: "eNTSCFullFrame",
    10: "ePAL",
    11: "eFrames24",
    12: "eFrames1000",
    13: "eFilmFullFrame",
    14: "eCustom",
    15: "eFrames96",
    16: "eFrames72",
    17: "eFrames59dot94",
    18: "eFrames119dot88",
}

# 3ds Max only holds integer frame rates; NTSC variants are snapped to these.
_MAX_FRAME_RATES = (24.0, 25.0, 30.0, 48.0, 50.0, 60.0, 72.0, 96.0, 100.0, 120.0)
_RATE_TOLERANCE = 0.01

_SCALAR_PROPERTY_TYPES = {b"Y": "<h", b"I": "<i", b"F": "<f", b"D": "<d", b"L": "<q"}
_ARRAY_PROPERTY_TYPES = (b"f", b"d", b"l", b"i", b"b")
_ASCII_PROPERTY_RE = re.compile(r'^\s*(?:P|Property):\s*"([^"]+)"\s*,(.*)$')


class _FbxReadError(Exception):
    """Raised when an FBX stream is truncated or uses an unknown property type."""



def read_fbx_time_settings(file_path: Any) -> Optional[Dict[str, Any]]:
    """Return the timeline settings stored in an FBX file's ``GlobalSettings``.

    Returns ``None`` when the file is not an FBX file or carries no usable time
    settings; the caller is then responsible for reporting that the timeline
    could not be aligned.
    """
    path = Path(str(file_path)).expanduser()
    try:
        with open(str(path), "rb") as handle:
            head = handle.read(21)
            if head == _BINARY_MAGIC:
                handle.read(2)
                raw_version = handle.read(4)
                if len(raw_version) != 4:
                    return None
                version = struct.unpack("<I", raw_version)[0]
                try:
                    values = _scan_binary_global_settings(handle, version)
                except _FbxReadError:
                    return None
                encoding = "binary"
            else:
                handle.seek(0)
                text = handle.read(_ASCII_SCAN_BYTES).decode("utf-8", "replace")
                values = _scan_ascii_global_settings(text)
                version = None
                encoding = "ascii"
    except OSError:
        return None
    if not values:
        return None
    return _time_settings(values, encoding=encoding, fbx_version=version)


def scene_time_settings(runtime: Any) -> Dict[str, Any]:
    """Return the current scene timeline as JSON-safe values."""
    interval = getattr(runtime, "animationRange", None)
    return {
        "frame_rate": float(getattr(runtime, "frameRate", 30.0) or 30.0),
        "frame_start": int(
            _frame_value(getattr(interval, "start", None), getattr(runtime, "animationRangeStart", 0))
        ),
        "frame_end": int(_frame_value(getattr(interval, "end", None), getattr(runtime, "animationRangeEnd", 100))),
    }


def apply_source_timeline(
    runtime: Any,
    file_path: Any,
    fbx_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Align the scene timeline with an FBX file's own time settings.

    The report always contains the source settings plus the scene timeline
    before and afterwards, so a caller can see whether frame numbers still line
    up even when nothing could be aligned.
    """
    options = fbx_options or {}
    mode = str(options.get("timeline_mode") or "source")
    report: Dict[str, Any] = {
        "mode": mode,
        "source": None,
        "scene_before": scene_time_settings(runtime),
        "scene_after": None,
        "scene_after_import": None,
        "requested": None,
        "applied": False,
        "frame_rate_matches": False,
        "range_covers_source": False,
        "warnings": [],
    }
    source = read_fbx_time_settings(file_path)
    report["source"] = source
    if mode not in ("source", "union", "off"):
        report["warnings"].append("Unsupported timeline_mode '{}'; the scene timeline was left unchanged".format(mode))
        mode = "off"
        report["mode"] = mode

    if options.get("include_animation") is False:
        report["warnings"].append(
            "FBX animation is not imported (include_animation=false); the scene timeline was left unchanged"
        )
        return _finish_timeline_report(runtime, report)
    if source is None or not source.get("frame_rate"):
        before = report["scene_before"]
        report["warnings"].append(
            "Could not read the FBX source time settings; the scene timeline was left at {:g} fps / {}-{} and "
            "imported frame numbers may not match the source file".format(
                before["frame_rate"], before["frame_start"], before["frame_end"]
            )
        )
        return _finish_timeline_report(runtime, report)
    if not source.get("has_animation"):
        report["warnings"].append(
            "FBX source declares a zero-length animation range (frames {}-{}); the scene timeline was left "
            "unchanged".format(source.get("start_frame"), source.get("end_frame"))
        )
        return _finish_timeline_report(runtime, report)
    if mode == "off":
        return _finish_timeline_report(runtime, report)

    source_rate = float(source["frame_rate"])
    requested_rate = _max_frame_rate(source_rate)
    if abs(requested_rate - source_rate) > _RATE_TOLERANCE:
        report["warnings"].append(
            "3ds Max cannot represent the FBX source frame rate of {:g} fps; the scene frame rate was set to "
            "{:g} fps, so frame numbers drift by about {:.2f}%".format(
                source_rate, requested_rate, abs(requested_rate - source_rate) / source_rate * 100.0
            )
        )
    target_start = int(source["start_frame"])
    target_end = int(source["end_frame"])
    if mode == "union":
        target_start = min(target_start, report["scene_before"]["frame_start"])
        target_end = max(target_end, report["scene_before"]["frame_end"])
    report["requested"] = {"frame_rate": requested_rate, "frame_start": target_start, "frame_end": target_end}

    errors = _set_scene_timeline(runtime, requested_rate, target_start, target_end)
    report["warnings"].extend(errors)
    report["applied"] = not errors
    return _finish_timeline_report(runtime, report)


def verify_timeline_after_import(runtime: Any, report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Re-check the scene timeline once the importer has run.

    The importer is free to reset the time settings it was handed. When it
    does, the source timeline is applied again so frame numbers still line up,
    and the report says the timeline had to be restored instead of quietly
    claiming an alignment that no longer holds.
    """
    if not report or not report.get("applied"):
        return report
    requested = report.get("requested") or {}
    after_import = scene_time_settings(runtime)
    report["scene_after_import"] = after_import
    if after_import == report.get("scene_after"):
        return report

    errors = _set_scene_timeline(
        runtime,
        requested["frame_rate"],
        requested["frame_start"],
        requested["frame_end"],
    )
    # The caller may already have drained ``warnings`` into its own list.
    warnings = report.setdefault("warnings", [])
    if errors:
        warnings.extend(errors)
        report["applied"] = False
    else:
        warnings.append(
            "The FBX importer reset the scene timeline to {:g} fps / {}-{} after importing; the source timeline of "
            "{:g} fps / {}-{} was re-applied".format(
                after_import["frame_rate"],
                after_import["frame_start"],
                after_import["frame_end"],
                requested["frame_rate"],
                requested["frame_start"],
                requested["frame_end"],
            )
        )
        report["scene_after"] = scene_time_settings(runtime)
    _refresh_alignment(report)
    warnings.extend(_alignment_warnings(report))
    return report


def _finish_timeline_report(runtime: Any, report: Dict[str, Any]) -> Dict[str, Any]:
    """Record the resulting scene timeline and any remaining alignment warnings."""
    report["scene_after"] = scene_time_settings(runtime)
    _refresh_alignment(report)
    report["warnings"].extend(_alignment_warnings(report))
    return report


def _refresh_alignment(report: Dict[str, Any]) -> None:
    """Recompute the alignment flags from the recorded scene timeline."""
    source = report.get("source") or {}
    after = report["scene_after"]
    source_rate = source.get("frame_rate")
    start_frame = source.get("start_frame")
    end_frame = source.get("end_frame")
    if source_rate:
        report["frame_rate_matches"] = abs(float(after["frame_rate"]) - float(source_rate)) <= _RATE_TOLERANCE
    if start_frame is not None and end_frame is not None:
        report["range_covers_source"] = bool(after["frame_start"] <= start_frame and after["frame_end"] >= end_frame)


def _alignment_warnings(report: Dict[str, Any]) -> List[str]:
    """Describe how the scene timeline still differs from the FBX source."""
    warnings: List[str] = []
    source = report.get("source") or {}
    after = report["scene_after"]
    source_rate = source.get("frame_rate")
    start_frame = source.get("start_frame")
    end_frame = source.get("end_frame")
    if source_rate and not report["frame_rate_matches"]:
        warnings.append(
            "Scene frame rate is {:g} fps but the FBX source is {:g} fps; imported frame numbers do not correspond "
            "1:1 with the source file".format(float(after["frame_rate"]), float(source_rate))
        )
    elif start_frame is not None and end_frame is not None and not report["range_covers_source"]:
        warnings.append(
            "Scene animation range is {}-{} and does not cover the FBX source range {}-{}; part of the imported "
            "animation is outside the playback range".format(
                after["frame_start"], after["frame_end"], start_frame, end_frame
            )
        )
    return warnings


def _max_frame_rate(frame_rate: float) -> float:
    """Snap an FBX frame rate to a rate 3ds Max can actually hold."""
    for candidate in _MAX_FRAME_RATES:
        if abs(candidate - frame_rate) <= 0.5:
            return candidate
    return float(round(frame_rate))


def _set_scene_timeline(runtime: Any, frame_rate: float, start_frame: int, end_frame: int) -> List[str]:
    """Set the scene timeline, returning a warning for anything that did not stick.

    The frame rate must be set first: 3ds Max stores ``animationRange`` in
    ticks, so a range assigned before the frame rate is reinterpreted
    afterwards and lands on different frame numbers.
    """
    errors: List[str] = []
    try:
        runtime.frameRate = float(frame_rate)
    except Exception as exc:  # noqa: BLE001 - host runtime errors must not abort the import.
        errors.append("Could not set the scene frame rate to {:g} fps: {}".format(frame_rate, exc))
    interval = _interval_factory(runtime)
    try:
        if interval is not None:
            runtime.animationRange = interval(start_frame, end_frame)
        else:
            runtime.animationRangeStart = start_frame
            runtime.animationRangeEnd = end_frame
    except Exception as exc:  # noqa: BLE001 - host runtime errors must not abort the import.
        errors.append("Could not set the scene animation range to {}-{}: {}".format(start_frame, end_frame, exc))
    return errors


def _interval_factory(runtime: Any) -> Any:
    for name in ("Interval", "interval"):
        candidate = getattr(runtime, name, None)
        if callable(candidate):
            return candidate
    return None


def _frame_value(value: Any, fallback: Any) -> float:
    frame = getattr(value, "frame", None)
    if frame is not None:
        return float(frame)
    if value is None:
        return float(fallback or 0)
    return float(value)


def _time_settings(values: Dict[str, Any], *, encoding: str, fbx_version: Optional[int]) -> Dict[str, Any]:
    """Build the public time-settings payload from raw ``GlobalSettings`` values."""
    try:
        time_mode = int(values.get("TimeMode"))
    except (TypeError, ValueError):
        return {}
    custom = _as_number(values.get("CustomFrameRate"))
    frame_rate = None
    if time_mode == TIME_MODE_CUSTOM:
        if custom is not None and custom > 0:
            frame_rate = float(custom)
    elif time_mode not in (TIME_MODE_DEFAULT, TIME_MODE_MILLISECONDS):
        frame_rate = TIME_MODE_FRAME_RATES.get(time_mode)
    start_units = _as_number(values.get("TimeSpanStart"))
    stop_units = _as_number(values.get("TimeSpanStop"))
    info: Dict[str, Any] = {
        "encoding": encoding,
        "fbx_version": fbx_version,
        "time_mode": time_mode,
        "time_mode_name": TIME_MODE_NAMES.get(time_mode, "unknown"),
        "frame_rate": frame_rate,
        "custom_frame_rate": custom if (custom is not None and custom > 0) else None,
        "time_span_start": int(start_units) if start_units is not None else None,
        "time_span_stop": int(stop_units) if stop_units is not None else None,
        "start_frame": None,
        "end_frame": None,
        "has_animation": False,
    }
    if frame_rate and start_units is not None and stop_units is not None:
        info["start_frame"] = _frame_at(start_units, frame_rate)
        info["end_frame"] = _frame_at(stop_units, frame_rate)
        info["has_animation"] = stop_units > start_units
    return info


def _frame_at(units: float, frame_rate: float) -> int:
    return int(round(units * frame_rate / KTIME_UNITS_PER_SECOND))


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _scan_binary_global_settings(handle: Any, version: int) -> Dict[str, Any]:
    """Walk binary FBX top-level nodes until ``GlobalSettings`` is found."""
    wide = version >= 7500
    for name, header in _iter_nodes(handle, wide):
        node_end, num_properties = header
        if name == "GlobalSettings":
            _skip_properties(handle, num_properties)
            return _binary_property_values(handle, wide, node_end)
        handle.seek(node_end)
    return {}


def _iter_nodes(handle: Any, wide: bool, end: Optional[int] = None) -> Iterator[Tuple[str, Tuple[int, int]]]:
    """Yield ``(name, (end_offset, property_count))`` for each node at this level."""
    while end is None or handle.tell() < end:
        header = _read_node_header(handle, wide)
        if header is None:
            return
        node_end, num_properties, name = header
        if node_end <= 0:  # Null sentinel closes a node's child list.
            return
        yield name, (node_end, num_properties)
        handle.seek(node_end)


def _read_node_header(handle: Any, wide: bool) -> Optional[Tuple[int, int, str]]:
    fmt = "<QQQB" if wide else "<IIIB"
    size = struct.calcsize(fmt)
    data = handle.read(size)
    if len(data) != size:
        return None
    end, num_properties, _property_bytes, name_len = struct.unpack(fmt, data)
    name_bytes = handle.read(name_len) if name_len else b""
    if len(name_bytes) != name_len:
        return None
    return int(end), int(num_properties), name_bytes.decode("utf-8", "replace")


def _binary_property_values(handle: Any, wide: bool, end: int) -> Dict[str, Any]:
    """Collect ``Properties70``/``Properties60`` entries from a node's children."""
    values: Dict[str, Any] = {}
    for name, header in _iter_nodes(handle, wide, end):
        node_end, num_properties = header
        if not name.startswith("Properties"):
            handle.seek(node_end)
            continue
        _skip_properties(handle, num_properties)
        for child_name, child_header in _iter_nodes(handle, wide, node_end):
            child_end, child_num_properties = child_header
            if child_name in ("P", "Property"):
                _collect_entry(values, _read_properties(handle, child_num_properties))
            else:
                _skip_properties(handle, child_num_properties)
            handle.seek(child_end)
        handle.seek(node_end)
    return values


def _collect_entry(values: Dict[str, Any], entries: List[Any]) -> None:
    if len(entries) < 2 or not isinstance(entries[0], str):
        return
    key = entries[0]
    if key not in _TIME_SETTING_KEYS:
        return
    value = _as_number(entries[-1])
    if value is not None:
        values[key] = value


def _read_properties(handle: Any, count: int) -> List[Any]:
    return [_read_property(handle) for _ in range(count)]


def _skip_properties(handle: Any, count: int) -> None:
    for _ in range(count):
        _read_property(handle)


def _read_property(handle: Any) -> Any:
    """Read one binary FBX property value, skipping payloads we do not need."""
    code = handle.read(1)
    if not code:
        raise _FbxReadError("truncated property type")
    if code == b"C":
        return handle.read(1) == b"\x01"
    if code in _SCALAR_PROPERTY_TYPES:
        fmt = _SCALAR_PROPERTY_TYPES[code]
        size = struct.calcsize(fmt)
        data = handle.read(size)
        if len(data) != size:
            raise _FbxReadError("truncated scalar property")
        return struct.unpack(fmt, data)[0]
    if code in (b"S", b"R"):
        raw_length = handle.read(4)
        if len(raw_length) != 4:
            raise _FbxReadError("truncated string length")
        length = struct.unpack("<I", raw_length)[0]
        data = handle.read(length)
        if len(data) != length:
            raise _FbxReadError("truncated string property")
        return data.decode("utf-8", "replace") if code == b"S" else data
    if code in _ARRAY_PROPERTY_TYPES:
        head = handle.read(12)
        if len(head) != 12:
            raise _FbxReadError("truncated array header")
        handle.seek(struct.unpack("<III", head)[2], 1)
        return None
    raise _FbxReadError("unknown property type {!r}".format(code))


def _scan_ascii_global_settings(text: str) -> Dict[str, Any]:
    """Collect time settings from the ``GlobalSettings`` block of an ASCII FBX."""
    values: Dict[str, Any] = {}
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if not inside:
            if stripped.startswith("GlobalSettings:"):
                inside = True
            continue
        if stripped.startswith("}"):
            break
        if stripped and not line[:1].isspace():
            break  # Next top-level node: GlobalSettings ended without a brace line.
        match = _ASCII_PROPERTY_RE.match(line)
        if match is None:
            continue
        key, rest = match.group(1), match.group(2)
        if key not in _TIME_SETTING_KEYS:
            continue
        value = _parse_ascii_number(rest.rsplit(",", 1)[-1])
        if value is not None:
            values[key] = value
    return values


def _parse_ascii_number(raw: str) -> Optional[float]:
    token = raw.strip().strip('"').strip()
    try:
        return float(token)
    except ValueError:
        return None
