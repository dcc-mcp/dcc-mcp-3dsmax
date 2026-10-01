"""Tests for the FBX timeline inspection used by geometry imports."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _fbx_time as fbx_time  # noqa: E402

KTIME = fbx_time.KTIME_UNITS_PER_SECOND


# --------------------------------------------------------------------- helpers
class _FakeTime:
    def __init__(self, frame: float) -> None:
        self.frame = float(frame)


class _FakeInterval:
    def __init__(self, start: float, end: float) -> None:
        self.start = _FakeTime(start)
        self.end = _FakeTime(end)


class _FakeRuntime:
    """Minimal stand-in for the pymxs runtime timeline surface."""

    def __init__(self, frame_rate: float = 30.0, start: float = 0, end: float = 100) -> None:
        self.frameRate = frame_rate
        self.animationRange = _FakeInterval(start, end)
        self.animationRangeStart = start
        self.animationRangeEnd = end
        self.interval_calls = []

    def Interval(self, start, end):  # noqa: N802 - mirrors pymxs runtime naming.
        self.interval_calls.append((start, end))
        return _FakeInterval(start, end)


def _string_property(value: str) -> bytes:
    raw = value.encode("utf-8")
    return b"S" + struct.pack("<I", len(raw)) + raw


def _int_property(value: int) -> bytes:
    return b"I" + struct.pack("<i", value)


def _long_property(value: int) -> bytes:
    return b"L" + struct.pack("<q", value)


def _double_property(value: float) -> bytes:
    return b"D" + struct.pack("<d", value)


class _FbxWriter:
    """Builds binary FBX node records with the absolute offsets the format uses."""

    def __init__(self, wide: bool = True) -> None:
        self.wide = wide
        # Node end offsets are absolute file positions, so reserve the 27 byte
        # file header up front and keep buffer positions equal to file offsets.
        self.buffer = bytearray(27)
        self.fmt = "<QQQB" if wide else "<IIIB"

    def node(self, name: str, properties: bytes = b"", count: int = 0, children=None) -> None:
        start = len(self.buffer)
        name_bytes = name.encode("utf-8")
        self.buffer += struct.pack(self.fmt, 0, count, len(properties), len(name_bytes))
        self.buffer += name_bytes
        self.buffer += properties
        if children is not None:
            children()
        struct.pack_into(self.fmt, self.buffer, start, len(self.buffer), count, len(properties), len(name_bytes))

    def bytes(self, version: int) -> bytes:
        self.buffer[0:27] = b"Kaydara FBX Binary  \x00" + b"\x1a\x00" + struct.pack("<I", version)
        return bytes(self.buffer)


def _property_entry(writer: _FbxWriter, key: str, value: bytes) -> None:
    """Append a ``P`` node holding one ``Properties70`` entry."""
    properties = _string_property(key) + _string_property("") + _string_property("") + _string_property("")
    writer.node("P", properties + value, count=5)


def _binary_fbx(path: Path, *, time_mode: int, start: int, stop: int, custom: float = -1.0, version: int = 7700) -> Path:
    """Write a minimal binary FBX whose GlobalSettings carry time settings."""
    writer = _FbxWriter(wide=version >= 7500)

    def entries() -> None:
        _property_entry(writer, "TimeMode", _int_property(time_mode))
        _property_entry(writer, "TimeSpanStart", _long_property(start))
        _property_entry(writer, "TimeSpanStop", _long_property(stop))
        _property_entry(writer, "CustomFrameRate", _double_property(custom))

    def global_settings() -> None:
        writer.node("Version", _int_property(1000), count=1)
        writer.node("Properties70", b"", count=0, children=entries)

    writer.node("FBXHeaderExtension")
    writer.node("GlobalSettings", b"", count=0, children=global_settings)
    path.write_bytes(writer.bytes(version))
    return path


_ASCII_TEMPLATE = """; FBX 7.7.0 project file
FBXHeaderExtension:  {{
    FBXHeaderVersion: 1003
    FBXVersion: 7700
}}
GlobalSettings:  {{
    Version: 1000
    Properties70:  {{
        P: "UpAxis", "int", "Integer", "",1
        P: "TimeMode", "enum", "", "",{time_mode}
        P: "TimeSpanStart", "KTime", "Time", "",{start}
        P: "TimeSpanStop", "KTime", "Time", "",{stop}
        P: "CustomFrameRate", "double", "Number", "",{custom}
    }}
}}
Objects:  {{
}}
"""


def _ascii_fbx(path: Path, *, time_mode: int, start: int, stop: int, custom: float = -1.0) -> Path:
    path.write_text(
        _ASCII_TEMPLATE.format(time_mode=time_mode, start=start, stop=stop, custom=custom),
        encoding="utf-8",
    )
    return path


def _span(start_frame: int, end_frame: int, frame_rate: float = 24.0) -> tuple:
    """Return FBX KTime values for a frame range at the given rate."""
    unit = KTIME / frame_rate
    return int(round(start_frame * unit)), int(round(end_frame * unit))


# ------------------------------------------------------------------- parsing
def test_binary_fbx_reports_source_frame_rate_and_range(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)

    settings = fbx_time.read_fbx_time_settings(path)

    assert settings["encoding"] == "binary"
    assert settings["frame_rate"] == 24.0
    assert settings["time_mode_name"] == "eFrames24"
    assert settings["start_frame"] == 1
    assert settings["end_frame"] == 24
    assert settings["has_animation"] is True


def test_ascii_fbx_reports_source_frame_rate_and_range(tmp_path):
    start, stop = _span(1, 48, 30.0)
    path = _ascii_fbx(tmp_path / "clip.fbx", time_mode=6, start=start, stop=stop)

    settings = fbx_time.read_fbx_time_settings(path)

    assert settings["encoding"] == "ascii"
    assert settings["frame_rate"] == 30.0
    assert (settings["start_frame"], settings["end_frame"]) == (1, 48)


@pytest.mark.parametrize(
    "time_mode,expected_rate",
    [
        (6, 30.0),  # eFrames30
        (10, 25.0),  # ePAL
        (11, 24.0),  # eFrames24
        (3, 60.0),  # eFrames60
        (9, 30000.0 / 1001.0),  # eNTSCFullFrame
    ],
)
def test_time_modes_map_to_frame_rates(tmp_path, time_mode, expected_rate):
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=time_mode, start=0, stop=KTIME)

    settings = fbx_time.read_fbx_time_settings(path)

    assert settings["frame_rate"] == pytest.approx(expected_rate)


def test_custom_time_mode_uses_custom_frame_rate(tmp_path):
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=14, start=0, stop=KTIME, custom=23.976)

    settings = fbx_time.read_fbx_time_settings(path)

    assert settings["time_mode_name"] == "eCustom"
    assert settings["frame_rate"] == pytest.approx(23.976)


def test_default_time_mode_has_no_frame_rate(tmp_path):
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=0, start=0, stop=KTIME)

    settings = fbx_time.read_fbx_time_settings(path)

    assert settings["frame_rate"] is None
    assert settings["time_mode_name"] == "eDefaultMode"


def test_non_fbx_file_returns_none(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not an fbx", encoding="utf-8")

    assert fbx_time.read_fbx_time_settings(path) is None


def test_fbx_without_global_settings_returns_none(tmp_path):
    path = tmp_path / "clip.fbx"
    path.write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 6000))

    assert fbx_time.read_fbx_time_settings(path) is None


def test_truncated_binary_fbx_returns_none(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    path.write_bytes(path.read_bytes()[:40])

    assert fbx_time.read_fbx_time_settings(path) is None


def test_missing_file_returns_none(tmp_path):
    assert fbx_time.read_fbx_time_settings(tmp_path / "missing.fbx") is None


def test_ascii_property_with_a_trailing_comment_is_parsed(tmp_path):
    """ASCII FBX allows `;` comments after the value on a P: line."""
    path = tmp_path / "commented.fbx"
    path.write_text(
        "; FBX 7.7.0 project file\n"
        "GlobalSettings:  {\n"
        "    Version: 1000\n"
        '    Properties70:  {\n'
        '        P: "TimeMode", "enum", "", "",11    ; film speed\n'
        '        P: "TimeSpanStart", "KTime", "Time", "",1924423250    ; start\n'
        '        P: "TimeSpanStop", "KTime", "Time", "",46186158000    ; stop\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    settings = fbx_time.read_fbx_time_settings(path)

    assert settings["frame_rate"] == 24.0
    assert (settings["start_frame"], settings["end_frame"]) == (1, 24)


def test_missing_span_and_unparsable_span_are_reported_differently(tmp_path):
    """Two different causes must not collapse into the same `None-None` text."""
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)
    # TimeMode present, TimeSpan* absent: the file declares no range at all.
    writer = _FbxWriter()

    def entries() -> None:
        _property_entry(writer, "TimeMode", _int_property(11))

    def global_settings() -> None:
        writer.node("Properties70", b"", count=0, children=entries)

    writer.node("GlobalSettings", b"", count=0, children=global_settings)
    absent = tmp_path / "absent_span.fbx"
    absent.write_bytes(writer.bytes(7700))

    missing = fbx_time.apply_source_timeline(runtime, absent, {"timeline_mode": "source"})
    unparsable = fbx_time.apply_source_timeline(runtime, _unparsable_span_file(tmp_path), {"timeline_mode": "source"})

    assert any("declares no animation range" in w for w in missing["warnings"])
    assert not any("cannot parse" in w for w in missing["warnings"])
    assert any("cannot parse" in w for w in unparsable["warnings"])
    assert not any("None-None" in w for w in missing["warnings"] + unparsable["warnings"])


def _unparsable_span_file(tmp_path: Path) -> Path:
    """Write an FBX whose TimeSpan values are present but not numbers."""
    writer = _FbxWriter()

    def entries() -> None:
        _property_entry(writer, "TimeMode", _int_property(11))
        _property_entry(writer, "TimeSpanStart", _string_property("not-a-number"))
        _property_entry(writer, "TimeSpanStop", _string_property("not-a-number"))

    def global_settings() -> None:
        writer.node("Properties70", b"", count=0, children=entries)

    writer.node("GlobalSettings", b"", count=0, children=global_settings)
    path = tmp_path / "unparsable_span.fbx"
    path.write_bytes(writer.bytes(7700))
    return path


def test_zero_length_span_reports_no_animation(tmp_path):
    path = _binary_fbx(tmp_path / "static.fbx", time_mode=11, start=0, stop=0)

    settings = fbx_time.read_fbx_time_settings(path)

    assert settings["has_animation"] is False
    assert settings["start_frame"] == 0
    assert settings["end_frame"] == 0


# ---------------------------------------------------------------- scene state
def test_scene_time_settings_reads_interval_frames():
    runtime = _FakeRuntime(frame_rate=30.0, start=1, end=100)

    assert fbx_time.scene_time_settings(runtime) == {
        "frame_rate": 30.0,
        "frame_start": 1,
        "frame_end": 100,
    }


# ------------------------------------------------------------------ alignment
def test_source_mode_adopts_source_timeline(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert report["applied"] is True
    assert report["warnings"] == []
    assert runtime.frameRate == 24.0
    assert runtime.interval_calls == [(1, 24)]
    assert report["scene_after"] == {"frame_rate": 24.0, "frame_start": 1, "frame_end": 24}
    assert report["frame_rate_matches"] is True
    assert report["range_covers_source"] is True


def test_frame_rate_is_set_before_the_animation_range(tmp_path):
    """3ds Max stores the range in ticks, so setting it first lands elsewhere."""
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    seen = []

    class _RecordingRuntime(_FakeRuntime):
        @property
        def frameRate(self):  # noqa: N802 - mirrors pymxs runtime naming.
            return self._frame_rate

        @frameRate.setter
        def frameRate(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
            seen.append(("frame_rate", value))
            self._frame_rate = float(value)

        @property
        def animationRange(self):  # noqa: N802 - mirrors pymxs runtime naming.
            return self._range

        @animationRange.setter
        def animationRange(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
            seen.append(("range", value.start.frame, value.end.frame))
            self._range = value

    runtime = _RecordingRuntime(frame_rate=30.0, start=0, end=100)
    del seen[:]  # Drop the assignments made while constructing the fake runtime.
    fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert [entry[0] for entry in seen] == ["frame_rate", "range"]


def test_union_mode_widens_the_range_instead_of_shrinking_it(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=24.0, start=0, end=200)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "union"})

    assert report["scene_after"] == {"frame_rate": 24.0, "frame_start": 0, "frame_end": 200}
    assert report["range_covers_source"] is True
    assert report["warnings"] == []


def test_off_mode_leaves_the_scene_and_warns_about_the_mismatch(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "off"})

    assert report["applied"] is False
    assert runtime.frameRate == 30.0
    assert runtime.interval_calls == []
    assert report["frame_rate_matches"] is False
    assert any("do not correspond 1:1" in warning for warning in report["warnings"])


def test_unknown_source_rate_warns_and_leaves_the_scene(tmp_path):
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=0, start=0, stop=KTIME)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert report["applied"] is False
    assert runtime.frameRate == 30.0
    assert any("Could not read the FBX source time settings" in warning for warning in report["warnings"])


def test_unreadable_file_warns_instead_of_raising(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not an fbx", encoding="utf-8")
    runtime = _FakeRuntime()

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert report["source"] is None
    assert report["applied"] is False
    assert len(report["warnings"]) == 1


def test_animation_disabled_leaves_the_scene(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(
        runtime, path, {"timeline_mode": "source", "include_animation": False}
    )

    assert report["applied"] is False
    assert runtime.frameRate == 30.0
    assert any("include_animation=false" in warning for warning in report["warnings"])


def test_ntsc_rate_is_snapped_to_a_rate_3ds_max_holds(tmp_path):
    start, stop = _span(1, 100, 30000.0 / 1001.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=9, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=24.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert runtime.frameRate == 30.0
    assert any("cannot represent" in warning for warning in report["warnings"])


def test_unsupported_mode_falls_back_to_off(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "nonsense"})

    assert report["mode"] == "off"
    assert runtime.frameRate == 30.0
    assert any("Unsupported timeline_mode" in warning for warning in report["warnings"])


# ------------------------------------------------------------ malformed input
# This parser runs on the 3ds Max main thread before importFile, so a node
# offset that does not advance must be rejected, never followed. These cases
# hung before the forward-progress guard existed; the timeout markers keep them
# from hanging a CI worker instead if the guard ever regresses.
def _self_referencing_node(path: Path, *, version: int = 7700) -> Path:
    """Write an FBX whose top-level node end offset points at its own header."""
    writer = _FbxWriter(wide=version >= 7500)

    def pinned() -> None:
        # Patch the node's own end offset back to where its header starts.
        struct.pack_into(writer.fmt, writer.buffer, 27, 27, 0, 0, len("GlobalSettings"))

    writer.node("GlobalSettings", b"", count=0, children=pinned)
    path.write_bytes(writer.bytes(version))
    return path


def _cyclic_child_node(path: Path, *, version: int = 7700) -> Path:
    """Write an FBX whose GlobalSettings child points back at Properties70."""
    writer = _FbxWriter(wide=version >= 7500)

    def entries() -> None:
        _property_entry(writer, "TimeMode", _int_property(11))

    def cyclic_properties() -> None:
        start = len(writer.buffer)
        name = b"Properties70"
        writer.buffer += struct.pack(writer.fmt, 0, 0, 0, len(name))
        writer.buffer += name
        # End the child where it started, so walking it never progresses.
        struct.pack_into(writer.fmt, writer.buffer, start, start, 0, 0, len(name))

    def global_settings() -> None:
        writer.node("Properties70", b"", count=0, children=entries)
        cyclic_properties()

    writer.node("GlobalSettings", b"", count=0, children=global_settings)
    path.write_bytes(writer.bytes(version))
    return path


@pytest.mark.timeout(5)
@pytest.mark.parametrize("version", [7700, 6000])
def test_node_offset_that_does_not_advance_returns_none(tmp_path, version):
    path = _self_referencing_node(tmp_path / "pinned.fbx", version=version)

    assert fbx_time.read_fbx_time_settings(path) is None


@pytest.mark.timeout(5)
def test_cyclic_child_offset_returns_none(tmp_path):
    path = _cyclic_child_node(tmp_path / "cyclic.fbx")

    assert fbx_time.read_fbx_time_settings(path) is None


@pytest.mark.timeout(5)
def test_malformed_offsets_never_reach_the_scene_timeline(tmp_path):
    """The public entry point must degrade to a warning, not hang or raise."""
    path = _self_referencing_node(tmp_path / "pinned.fbx")
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert runtime.frameRate == 30.0
    assert any("Could not read the FBX source time settings" in w for w in report["warnings"])


def test_static_file_leaves_the_scene_and_warns(tmp_path):
    path = _binary_fbx(tmp_path / "static.fbx", time_mode=11, start=0, stop=0)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert report["applied"] is False
    assert runtime.frameRate == 30.0
    assert any("zero-length animation range" in warning for warning in report["warnings"])


def test_verify_re_applies_the_timeline_when_the_importer_reset_it(tmp_path):
    """A report written before the import runs must not over-claim alignment."""
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})
    # Stand in for an importer that puts the scene back on its own defaults.
    runtime.frameRate = 30.0
    runtime.animationRange = _FakeInterval(0, 100)

    verified = fbx_time.verify_timeline_after_import(runtime, report)

    assert verified["scene_after_import"] == {"frame_rate": 30.0, "frame_start": 0, "frame_end": 100}
    assert verified["scene_after"] == {"frame_rate": 24.0, "frame_start": 1, "frame_end": 24}
    assert verified["frame_rate_matches"] is True
    assert any("re-applied" in warning for warning in verified["warnings"])


def test_verify_keeps_quiet_when_the_importer_left_the_timeline_alone(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    verified = fbx_time.verify_timeline_after_import(runtime, report)

    assert verified["warnings"] == []
    assert verified["scene_after_import"] == verified["scene_after"]


def test_verify_reports_the_real_scene_state_for_an_unapplied_timeline(tmp_path):
    """Off mode must not report a pre-import snapshot as the current state."""
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "off"})
    warnings_before = list(report["warnings"])

    verified = fbx_time.verify_timeline_after_import(runtime, report)

    assert runtime.frameRate == 30.0  # Nothing was applied.
    assert verified["scene_after_import"] == {"frame_rate": 30.0, "frame_start": 0, "frame_end": 100}
    assert verified["scene_after"] == verified["scene_after_import"]
    assert verified["warnings"] == warnings_before  # Nothing changed, nothing to add.


def test_verify_reports_an_importer_change_even_in_off_mode(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)
    runtime = _FakeRuntime(frame_rate=30.0, start=0, end=100)

    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "off"})
    runtime.frameRate = 25.0
    runtime.animationRange = _FakeInterval(5, 55)

    verified = fbx_time.verify_timeline_after_import(runtime, report)

    assert runtime.frameRate == 25.0  # off mode still leaves the scene alone.
    assert verified["scene_after"] == {"frame_rate": 25.0, "frame_start": 5, "frame_end": 55}
    assert verified["scene_after_import"] == verified["scene_after"]
    assert any("left it there" in warning for warning in verified["warnings"])


def test_runtime_failure_is_reported_as_a_warning(tmp_path):
    start, stop = _span(1, 24, 24.0)
    path = _binary_fbx(tmp_path / "clip.fbx", time_mode=11, start=start, stop=stop)

    class _BrokenRuntime(_FakeRuntime):
        locked = False

        @property
        def frameRate(self):  # noqa: N802 - mirrors pymxs runtime naming.
            return 30.0

        @frameRate.setter
        def frameRate(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
            if _BrokenRuntime.locked:
                raise RuntimeError("read-only frame rate")

    runtime = _BrokenRuntime()
    _BrokenRuntime.locked = True
    report = fbx_time.apply_source_timeline(runtime, path, {"timeline_mode": "source"})

    assert report["applied"] is False
    assert any("Could not set the scene frame rate" in warning for warning in report["warnings"])
