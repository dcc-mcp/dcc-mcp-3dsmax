"""Unit tests for the 3ds Max core-integration modules.

Covers the parity work bringing :class:`MaxMcpServer` up to the Maya adapter's
``dcc-mcp-core`` interface integrations — readiness probe, capability manifest,
context snapshot, project tools, resources binder, semantic augmentation and
the Qt UI inspector.

None of these tests require a live 3ds Max: every 3ds Max-specific dependency
is faked or guarded so the suite runs in CI / ``mayapy``-free interpreters.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Add src to path for testing.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _capability_manifest as capmod
from dcc_mcp_3dsmax import _project_tools as projmod
from dcc_mcp_3dsmax import _qt_inspector as qtmod
from dcc_mcp_3dsmax import _readiness as readymod
from dcc_mcp_3dsmax import _resources as resmod
from dcc_mcp_3dsmax import _semantic_index as semmod
from dcc_mcp_3dsmax import context_snapshot as ctxmod

# ===========================================================================
# Fakes
# ===========================================================================


class _FakeProbeHolder:
    """Captures the readiness probe handed to ``server._server``."""

    def __init__(self) -> None:
        self.probe: Any = None

    def set_readiness_probe(self, probe: Any) -> None:
        self.probe = probe


class _FakeReadyServer:
    """Minimal stand-in for :class:`MaxMcpServer` used by readiness tests."""

    def __init__(self, dispatcher: Any = None) -> None:
        self._server = _FakeProbeHolder()
        self._max_dispatcher = dispatcher


class _FakeAsyncDispatcher:
    """UI-dispatcher-like object exposing ``submit_async_callable``."""

    def __init__(self, *, run_complete: bool = True) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._run_complete = run_complete

    def submit_async_callable(self, *, request_id, task, affinity="main", timeout_ms=None, on_complete=None, **_):
        self.calls.append({"request_id": request_id, "affinity": affinity, "timeout_ms": timeout_ms})
        task()
        if self._run_complete and on_complete is not None:
            on_complete({"success": True})
        return {"success": True}


class _Registry:
    def __init__(self) -> None:
        self.registered: List[str] = []

    def register(self, name, **_kwargs):
        self.registered.append(name)


class _FakeInnerServer:
    """Duck-typed inner Rust server: ``registry`` + ``register_handler``."""

    def __init__(self) -> None:
        self.registry = _Registry()
        self.handlers: Dict[str, Any] = {}

    def register_handler(self, name, handler):
        self.handlers[name] = handler


class _FakeOuterServer:
    """Wraps a fake inner server like :class:`MaxMcpServer` does."""

    def __init__(self) -> None:
        self._server = _FakeInnerServer()
        self._config = type("Cfg", (), {"scene": None, "dcc_version": None})()


# ===========================================================================
# Readiness binder
# ===========================================================================


def test_readiness_inline_when_no_dispatcher():
    server = _FakeReadyServer(dispatcher=None)
    binder = readymod.ReadinessBinder()
    assert binder.bind(server) is True
    report = binder.report()
    assert report["process"] is True
    assert report["dispatcher"] is True
    assert report["dcc"] is True
    # Core 0.17.56+: host_execution_bridge, main_thread_executor, skill_catalog
    # are set by the server wiring, not by the binder alone.
    assert "host_execution_bridge" in report
    assert "main_thread_executor" in report
    assert "skill_catalog" in report


def test_readiness_schedules_on_ui_dispatcher():
    dispatcher = _FakeAsyncDispatcher(run_complete=True)
    server = _FakeReadyServer(dispatcher=dispatcher)
    binder = readymod.ReadinessBinder()
    assert binder.bind(server) is True
    assert dispatcher.calls, "probe must be scheduled on the dispatcher"
    assert dispatcher.calls[0]["affinity"] == "main"
    report = binder.report()
    assert report["dcc"] is True
    # Core 0.17.56+: first pump completion must flip main_thread_executor too.
    assert report["main_thread_executor"] is True, (
        "first pump completion must prove main-thread executor availability "
        "(gateway host execution summary requires dcc && main_thread_executor)"
    )


def test_readiness_dcc_pending_until_pump_runs():
    dispatcher = _FakeAsyncDispatcher(run_complete=False)
    server = _FakeReadyServer(dispatcher=dispatcher)
    binder = readymod.ReadinessBinder()
    binder.bind(server)
    report = binder.report()
    assert report["dispatcher"] is True
    assert report["dcc"] is False  # on_complete never fired
    # Core 0.17.56+: extended probe fields are present.
    assert "host_execution_bridge" in report
    assert "main_thread_executor" in report


def test_resolve_readiness_timeout_secs(monkeypatch):
    monkeypatch.delenv(readymod.ENV_READINESS_TIMEOUT_SECS, raising=False)
    assert readymod.resolve_readiness_timeout_secs(None) is None
    assert readymod.resolve_readiness_timeout_secs(60) == 60
    assert readymod.resolve_readiness_timeout_secs(0) is None
    monkeypatch.setenv(readymod.ENV_READINESS_TIMEOUT_SECS, "45")
    assert readymod.resolve_readiness_timeout_secs(None) == 45
    monkeypatch.setenv(readymod.ENV_READINESS_TIMEOUT_SECS, "garbage")
    assert readymod.resolve_readiness_timeout_secs(None) is None


def test_readiness_core_01756_extended_states():
    """Core 0.17.56+ ReadinessProbe exposes 6 states; binder can flip the new ones."""
    binder = readymod.ReadinessBinder()
    report = binder.report()
    # Baseline: 6-state report from core 0.17.56+.
    assert "process" in report
    assert "dcc" in report
    assert "dispatcher" in report
    assert "skill_catalog" in report
    assert "host_execution_bridge" in report
    assert "main_thread_executor" in report
    # skill_catalog starts true (empty catalog is valid).
    assert report["skill_catalog"] is True
    # host_execution_bridge and main_thread_executor start false.
    assert report["host_execution_bridge"] is False
    assert report["main_thread_executor"] is False


def test_readiness_binder_flips_extended_states():
    """ReadinessBinder.mark_* methods correctly flip core 0.17.56+ probe bits."""
    binder = readymod.ReadinessBinder()
    binder.mark_host_execution_bridge_ready()
    assert binder.report()["host_execution_bridge"] is True
    binder.mark_main_thread_executor_ready()
    assert binder.report()["main_thread_executor"] is True
    binder.mark_skill_catalog_ready(False)
    assert binder.report()["skill_catalog"] is False
    binder.mark_skill_catalog_ready(True)
    assert binder.report()["skill_catalog"] is True


def test_readiness_inline_marks_executor_ready():
    """When no dispatcher is present, the binder also flips main_thread_executor."""
    server = _FakeReadyServer(dispatcher=None)
    binder = readymod.ReadinessBinder()
    assert binder.bind(server) is True
    report = binder.report()
    assert report["main_thread_executor"] is True
    assert report["dcc"] is True


def test_max_mcp_server_readiness_report_contract_with_ui_dispatcher():
    """MaxMcpServer + UI-style dispatcher produces a valid 6-state readiness report.

    Core 0.17.56 gateway host execution summary requires all four of
    dispatcher, dcc, host_execution_bridge, and main_thread_executor.
    A fake async dispatcher with ``run_complete=True`` must flip all four.
    """
    from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

    dispatcher = _FakeAsyncDispatcher(run_complete=True)
    server = MaxMcpServer(
        options=MaxServerOptions(
            port=0,
            dispatcher=dispatcher,
            enable_gateway_failover=False,
            job_storage_path="",
        )
    )

    report = server.readiness_report()
    # All six keys must be present.
    assert report.keys() == {
        "process",
        "dcc",
        "skill_catalog",
        "dispatcher",
        "host_execution_bridge",
        "main_thread_executor",
    }
    # Core invariants: process is always True, skill_catalog starts True.
    assert report["process"] is True
    assert report["skill_catalog"] is True
    # Binder wiring: dispatcher probe published, pump completed.
    assert report["dispatcher"] is True
    assert report["dcc"] is True
    # Core 0.17.56+: HostExecutionBridge was registered, and the first pump
    # completion proved main-thread executor availability.
    assert report["host_execution_bridge"] is True, "HostExecutionBridge must be registered during __init__"
    assert report["main_thread_executor"] is True, (
        "first pump completion must flip main_thread_executor "
        "(gateway requires dcc && main_thread_executor for host execution ready)"
    )

    server.stop()


def test_max_mcp_server_readiness_main_thread_executor_pending_before_pump():
    """When the pump has NOT completed, main_thread_executor stays False."""
    from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

    dispatcher = _FakeAsyncDispatcher(run_complete=False)
    server = MaxMcpServer(
        options=MaxServerOptions(
            port=0,
            dispatcher=dispatcher,
            enable_gateway_failover=False,
            job_storage_path="",
        )
    )

    report = server.readiness_report()
    assert report["dispatcher"] is True
    assert report["dcc"] is False  # pump never completed
    assert report["host_execution_bridge"] is True  # registered during __init__
    assert report["main_thread_executor"] is False, "main_thread_executor must be False until the first pump drains"

    server.stop()


# ===========================================================================
# Capability manifest builder
# ===========================================================================


def _builder(skills, actions, loaded):
    return capmod.MaxCapabilityManifestBuilder(
        skill_lister=lambda: skills,
        action_lister=lambda: actions,
        is_loaded=lambda name: name in loaded,
    )


def test_capability_manifest_empty():
    builder = _builder([], [], set())
    records = builder.build()
    assert records == []
    payload = capmod.build_manifest_payload(records)
    assert payload["dcc_type"] == "3dsmax"
    assert payload["totals"]["actions"] == 0


def test_capability_manifest_projects_loaded_action():
    skills = [{"name": "3dsmax-modeling", "summary": "Create primitives", "tags": ["mesh"]}]
    actions = [
        {
            "name": "3dsmax_modeling__create_box",
            "skill": "3dsmax-modeling",
            "summary": "Make a box",
            "execution": "sync",
            "affinity": "main",
            "input_schema": {"type": "object"},
        }
    ]
    builder = _builder(skills, actions, {"3dsmax-modeling"})
    records = builder.build()
    assert len(records) == 1
    rec = records[0]
    assert rec.backend_tool == "3dsmax_modeling__create_box"
    assert rec.loaded is True
    assert rec.has_schema is True
    assert "mesh" in rec.tags

    payload = capmod.build_manifest_payload(records, dcc_version="2024", scene="C:/p/shot.max")
    assert payload["totals"]["loaded_actions"] == 1
    assert payload["metadata"]["dcc_version"] == "2024"


def test_capability_manifest_filters_stubs():
    actions = [
        {"name": "__skill__3dsmax-scene"},
        {"name": "__group__authoring"},
        {"name": "3dsmax_scene__get_scene_info", "skill": "3dsmax-scene"},
    ]
    builder = _builder([], actions, set())
    records = builder.build()
    assert [r.backend_tool for r in records] == ["3dsmax_scene__get_scene_info"]


def test_capability_manifest_unloaded_skill_tools():
    skills = [
        {
            "name": "3dsmax-transform",
            "summary": "Move nodes",
            "tools": [{"name": "move_nodes", "summary": "Translate"}],
        }
    ]
    builder = _builder(skills, [], set())  # not loaded
    records = builder.build()
    assert len(records) == 1
    rec = records[0]
    assert rec.requires_load_skill is True
    assert rec.backend_tool == "3dsmax_transform__move_nodes"
    assert rec.load_hint["arguments"]["skill_name"] == "3dsmax-transform"


def test_register_capability_mcp_tool():
    server = _FakeOuterServer()
    builder = _builder([], [], set())
    assert capmod.register_capability_mcp_tool(server, builder=builder) is True
    assert "dcc_capability_manifest" in server._server.handlers
    result = server._server.handlers["dcc_capability_manifest"]({"loaded_only": False})
    assert result["success"] is True
    assert result["context"]["dcc_type"] == "3dsmax"


# ===========================================================================
# Context snapshot
# ===========================================================================


def test_context_snapshot_headless_stub():
    provider = ctxmod.MaxContextSnapshotProvider(runtime_provider=lambda: None)
    snap = provider.collect()
    assert snap["dcc"] == "3dsmax"
    assert snap["available"] is False
    assert "scene" not in snap


class _FakeUnits:
    @staticmethod
    def SystemType():
        return "Centimeters"


class _FakeMaxRuntime:
    maxFileName = "shot.max"
    maxFilePath = "C:/proj/"
    currentTime = 10
    units = _FakeUnits

    def __init__(self):
        self.selection = [type("N", (), {"name": "Box001"})()]
        self.objects = [1, 2, 3]


def test_context_snapshot_with_runtime():
    provider = ctxmod.MaxContextSnapshotProvider(runtime_provider=_FakeMaxRuntime)
    snap = provider.collect()
    assert snap["available"] is True
    assert snap["scene"] == "C:/proj/shot.max"
    assert snap["selection"] == ["Box001"]
    assert snap["frame"] == 10
    assert snap["node_count"] == 3
    assert snap["units"] == "Centimeters"


def test_collect_gateway_metadata():
    provider = ctxmod.MaxContextSnapshotProvider(runtime_provider=_FakeMaxRuntime)
    meta = ctxmod.collect_gateway_metadata(provider)
    assert meta["scene"] == "C:/proj/shot.max"
    assert meta["documents"] == ["C:/proj/shot.max"]


def test_collect_gateway_metadata_headless():
    provider = ctxmod.MaxContextSnapshotProvider(runtime_provider=lambda: None)
    meta = ctxmod.collect_gateway_metadata(provider)
    assert meta["scene"] is None
    assert meta["documents"] == []


# ===========================================================================
# Project tools
# ===========================================================================


class _FakeSceneResolver:
    def __init__(self, scene: Optional[str]) -> None:
        self._scene = scene

    def current_scene(self) -> Optional[str]:
        return self._scene


def test_project_tools_resolve_enabled(monkeypatch):
    monkeypatch.delenv(projmod.ENV_PROJECT_TOOLS, raising=False)
    assert projmod.resolve_enabled() is True
    assert projmod.resolve_enabled(False) is False
    monkeypatch.setenv(projmod.ENV_PROJECT_TOOLS, "0")
    assert projmod.resolve_enabled() is False


def test_project_tools_bind_without_scene(monkeypatch):
    server = _FakeOuterServer()
    registered: List[str] = []

    def fake_register(inner, *, dcc_name, project):
        registered.append(dcc_name)
        assert project is None

    monkeypatch.setattr(projmod, "register_project_tools", fake_register)
    integration = projmod.ProjectToolsIntegration(scene_resolver=_FakeSceneResolver(None))
    assert integration.bind(server) is True
    assert registered == ["3dsmax"]


def test_project_tools_attach_disabled(monkeypatch):
    monkeypatch.setenv(projmod.ENV_PROJECT_TOOLS, "0")
    assert projmod.attach_to_server(_FakeOuterServer()) is None


def test_max_scene_resolver_headless():
    # No pymxs available in CI → None, never raises.
    assert projmod.MaxSceneResolver().current_scene() is None


# ===========================================================================
# Resources binder
# ===========================================================================


class _FakeResourceHandle:
    def __init__(self) -> None:
        self.scenes: List[Any] = []
        self.producers: List[str] = []

    def set_scene(self, payload):
        self.scenes.append(payload)

    def register_producer(self, scheme, producer):
        self.producers.append(scheme)


class _FakeResourceServer:
    def __init__(self) -> None:
        self._handle = _FakeResourceHandle()
        self._server = type("Inner", (), {"resources": lambda _self: self._handle})()


def test_resources_resolve_enabled(monkeypatch):
    monkeypatch.delenv(resmod.ENV_RESOURCES, raising=False)
    assert resmod.resolve_enabled() is True
    monkeypatch.setenv(resmod.ENV_RESOURCES, "0")
    assert resmod.resolve_enabled() is False


def test_resources_binder_publishes_initial_scene():
    server = _FakeResourceServer()
    binder = resmod.MaxResourceBinder(snapshot_provider=lambda: {"dcc": "3dsmax", "scene": "s.max"})
    assert binder.bind(server) is True
    assert server._handle.scenes == [{"dcc": "3dsmax", "scene": "s.max"}]
    assert resmod.SCHEME_MAXSCRIPT in server._handle.producers


def test_resources_install_disabled(monkeypatch):
    monkeypatch.setenv(resmod.ENV_RESOURCES, "0")
    assert resmod.install_resources(_FakeResourceServer()) is None


def test_resources_publish_scene_explicit_payload():
    server = _FakeResourceServer()
    binder = resmod.MaxResourceBinder(snapshot_provider=None)
    binder.bind(server)
    binder.publish_scene({"scene": "explicit.max"})
    assert {"scene": "explicit.max"} in server._handle.scenes


# ===========================================================================
# Regression: issue #50 — dispatcher + execution_bridge not both passed
# ===========================================================================


def test_startup_passes_only_one_execution_mode(monkeypatch):
    """Core 0.17.36+ rejects dispatcher+execution_bridge set simultaneously; adapter passes only one."""
    from dcc_mcp_core import HostExecutionBridge

    from dcc_mcp_3dsmax.dispatcher import MaxUiDispatcher
    from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

    dispatcher = MaxUiDispatcher()
    bridge = HostExecutionBridge(
        dispatcher=dispatcher,
        runner=lambda p, a: {"success": True},
        default_thread_affinity="main",
    )
    # This MUST NOT raise ValueError("Pass either dispatcher or execution_bridge, not both")
    server = MaxMcpServer(
        options=MaxServerOptions(
            port=0,
            dispatcher=dispatcher,
            execution_bridge=bridge,
            enable_gateway_failover=False,
            job_storage_path="",
        )
    )
    assert server._execution_bridge is bridge
    assert server._inprocess_executor_registered is True


# ===========================================================================
# Semantic augmentation
# ===========================================================================


class _FakeSummary:
    def __init__(self, name, description="", tags=(), dcc="3dsmax", version=""):
        self.name = name
        self.description = description
        self.tags = list(tags)
        self.dcc = dcc
        self.version = version


def test_semantic_disabled_by_default(monkeypatch):
    monkeypatch.delenv(semmod.ENV_SEMANTIC_INDEX, raising=False)
    assert semmod.resolve_semantic_index_enabled() is False
    assert semmod.build_semantic_index() is None


def test_semantic_embedder_kind(monkeypatch):
    monkeypatch.delenv(semmod.ENV_SEMANTIC_EMBEDDER, raising=False)
    assert semmod.resolve_embedder_kind() == "hashed"
    monkeypatch.setenv(semmod.ENV_SEMANTIC_EMBEDDER, "onnx")
    assert semmod.resolve_embedder_kind() == "onnx"


def test_semantic_augment_appends_recall():
    index = semmod.MaxSemanticIndex.build(embedder_kind="hashed")
    if index is None:
        pytest.skip("dcc-mcp-core lacks VectorSkillIndex (needs >=0.17.38)")
    summaries = [
        _FakeSummary("3dsmax-modeling", "create and model primitives and meshes"),
        _FakeSummary("3dsmax-transform", "move rotate scale nodes"),
    ]
    base = [summaries[1]]  # transform first by base ranking
    augmented = index.augment(base, "modelling a chair", summaries)
    names = [s.name for s in augmented]
    # base ordering preserved (transform stays first), modeling appended.
    assert names[0] == "3dsmax-transform"
    assert "3dsmax-modeling" in names


def test_semantic_augment_noop_without_query():
    index = semmod.MaxSemanticIndex.build(embedder_kind="hashed")
    if index is None:
        pytest.skip("dcc-mcp-core lacks VectorSkillIndex (needs >=0.17.38)")
    base = [_FakeSummary("3dsmax-scene")]
    assert index.augment(base, None, base) == base


# ===========================================================================
# Qt UI inspector — graceful unavailability
# ===========================================================================


def test_qt_inspector_disabled_via_env(monkeypatch):
    monkeypatch.setenv(qtmod.ENV_QT_UI_INSPECTOR, "0")
    assert qtmod.register_3dsmax_qt_ui_inspector(_FakeInnerServer()) is False


def test_qt_inspector_marshal_inline_without_dispatcher():
    # No dispatcher → handler runs inline (returns result directly).
    out = qtmod._marshal_to_main(None, lambda: {"ok": 1})
    assert out == {"ok": 1}


def test_qt_inspector_registers_when_core_available(monkeypatch):
    monkeypatch.setenv(qtmod.ENV_QT_UI_INSPECTOR, "1")
    server = _FakeInnerServer()
    result = qtmod.register_3dsmax_qt_ui_inspector(server, dispatcher=None)
    # Either the core skill registered (True + handlers) or it is unavailable
    # in this core build (False) — both are graceful, never raising.
    assert result in (True, False)
    if result:
        assert server.registry.registered
