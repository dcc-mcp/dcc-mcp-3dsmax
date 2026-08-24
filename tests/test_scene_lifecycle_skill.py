"""Contract tests for typed 3ds Max scene lifecycle tools."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dcc_mcp_3dsmax._executor import run_skill_script

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scene"


class _FakeRuntime:
    def __init__(self) -> None:
        self.maxFileName = "working.max"
        self.maxFilePath = "C:/scenes/"
        self.objects = [types.SimpleNamespace(name="unsaved_box", handle=1)]
        self.units = types.SimpleNamespace(SystemType="centimeters", DisplayType="metric")
        self.renderers = types.SimpleNamespace(current="Arnold")
        self.loaded = []
        self.merged = []
        self.last_merged_nodes = []
        self.reset_calls = []
        self.saved = []
        self._dirty = True

    def getSaveRequired(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return self._dirty

    def isMaxFile(self, _file_path):  # noqa: N802 - mirrors pymxs runtime naming.
        return True

    def loadMaxFile(self, file_path, **kwargs):  # noqa: N802 - mirrors pymxs runtime naming.
        self.loaded.append((file_path, kwargs))
        path = Path(file_path)
        self.maxFileName = path.name
        self.maxFilePath = str(path.parent)
        self.objects = [types.SimpleNamespace(name="loaded_box", handle=2)]
        self._dirty = False
        return True

    def resetMaxFile(self, *args):  # noqa: N802 - mirrors pymxs runtime naming.
        self.reset_calls.append(args)
        self.maxFileName = ""
        self.maxFilePath = ""
        self.objects = []
        self._dirty = False
        return True

    def Name(self, value):  # noqa: N802 - mirrors pymxs runtime naming.
        return "#{}".format(value)

    def saveMaxFile(self, file_path, **kwargs):  # noqa: N802 - mirrors pymxs runtime naming.
        self.saved.append((file_path, kwargs))
        path = Path(file_path)
        path.write_bytes(b"saved max fixture")
        self.maxFileName = path.name
        self.maxFilePath = str(path.parent)
        self._dirty = False
        return True

    def mergeMAXFile(self, file_path, *args, **kwargs):  # noqa: N802 - mirrors pymxs runtime naming.
        self.merged.append((file_path, args, kwargs))
        node = types.SimpleNamespace(name="merged_box", handle=2)
        self.objects.append(node)
        self.last_merged_nodes = [node]
        self._dirty = True
        return True

    def getLastMergedNodes(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return list(self.last_merged_nodes)


def test_open_scene_fails_closed_before_mutation_when_scene_is_dirty(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    scene_file = tmp_path / "incoming.max"
    scene_file.write_bytes(b"fake max fixture")

    result = run_skill_script(
        str(SKILL_DIR / "action_open_scene.py"),
        {"file_path": str(scene_file), "force": False},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "dirty_scene_requires_force"
    assert runtime.loaded == []


def test_open_scene_rejects_string_force_instead_of_bypassing_dirty_guard(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    scene_file = tmp_path / "incoming.max"
    scene_file.write_bytes(b"fake max fixture")

    result = run_skill_script(
        str(SKILL_DIR / "action_open_scene.py"),
        {"file_path": str(scene_file), "force": "false"},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_force"
    assert runtime.loaded == []


def test_open_scene_verifies_loaded_path_dirty_state_and_scene_facts(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    scene_file = tmp_path / "incoming.max"
    scene_file.write_bytes(b"fake max fixture")

    result = run_skill_script(
        str(SKILL_DIR / "action_open_scene.py"),
        {"file_path": str(scene_file), "force": True},
    )

    assert result["success"] is True
    assert runtime.loaded == [
        (
            str(scene_file.resolve()),
            {"useFileUnits": True, "quiet": True, "allowPrompts": False},
        )
    ]
    assert result["data"]["after"] == {
        "scene_name": "incoming.max",
        "current_file_path": str(scene_file.resolve()),
        "dirty": False,
        "object_count": 1,
        "system_units": "centimeters",
        "display_units": "metric",
        "renderer": "Arnold",
    }
    assert result["data"]["verified"] is True


def test_get_scene_status_exposes_the_lifecycle_readback_surface(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = run_skill_script(str(SKILL_DIR / "action_get_scene_status.py"), {})

    assert result["success"] is True
    assert result["data"]["scene_name"] == "working.max"
    assert result["data"]["dirty"] is True
    assert result["data"]["object_count"] == 1
    assert result["data"]["system_units"] == "centimeters"
    assert result["data"]["renderer"] == "Arnold"
    assert runtime.loaded == []


def test_new_scene_fails_closed_before_reset_when_scene_is_dirty(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = run_skill_script(str(SKILL_DIR / "action_new_scene.py"), {"force": False})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "dirty_scene_requires_force"
    assert runtime.reset_calls == []


def test_new_scene_rejects_string_force_before_reset(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = run_skill_script(str(SKILL_DIR / "action_new_scene.py"), {"force": "false"})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_force"
    assert runtime.reset_calls == []


def test_new_scene_resets_without_prompts_and_verifies_empty_clean_scene(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = run_skill_script(str(SKILL_DIR / "action_new_scene.py"), {"force": True})

    assert result["success"] is True
    assert runtime.reset_calls == [("#noPrompt",)]
    assert result["data"]["after"]["scene_name"] == "Untitled"
    assert result["data"]["after"]["current_file_path"] == ""
    assert result["data"]["after"]["dirty"] is False
    assert result["data"]["after"]["object_count"] == 0
    assert result["data"]["verified"] is True


def test_save_scene_uses_current_path_and_verifies_clean_file(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    runtime.maxFileName = "working.max"
    runtime.maxFilePath = str(tmp_path)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = run_skill_script(str(SKILL_DIR / "action_save_scene.py"), {})

    current_path = tmp_path / "working.max"
    assert result["success"] is True
    assert runtime.saved == [(str(current_path.resolve()), {"quiet": True})]
    assert current_path.is_file()
    assert result["data"]["after"]["current_file_path"] == str(current_path.resolve())
    assert result["data"]["after"]["dirty"] is False
    assert result["data"]["verified"] is True


def test_save_scene_as_refuses_to_overwrite_before_native_save(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    output = tmp_path / "existing.max"
    output.write_bytes(b"do not replace")

    result = run_skill_script(
        str(SKILL_DIR / "action_save_scene_as.py"),
        {"file_path": str(output), "overwrite": False},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_file_exists"
    assert runtime.saved == []
    assert output.read_bytes() == b"do not replace"


def test_save_scene_as_rejects_string_overwrite_before_native_save(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    output = tmp_path / "existing.max"
    output.write_bytes(b"do not replace")

    result = run_skill_script(
        str(SKILL_DIR / "action_save_scene_as.py"),
        {"file_path": str(output), "overwrite": "false"},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_overwrite"
    assert runtime.saved == []
    assert output.read_bytes() == b"do not replace"


def test_merge_file_uses_safe_flags_and_verifies_object_delta(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    runtime._dirty = False
    runtime.maxFileName = "working.max"
    runtime.maxFilePath = str(tmp_path)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    source = tmp_path / "asset.max"
    source.write_bytes(b"fake max fixture")

    result = run_skill_script(str(SKILL_DIR / "action_merge_file.py"), {"file_path": str(source)})

    assert result["success"] is True
    assert runtime.merged == [
        (
            str(source.resolve()),
            ("#autoRenameDups", "#renameMtlDups", "#neverReparent"),
            {"quiet": True},
        )
    ]
    assert result["data"]["after"]["current_file_path"] == str((tmp_path / "working.max").resolve())
    assert result["data"]["after"]["object_count"] == 2
    assert result["data"]["after"]["dirty"] is True
    assert result["data"]["merged_nodes"] == [{"node_name": "merged_box", "handle": 2}]
    assert result["data"]["verified"] is True


def test_scene_lifecycle_tools_are_typed_main_thread_contracts():
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    lifecycle_names = {
        "get_scene_status",
        "new_scene",
        "open_scene",
        "save_scene",
        "save_scene_as",
        "merge_file",
    }

    assert lifecycle_names.issubset(by_name)
    for name in lifecycle_names:
        assert by_name[name]["affinity"] == "main"
        assert by_name[name]["enforce_thread_affinity"] is True
        assert by_name[name]["input_schema"]["additionalProperties"] is False
        assert by_name[name]["job_strategy"] == "monolithic"
    assert by_name["get_scene_status"]["read_only"] is True
    assert by_name["new_scene"]["destructive"] is True
    assert by_name["open_scene"]["destructive"] is True
    assert by_name["open_scene"]["input_schema"]["properties"]["file_path"]["maxLength"] == 4096
    assert by_name["merge_file"]["input_schema"]["properties"]["node_names"]["maxItems"] == 1000


def test_native_success_without_open_readback_fails_closed(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    runtime.loadMaxFile = lambda _file_path, **_kwargs: True  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    source = tmp_path / "incoming.max"
    source.write_bytes(b"fake max fixture")

    result = run_skill_script(
        str(SKILL_DIR / "action_open_scene.py"),
        {"file_path": str(source), "force": True},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_open_readback_mismatch"


def test_native_success_without_reset_readback_fails_closed(monkeypatch):
    runtime = _FakeRuntime()
    runtime.resetMaxFile = lambda *_args: True  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = run_skill_script(str(SKILL_DIR / "action_new_scene.py"), {"force": True})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_reset_readback_mismatch"


def test_native_success_without_save_readback_fails_closed(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    runtime.maxFileName = "working.max"
    runtime.maxFilePath = str(tmp_path)
    current_path = tmp_path / "working.max"
    current_path.write_bytes(b"previous max fixture")
    runtime.saveMaxFile = lambda _file_path, **_kwargs: True  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))

    result = run_skill_script(str(SKILL_DIR / "action_save_scene.py"), {})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_save_readback_mismatch"


def test_native_success_without_merge_delta_fails_closed(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    runtime._dirty = False
    runtime.mergeMAXFile = lambda _file_path, *_args, **_kwargs: True  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    source = tmp_path / "asset.max"
    source.write_bytes(b"fake max fixture")

    result = run_skill_script(str(SKILL_DIR / "action_merge_file.py"), {"file_path": str(source)})

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_merge_readback_mismatch"


def test_save_scene_as_overwrites_only_when_explicit_and_verifies(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    output = tmp_path / "existing.max"
    output.write_bytes(b"replace me")

    result = run_skill_script(
        str(SKILL_DIR / "action_save_scene_as.py"),
        {"file_path": str(output), "overwrite": True},
    )

    assert result["success"] is True
    assert output.read_bytes() == b"saved max fixture"
    assert result["data"]["after"]["current_file_path"] == str(output.resolve())
    assert result["data"]["after"]["dirty"] is False


def test_merge_file_rejects_unbounded_options_before_native_call(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    source = tmp_path / "asset.max"
    source.write_bytes(b"fake max fixture")

    result = run_skill_script(
        str(SKILL_DIR / "action_merge_file.py"),
        {"file_path": str(source), "duplicate_names": "delete_existing"},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_merge_options"
    assert runtime.merged == []


def test_merge_file_rejects_string_select_flag_before_native_call(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    source = tmp_path / "asset.max"
    source.write_bytes(b"fake max fixture")

    result = run_skill_script(
        str(SKILL_DIR / "action_merge_file.py"),
        {"file_path": str(source), "select_merged": "false"},
    )

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_merge_options"
    assert runtime.merged == []
