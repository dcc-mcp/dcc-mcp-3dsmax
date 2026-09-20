"""Contract tests for the external .max file tools in the 3dsmax-scene skill.

Follows the bundled-skill testing convention: ``pymxs`` is faked with plain
Python objects, so every path here runs without a 3ds Max host.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dcc_mcp_3dsmax._executor import run_skill_script  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scene"
TOOLS_YAML = SKILL_DIR / "tools.yaml"


class _Node:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle


class _FakeRuntime:
    """Stands in for ``pymxs.runtime`` with an external-file reader."""

    def __init__(
        self,
        object_names=None,
        *,
        with_reader=True,
        with_version=True,
        with_is_max_file=True,
        with_readback=True,
    ):
        self.maxFileName = "working.max"
        self.maxFilePath = "C:/scenes/"
        self.objects = [_Node("existing_box", 1)]
        self.units = types.SimpleNamespace(SystemType="centimeters", DisplayType="metric")
        self.renderers = types.SimpleNamespace(current="Arnold")
        self._dirty = False
        self._next_handle = 100
        self.merged = []
        self.last_merged_nodes = []
        self.file_object_names = dict(object_names or {})
        self.is_max_file_results = {}
        self.object_names_raises = {}
        self.reject_quiet_flag = False
        self.object_names_calls = []
        self.readback_calls = 0
        self.readback_raises = False
        self.readback_ok_calls = 0
        if with_reader:
            self.getMAXFileObjectNames = self._get_max_file_object_names
        if with_version:
            self.getMAXFileVersion = self._get_max_file_version
        if with_is_max_file:
            self.isMaxFile = self._is_max_file
        if with_readback:
            self.getLastMergedNodes = self._get_last_merged_nodes

    # ── scene status surface ──
    def getSaveRequired(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._dirty

    def Name(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
        return "#{}".format(value)

    # ── external file surface ──
    def _is_max_file(self, file_path):
        return self.is_max_file_results.get(str(file_path), True)

    def _get_max_file_version(self, file_path):
        return "2024 - 26.0"

    def _get_last_merged_nodes(self):
        self.readback_calls += 1
        if self.readback_raises and self.readback_calls > self.readback_ok_calls:
            raise RuntimeError("readback is not available")
        return list(self.last_merged_nodes)

    def _get_max_file_object_names(self, file_path, *args, **kwargs):
        self.object_names_calls.append((str(file_path), args, kwargs))
        if self.reject_quiet_flag and kwargs:
            raise TypeError("quiet is not a supported keyword")
        raises = self.object_names_raises.get(str(file_path))
        if raises is not None:
            raise raises
        return list(self.file_object_names.get(str(file_path), []))

    def mergeMAXFile(self, file_path, *args, **kwargs):  # noqa: N802 - mirrors pymxs runtime naming.
        self.merged.append((file_path, args, kwargs))
        names = [str(item) for item in args[0]] if args and isinstance(args[0], list) else ["merged_object"]
        nodes = []
        for name in names:
            self._next_handle += 1
            node = _Node(name, self._next_handle)
            self.objects.append(node)
            nodes.append(node)
        self.last_merged_nodes = nodes
        self._dirty = True
        return True


def _install(monkeypatch, runtime):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def _write_max(tmp_path, name="asset.max", payload=b"fake max fixture"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _run(script, params):
    return run_skill_script(str(SKILL_DIR / script), params)


# ── inspect_max_file ────────────────────────────────────────────────────


def test_inspect_max_file_reports_objects_and_metadata(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero_mesh", "prop_crate", "cam_shot01"]})
    # scene_status resolves the current file from the fake's scene dir.
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_inspect_max_file.py", {"file_path": str(source)})

    assert result["success"] is True
    assert result["data"]["status"] == "ok"
    assert result["data"]["object_count"] == 3
    assert result["data"]["object_names"] == ["hero_mesh", "prop_crate", "cam_shot01"]
    assert result["data"]["max_file_version"] == "2024 - 26.0"
    assert result["data"]["metadata_source"] == "host"
    assert result["data"]["file_size_bytes"] == len(b"fake max fixture")
    assert result["data"]["truncated"] is False


def test_inspect_max_file_filters_and_truncates_names(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["prop_a", "prop_b", "hero"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_inspect_max_file.py", {"file_path": str(source), "name_filter": "prop", "limit": 1})

    assert result["success"] is True
    assert result["data"]["object_names"] == ["prop_a"]
    assert result["data"]["matched_count"] == 2
    assert result["data"]["object_count"] == 3
    assert result["data"]["truncated"] is True


def test_inspect_max_file_fails_on_missing_file(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    _install(monkeypatch, runtime)

    result = _run("action_inspect_max_file.py", {"file_path": str(tmp_path / "absent.max")})

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "read"
    assert result["data"]["failure_reason"] == "scene_file_not_found"


def test_inspect_max_file_rejects_non_max_paths(monkeypatch, tmp_path):
    other = tmp_path / "notes.txt"
    other.write_bytes(b"not a scene")
    _install(monkeypatch, _FakeRuntime())

    result = _run("action_inspect_max_file.py", {"file_path": str(other)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "absolute_max_path_required"


def test_inspect_max_file_fails_when_the_host_rejects_the_file(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.is_max_file_results[str(source)] = False
    _install(monkeypatch, runtime)

    result = _run("action_inspect_max_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_max_file"
    assert result["data"]["file"]["object_count"] == 0


def test_inspect_max_file_fails_when_the_reader_raises(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.object_names_raises[str(source)] = RuntimeError("corrupt scene file")
    _install(monkeypatch, runtime)

    result = _run("action_inspect_max_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "object_names_read_failed"
    # A real read failure must not read the same file twice without quiet=True.
    assert runtime.object_names_calls == [(str(source), (), {"quiet": True})]


def test_inspect_max_file_fails_without_a_host_reader(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    _install(monkeypatch, _FakeRuntime(with_reader=False))

    result = _run("action_inspect_max_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "external_scene_reader_unavailable"


def test_inspect_max_file_retries_without_the_quiet_flag(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.reject_quiet_flag = True
    _install(monkeypatch, runtime)

    result = _run("action_inspect_max_file.py", {"file_path": str(source)})

    assert result["success"] is True
    assert result["data"]["object_names"] == ["hero"]


def test_inspect_max_file_warns_when_the_version_reader_is_missing(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]}, with_version=False)
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_inspect_max_file.py", {"file_path": str(source)})

    assert result["success"] is True
    assert result["data"]["max_file_version"] is None
    assert result["data"]["metadata_source"] == "filesystem"
    assert any("getMAXFileVersion" in warning for warning in result["data"]["warnings"])


def test_inspect_max_file_reports_unreadable_paths(monkeypatch, tmp_path):
    from dcc_mcp_3dsmax import _max_file_io

    source = _write_max(tmp_path)
    _install(monkeypatch, _FakeRuntime({str(source): ["hero"]}))
    monkeypatch.setattr(_max_file_io.os, "access", lambda *_args, **_kwargs: False)

    result = _run("action_inspect_max_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_file_not_readable"


def test_inspect_max_file_rejects_a_bad_include_flag(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    _install(monkeypatch, _FakeRuntime({str(source): ["hero"]}))

    result = _run("action_inspect_max_file.py", {"file_path": str(source), "include_object_names": "yes"})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_include_object_names"


# ── batch_file_info ─────────────────────────────────────────────────────


def test_batch_file_info_reads_every_requested_file(monkeypatch, tmp_path):
    first = _write_max(tmp_path, "one.max")
    second = _write_max(tmp_path, "two.max")
    runtime = _FakeRuntime({str(first): ["a"], str(second): ["b", "c"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_batch_file_info.py", {"file_paths": [str(first), str(second)]})

    assert result["success"] is True
    assert result["data"]["requested_count"] == 2
    assert result["data"]["ok_count"] == 2
    assert result["data"]["failed_count"] == 0
    assert [row["object_count"] for row in result["data"]["files"]] == [1, 2]


def test_batch_file_info_fails_loudly_on_one_unreadable_file(monkeypatch, tmp_path):
    good = _write_max(tmp_path, "good.max")
    runtime = _FakeRuntime({str(good): ["a"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run(
        "action_batch_file_info.py",
        {"file_paths": [str(good), str(tmp_path / "absent.max")]},
    )

    assert result["success"] is False
    assert result["data"]["failed_count"] == 1
    assert result["data"]["partial"] is True
    assert result["data"]["failed"][0]["error_reason"] == "scene_file_not_found"
    assert result["data"]["ok_count"] == 1
    assert any("scene_file_not_found" in warning for warning in result["data"]["warnings"])


def test_batch_file_info_fails_when_every_file_is_unreadable(monkeypatch, tmp_path):
    _install(monkeypatch, _FakeRuntime())

    result = _run("action_batch_file_info.py", {"file_paths": [str(tmp_path / "absent.max")]})

    assert result["success"] is False
    assert result["data"]["partial"] is False
    assert result["data"]["ok_count"] == 0


def test_batch_file_info_rejects_unbounded_batches(monkeypatch):
    _install(monkeypatch, _FakeRuntime())

    assert _run("action_batch_file_info.py", {"file_paths": []})["data"]["failure_reason"] == "invalid_file_paths"
    from dcc_mcp_3dsmax._max_file_io import MAX_BATCH_FILES

    exactly_max = ["C:/scenes/{}.max".format(index) for index in range(MAX_BATCH_FILES)]
    _install(monkeypatch, _FakeRuntime())
    at_cap = _run("action_batch_file_info.py", {"file_paths": exactly_max})
    assert at_cap["success"] is False
    assert at_cap["data"]["requested_count"] == MAX_BATCH_FILES
    assert at_cap["data"]["failed_count"] == MAX_BATCH_FILES
    too_many = ["C:/scenes/{}.max".format(index) for index in range(MAX_BATCH_FILES + 1)]
    assert (
        _run("action_batch_file_info.py", {"file_paths": too_many})["data"]["failure_reason"] == "invalid_file_paths"
    )


def test_batch_file_info_reports_duplicate_paths(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["a"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_batch_file_info.py", {"file_paths": [str(source), str(source)]})

    assert result["success"] is True
    assert result["data"]["requested_count"] == 1
    assert result["data"]["duplicate_paths_ignored"] == 1


# ── search_max_files ────────────────────────────────────────────────────


def test_search_max_files_matches_names_across_files(monkeypatch, tmp_path):
    first = _write_max(tmp_path, "one.max")
    second = _write_max(tmp_path, "two.max")
    runtime = _FakeRuntime({str(first): ["prop_crate", "hero"], str(second): ["PROP_barrel"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run(
        "action_search_max_files.py",
        {"file_paths": [str(first), str(second)], "name_pattern": "prop"},
    )

    assert result["success"] is True
    assert result["data"]["match_count"] == 2
    assert [match["object_name"] for match in result["data"]["matches"]] == ["prop_crate", "PROP_barrel"]
    assert [row["matched_count"] for row in result["data"]["files"]] == [1, 1]


def test_search_max_files_supports_glob_mode(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["prop_crate", "prop_barrel", "hero"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run(
        "action_search_max_files.py",
        {"file_paths": [str(source)], "name_pattern": "prop_*", "match_mode": "glob"},
    )

    assert result["success"] is True
    assert result["data"]["match_count"] == 2


def test_search_max_files_fails_when_a_file_cannot_be_read(monkeypatch, tmp_path):
    good = _write_max(tmp_path, "good.max")
    runtime = _FakeRuntime({str(good): ["prop_crate"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run(
        "action_search_max_files.py",
        {"file_paths": [str(good), str(tmp_path / "absent.max")], "name_pattern": "prop"},
    )

    assert result["success"] is False
    assert result["data"]["failed_count"] == 1
    assert result["data"]["partial"] is True
    assert result["data"]["match_count"] == 1


def test_search_max_files_rejects_an_empty_pattern(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    _install(monkeypatch, _FakeRuntime({str(source): ["prop"]}))

    result = _run("action_search_max_files.py", {"file_paths": [str(source)], "name_pattern": "  "})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_search_options"


def test_search_max_files_rejects_an_unknown_match_mode(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    _install(monkeypatch, _FakeRuntime({str(source): ["prop"]}))

    result = _run(
        "action_search_max_files.py",
        {"file_paths": [str(source)], "name_pattern": "prop", "match_mode": "regex"},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_search_options"


def test_search_max_files_truncates_matches(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["prop_a", "prop_b", "prop_c"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_search_max_files.py", {"file_paths": [str(source)], "name_pattern": "prop", "limit": 2})

    assert result["success"] is True
    assert result["data"]["match_count"] == 3
    assert result["data"]["returned_count"] == 2
    assert result["data"]["truncated"] is True


# ── merge_from_file ─────────────────────────────────────────────────────


def test_merge_from_file_merges_every_source_object_by_default(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero", "prop"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source)})

    assert result["success"] is True
    assert result["data"]["selection_mode"] == "all"
    assert runtime.merged[0][1][0] == ["hero", "prop"]
    assert runtime.merged[0][2] == {"quiet": True}
    assert result["data"]["merged_count"] == 2
    assert result["data"]["verified"] is True


def test_merge_from_file_resolves_exact_names_and_reports_unresolved(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero", "prop"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source), "object_names": ["hero", "ghost"]})

    assert result["success"] is True
    assert result["data"]["selection_mode"] == "object_names"
    assert runtime.merged[0][1][0] == ["hero"]
    assert result["data"]["unresolved_object_names"] == ["ghost"]
    assert any("ghost" in warning for warning in result["data"]["warnings"])


def test_merge_from_file_require_all_fails_before_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run(
        "action_merge_from_file.py",
        {"file_path": str(source), "object_names": ["hero", "ghost"], "require_all": True},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "unresolved_object_names"
    assert runtime.merged == []


def test_merge_from_file_selects_by_pattern(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["prop_a", "prop_b", "hero"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run(
        "action_merge_from_file.py",
        {"file_path": str(source), "name_pattern": "prop_*", "match_mode": "glob"},
    )

    assert result["success"] is True
    assert result["data"]["selection_mode"] == "name_pattern"
    assert runtime.merged[0][1][0] == ["prop_a", "prop_b"]


def test_merge_from_file_fails_when_nothing_matches(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source), "name_pattern": "nope"})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "no_source_objects_matched"
    assert runtime.merged == []


def test_merge_from_file_rejects_two_selections_at_once(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    _install(monkeypatch, _FakeRuntime({str(source): ["hero"]}))

    result = _run(
        "action_merge_from_file.py",
        {"file_path": str(source), "object_names": ["hero"], "name_pattern": "hero"},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_merge_selection"


def test_merge_from_file_rejects_unsupported_policies_before_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source), "duplicate_names": "overwrite"})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_merge_options"
    assert runtime.merged == []


def test_merge_from_file_fails_closed_without_readback(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    runtime.mergeMAXFile = lambda _file_path, *_args, **_kwargs: True  # type: ignore[method-assign]
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_merge_readback_mismatch"
    assert result["data"]["verified"] is False


def test_merge_from_file_fails_on_a_missing_source_file(monkeypatch, tmp_path):
    _install(monkeypatch, _FakeRuntime())

    result = _run("action_merge_from_file.py", {"file_path": str(tmp_path / "absent.max")})

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "precondition"
    assert result["data"]["failure_reason"] == "scene_file_not_found"


# ── shared helper and tool contract ─────────────────────────────────────


def test_batch_readers_warn_above_the_recommended_batch_size(monkeypatch, tmp_path):
    from dcc_mcp_3dsmax._max_file_io import RECOMMENDED_BATCH_FILES

    paths = []
    names = {}
    for index in range(RECOMMENDED_BATCH_FILES + 1):
        path = _write_max(tmp_path, "batch_{}.max".format(index))
        paths.append(str(path))
        names[str(path)] = ["prop_a"]
    runtime = _FakeRuntime(names)
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    info = _run("action_batch_file_info.py", {"file_paths": paths})
    search = _run("action_search_max_files.py", {"file_paths": paths, "name_pattern": "prop"})

    for result in (info, search):
        assert result["success"] is True, result
        assert any("split it into batches" in warning for warning in result["data"]["warnings"]), result["data"]

    small = _run("action_batch_file_info.py", {"file_paths": paths[:RECOMMENDED_BATCH_FILES]})
    assert small["data"]["warnings"] == []
    from dcc_mcp_3dsmax._max_file_io import match_object_names

    names = ["PROP_One", "prop_two", "hero"]
    assert match_object_names(names, "prop", case_sensitive=True) == ["prop_two"]
    assert match_object_names(names, "prop", case_sensitive=False) == ["PROP_One", "prop_two"]
    assert match_object_names(names, "PROP_*", match_mode="glob", case_sensitive=True) == ["PROP_One"]
    assert match_object_names(names, "prop_*", match_mode="glob", case_sensitive=False) == ["PROP_One", "prop_two"]


def test_merge_from_file_refuses_a_host_without_readback_before_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]}, with_readback=False)
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "precondition"
    assert result["data"]["failure_reason"] == "merge_readback_unavailable"
    assert result["data"]["scene_modified"] is False
    assert runtime.merged == []


def test_merge_file_refuses_a_host_without_readback_before_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]}, with_readback=False)
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_merge_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "merge_readback_unavailable"
    assert runtime.merged == []


def test_merge_from_file_refuses_a_failing_readback_probe_before_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    runtime.readback_raises = True  # the entry point exists but raises when called
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "precondition"
    assert result["data"]["failure_reason"] == "merge_readback_unavailable"
    assert result["data"]["scene_modified"] is False
    assert runtime.merged == []


def test_merge_file_refuses_a_failing_readback_probe_before_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    runtime.readback_raises = True
    _install(monkeypatch, runtime)

    result = _run("action_merge_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "merge_readback_unavailable"
    assert runtime.merged == []


def test_merge_from_file_reports_a_readback_failure_after_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    runtime.readback_raises = True
    runtime.readback_ok_calls = 1  # preflight succeeds, the post-merge read fails
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_stage"] == "verify"
    assert result["data"]["failure_reason"] == "scene_merge_readback_mismatch"
    assert result["data"]["verified"] is False
    assert result["data"]["scene_modified"] is True
    assert result["data"]["readback_error"]
    assert runtime.merged, "the merge did happen"
    assert any("undo_last" in warning for warning in result["data"]["warnings"])


def test_merge_file_reports_a_readback_failure_after_merging(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    runtime.readback_raises = True
    runtime.readback_ok_calls = 1
    _install(monkeypatch, runtime)

    result = _run("action_merge_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_merge_readback_mismatch"
    assert result["data"]["scene_modified"] is True
    assert result["data"]["readback_error"]
    assert any("undo_last" in warning for warning in result["data"]["warnings"])


def test_merge_from_file_flags_an_unverified_scene_change(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    runtime.mergeMAXFile = lambda _file_path, *_args, **_kwargs: True  # type: ignore[method-assign]
    _install(monkeypatch, runtime)

    result = _run("action_merge_from_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_merge_readback_mismatch"
    assert result["data"]["scene_modified"] is True
    assert any("undo_last" in warning for warning in result["data"]["warnings"])


def test_merge_file_preserves_the_validity_probe_reason(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    _install(monkeypatch, _FakeRuntime({str(source): ["hero"]}, with_is_max_file=False))

    result = _run("action_merge_file.py", {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "is_max_file_unavailable"


def test_merge_file_still_uses_the_shared_conflict_policies(monkeypatch, tmp_path):
    source = _write_max(tmp_path)
    runtime = _FakeRuntime({str(source): ["hero"]})
    runtime.maxFilePath = str(tmp_path)
    _install(monkeypatch, runtime)

    result = _run("action_merge_file.py", {"file_path": str(source)})

    assert result["success"] is True
    assert runtime.merged[0][1] == ("#autoRenameDups", "#renameMtlDups", "#neverReparent")
    assert result["data"]["verified"] is True


def test_external_max_file_tools_are_typed_main_thread_contracts():
    tools = yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    new_tools = {
        "inspect_max_file",
        "batch_file_info",
        "search_max_files",
        "merge_from_file",
    }
    assert new_tools.issubset(by_name)

    from dcc_mcp_3dsmax._undo_utils import VALID_GRANULARITIES

    for name in new_tools:
        tool = by_name[name]
        assert tool["affinity"] == "main", name
        assert tool["enforce_thread_affinity"] is True, name
        assert tool["input_schema"]["additionalProperties"] is False, name
        assert (SKILL_DIR / tool["source_file"]).is_file(), name
        for key in ("side_effects", "produces", "risk", "intent", "annotations", "tool_role"):
            assert key in tool, (name, key)

    for name in ("inspect_max_file", "batch_file_info", "search_max_files"):
        assert by_name[name]["read_only"] is True, name
        assert by_name[name]["annotations"]["read_only_hint"] is True, name

    merge_tool = by_name["merge_from_file"]
    assert merge_tool["read_only"] is False
    assert merge_tool["job_strategy"] == "monolithic"
    assert merge_tool["undo"]["supported"] is True
    assert merge_tool["undo"]["granularity"] in VALID_GRANULARITIES
    assert merge_tool["undo"]["notes"].strip()
