"""3ds Max MCP server — embeds a Streamable HTTP MCP server inside 3ds Max.

Extends :class:`dcc_mcp_core.server_base.DccServerBase` with 3ds Max-specific
skill path discovery and version detection.

All generic logic (skill registration, hot-reload, gateway failover,
action registry, lifecycle) is provided by the base class.

Usage (inside 3ds Max MAXScript Listener or startup script)::

    import dcc_mcp_3dsmax

    # Start on a random instance port and publish through the stable gateway.
    server = dcc_mcp_3dsmax.start_server()

    # Progressive loading — discover skills without loading them immediately
    n = server.discover_skills()        # scan paths, register tool metadata
    server.load_skill("3dsmax-scene")  # lazy-load a specific skill

    dcc_mcp_3dsmax.stop_server()
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Import third-party modules
from dcc_mcp_core import DccServerOptions, HostExecutionBridge, MinimalModeConfig, scan_and_load_strict
from dcc_mcp_core._registration import RegistrationContext, run_registration_phases
from dcc_mcp_core.server_base import DccServerBase

# Import local modules
from dcc_mcp_3dsmax import (
    _env,
    _executor,
    _project_tools,
    _qt_inspector,
    _readiness,
    _registration,
    _resources,
    _semantic_index,
)
from dcc_mcp_3dsmax.__version__ import __version__
from dcc_mcp_3dsmax._capability_manifest import (
    MaxCapabilityManifestBuilder,
    build_manifest_payload,
    register_capability_mcp_tool,
)
from dcc_mcp_3dsmax._constants import DEFAULT_GATEWAY_PORT
from dcc_mcp_3dsmax._version_probe import get_3dsmax_version_string
from dcc_mcp_3dsmax.context_snapshot import (
    MaxContextSnapshotProvider,
    collect_gateway_metadata,
)

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────

SERVER_NAME = "dcc-mcp-3dsmax"
SERVER_VERSION = __version__
DEFAULT_PORT = 0

# Built-in skills directory shipped with this package
_BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# Environment variable for extra skill paths (colon/semicolon separated)
_ENV_EXTRA_SKILL_PATHS = "DCC_MCP_3DSMAX_SKILL_PATHS"
_ENV_GENERIC_SKILL_PATHS = "DCC_MCP_SKILL_PATHS"

_DCC_NAME = "3dsmax"


# ── options ─────────────────────────────────────────────────────────────────


@dataclass
class MaxServerOptions:
    """Adapter-local options collapsed for the dcc-mcp-core 0.17+ server contract."""

    port: int = DEFAULT_PORT
    extra_skill_paths: Optional[List[str]] = None
    server_name: str = SERVER_NAME
    server_version: str = SERVER_VERSION
    # Gateway options
    gateway_port: Optional[int] = DEFAULT_GATEWAY_PORT
    registry_dir: Optional[str] = None
    dcc_version: Optional[str] = None
    scene: Optional[str] = None
    enable_gateway_failover: Optional[bool] = None
    # Observability options
    metrics_enabled: Optional[bool] = None
    job_storage_path: Optional[str] = None
    job_recovery: Optional[str] = None
    enable_workflows: Optional[bool] = None
    # Diagnostics options (new in 0.17+)
    dcc_pid: Optional[int] = None
    dcc_window_title: Optional[str] = None
    dcc_window_handle: Optional[int] = None
    snapshot_provider: Optional[Any] = None
    # Execution options (new in 0.17+)
    dispatcher: Optional[Any] = None  # BaseDccCallableDispatcher
    execution_bridge: Optional[Any] = None  # HostExecutionBridge
    # Readiness options (issue parity with Maya #184)
    readiness_timeout_secs: Optional[int] = None

    def to_core_options(self) -> DccServerOptions:
        """Convert to core DccServerOptions using from_env()."""
        return DccServerOptions.from_env(
            dcc_name=_DCC_NAME,
            builtin_skills_dir=_BUILTIN_SKILLS_DIR,
            port=self.port,
            server_name=self.server_name,
            server_version=self.server_version,
            # Gateway kwargs
            gateway_port=self.gateway_port,
            registry_dir=self.registry_dir,
            dcc_version=self.dcc_version,
            scene=self.scene,
            enable_gateway_failover=_env.resolve_enable_gateway_failover(
                self.enable_gateway_failover,
                default=True,
            ),
            # Observability kwargs
            enable_file_logging=True,  # default
            enable_job_persistence=self.job_storage_path is not None,
            enable_telemetry=True,  # default
            # Diagnostics kwargs (new in 0.17+)
            dcc_pid=self.dcc_pid,
            dcc_window_title=self.dcc_window_title,
            dcc_window_handle=self.dcc_window_handle,
            snapshot_provider=self.snapshot_provider,
            # Execution kwargs (new in 0.17+)
            # Core 0.17.36+ rejects both dispatcher and execution_bridge set; pass only one.
            dispatcher=None if self.execution_bridge is not None else self.dispatcher,
            execution_bridge=self.execution_bridge,
        )


# ── server class ─────────────────────────────────────────────────────────────


class MaxMcpServer(DccServerBase):
    """MCP server embedded inside 3ds Max.

    Thin subclass of :class:`~dcc_mcp_core.server_base.DccServerBase`.
    All skill management, hot-reload, and gateway election logic is
    inherited.  This class adds only:

    - 3ds Max built-in skills directory (``skills/``)
    - 3ds Max version detection via ``pymxs.runtime.maxVersion()``
    - Progressive loading helpers: :meth:`discover_skills`, :meth:`loaded_skill_count`

    Multi-instance / gateway
    ------------------------
    dcc-mcp-core exposes the stable gateway at http://127.0.0.1:9765/mcp while
    each 3ds Max instance listens on its own ephemeral localhost port.

    Progressive loading
    -------------------
    Skills can be discovered (metadata only, no Python import) and loaded
    on demand::

        server.discover_skills()             # fast: scan SKILL.md files
        server.load_skill("3dsmax-scene")   # lazy: import scripts only now
        server.unload_skill("3dsmax-scene") # unload to free memory

    Attributes:
        port: TCP port the server is listening on (updated after :meth:`start`).
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        extra_skill_paths: Optional[List[str]] = None,
        server_name: str = SERVER_NAME,
        server_version: str = SERVER_VERSION,
        gateway_port: Optional[int] = DEFAULT_GATEWAY_PORT,
        registry_dir: Optional[str] = None,
        dcc_version: Optional[str] = None,
        scene: Optional[str] = None,
        enable_gateway_failover: Optional[bool] = None,
        metrics_enabled: Optional[bool] = None,
        job_storage_path: Optional[str] = None,
        job_recovery: Optional[str] = None,
        enable_workflows: Optional[bool] = None,
        dcc_pid: Optional[int] = None,
        dcc_window_title: Optional[str] = None,
        dcc_window_handle: Optional[int] = None,
        snapshot_provider: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
        execution_bridge: Optional[Any] = None,
        readiness_timeout_secs: Optional[int] = None,
        options: Optional[MaxServerOptions] = None,
    ) -> None:
        if options is None:
            options = MaxServerOptions(
                port=port,
                extra_skill_paths=extra_skill_paths,
                server_name=server_name,
                server_version=server_version,
                gateway_port=gateway_port,
                registry_dir=registry_dir,
                dcc_version=dcc_version,
                scene=scene,
                enable_gateway_failover=enable_gateway_failover,
                metrics_enabled=metrics_enabled,
                job_storage_path=job_storage_path,
                job_recovery=job_recovery,
                enable_workflows=enable_workflows,
                dcc_pid=dcc_pid,
                dcc_window_title=dcc_window_title,
                dcc_window_handle=dcc_window_handle,
                snapshot_provider=snapshot_provider,
                dispatcher=dispatcher,
                execution_bridge=execution_bridge,
                readiness_timeout_secs=readiness_timeout_secs,
            )

        super().__init__(options=options.to_core_options())
        self._extra_skill_paths: List[str] = list(options.extra_skill_paths or [])
        self._max_dispatcher: Any = None
        self._execution_bridge: HostExecutionBridge

        # ── Runtime readiness binder (parity with Maya #184) ───────────
        # Constructed *before* dispatcher attachment so ``attach_dispatcher``
        # can re-bind through ``self._readiness`` unconditionally.  Bound for
        # real at the end of ``__init__`` once executor wiring is settled.
        self._readiness_timeout_secs: Optional[int] = _readiness.resolve_readiness_timeout_secs(
            options.readiness_timeout_secs
        )
        self._readiness: _readiness.ReadinessBinder = _readiness.ReadinessBinder(
            timeout_secs=self._readiness_timeout_secs,
        )

        if options.execution_bridge is not None:
            self._execution_bridge = options.execution_bridge
            self.register_host_execution_bridge(self._execution_bridge)
            self._readiness.mark_host_execution_bridge_ready()
        else:
            self.attach_dispatcher(options.dispatcher)

        # ── Prometheus metrics ──────────────────────────────────────
        if _env.resolve_metrics_enabled(options.metrics_enabled):
            self._config.enable_prometheus = True
            logger.info("[%s] Prometheus /metrics endpoint enabled", _DCC_NAME)

        # ── Job persistence ─────────────────────────────────────────
        effective_job_path = _env.resolve_job_storage(options.job_storage_path)
        if effective_job_path:
            self._config.job_storage_path = effective_job_path
            logger.info("[%s] Job storage: %s", _DCC_NAME, effective_job_path)
        elif effective_job_path == "":
            self._config.job_storage_path = ""

        self._job_recovery: str = _env.resolve_job_recovery(options.job_recovery)
        self._config.job_recovery = self._job_recovery
        if self._job_recovery == "requeue":
            logger.info("[%s] Job recovery policy: requeue idempotent interrupted jobs", _DCC_NAME)

        # ── Workflow engine ──────────────────────────────────────────
        if _env.resolve_enable_workflows(options.enable_workflows):
            try:
                self._config.enable_workflows = True
                logger.info(
                    "[%s] Workflow engine enabled (workflows.run / .resume / .list_runs)",
                    _DCC_NAME,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] Could not enable workflows on inner config: %s",
                    _DCC_NAME,
                    exc,
                )

        if options.gateway_port == 0 or (
            options.gateway_port is None
            and not _env.resolve_enable_gateway_failover(
                options.enable_gateway_failover,
                default=True,
            )
        ):
            self._config.gateway_port = 0

        # ── Context snapshot + capability manifest (parity #163 / #165) ──
        self._snapshot_provider_impl: MaxContextSnapshotProvider = MaxContextSnapshotProvider()
        try:
            self.set_context_snapshot_provider(self._snapshot_provider_impl)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] set_context_snapshot_provider failed: %s", _DCC_NAME, exc)

        self._capability_builder: MaxCapabilityManifestBuilder = MaxCapabilityManifestBuilder(
            dcc_name=_DCC_NAME,
            skill_lister=self.list_skills,
            action_lister=self.list_actions,
            is_loaded=self.is_skill_loaded,
            skill_info_lister=self.get_skill_info,
        )

        # ── Project tools + resources (populated in register_builtin_actions) ──
        self._project_tools: Optional[_project_tools.ProjectToolsIntegration] = None
        self._resources: Optional[_resources.MaxResourceBinder] = None

        # ── Bind readiness now the executor/dispatcher state is settled ──
        self._readiness.bind(self)

        # ── Morphology-aware semantic recall (parity #313) ──────────────
        try:
            self._semantic: Optional[_semantic_index.MaxSemanticIndex] = _semantic_index.build_semantic_index()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] semantic index init failed: %s", _DCC_NAME, exc)
            self._semantic = None
        if self._semantic is not None:
            logger.info("[%s] semantic skill recall enabled (embedder=%s)", _DCC_NAME, self._semantic.embedder_kind)

        logger.info("[%s] MaxMcpServer initialized (port=%s)", _DCC_NAME, options.port)

    def attach_dispatcher(self, dispatcher: Any) -> None:
        """Attach or replace the 3ds Max host dispatcher used by skill execution."""
        self._max_dispatcher = dispatcher
        self._register_execution_bridge(dispatcher)

        # Re-bind readiness so a late ``attach_dispatcher`` (plugin bootstrap
        # wiring the dispatcher after ``__init__``) flips ``dispatcher=true``
        # and schedules the dcc probe on the new dispatcher.
        readiness = getattr(self, "_readiness", None)
        if readiness is not None:
            readiness.bound_server = None  # force re-bind
            readiness.bind(self)

    def _register_execution_bridge(self, dispatcher: Any) -> None:
        core_dispatcher_attached = self._attach_core_dispatcher(dispatcher)
        self._execution_bridge = HostExecutionBridge(
            dispatcher=None if core_dispatcher_attached else dispatcher,
            runner=_executor.run_skill_script,
            default_thread_affinity="main",
        )
        self.register_host_execution_bridge(self._execution_bridge)
        self._readiness.mark_host_execution_bridge_ready()
        if core_dispatcher_attached:
            self._dcc_dispatcher = dispatcher
            self._readiness.mark_main_thread_executor_ready()

    def _attach_core_dispatcher(self, dispatcher: Any) -> bool:
        """Attach core Queue/BlockingDispatcher instances to the HTTP main-thread route."""
        if dispatcher is None:
            return False
        attach = getattr(self._server, "attach_dispatcher", None)
        if not callable(attach):
            return False
        try:
            attach(dispatcher)
        except (RuntimeError, TypeError) as exc:
            logger.debug("[%s] Core dispatcher attach skipped: %s", _DCC_NAME, exc)
            return False
        logger.info("[%s] Core main-thread dispatcher attached (%s)", _DCC_NAME, type(dispatcher).__name__)
        return True

    # ── 3ds Max version detection ──────────────────────────────────────

    def _version_string(self) -> str:
        """Return the 3ds Max version via ``pymxs.runtime.maxVersion()``."""
        return get_3dsmax_version_string()

    # ── Port property ──────────────────────────────────────────────────

    @property
    def port(self) -> int:
        """TCP port the server is listening on."""
        if self._handle is not None:
            try:
                return int(self._handle.port)
            except Exception:
                pass
        return int(self._options.port)

    # ── Skill search path helpers ──────────────────────────────────────────────

    def _collect_skill_paths(self) -> List[str]:
        """Collect and deduplicate existing skill paths."""
        return self.collect_skill_search_paths(
            extra_paths=self._extra_skill_paths,
            filter_existing=True,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self, *, install_atexit_hook: bool = True) -> "MaxMcpServer":
        """Start the MCP HTTP server.  Returns *self* for chaining."""
        super().start(install_atexit_hook=install_atexit_hook)
        return self

    def stop(self) -> None:
        """Stop the MCP server and drain any attached 3ds Max dispatcher."""
        dispatcher = getattr(self, "_dcc_dispatcher", None)
        if dispatcher is not None:
            shutdown = getattr(dispatcher, "shutdown", None)
            if callable(shutdown):
                try:
                    try:
                        signalled = shutdown("Interrupted")
                    except TypeError:
                        signalled = shutdown()
                    logger.info("[%s] dispatcher.shutdown signalled %s job(s)", _DCC_NAME, signalled)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Error draining dispatcher during stop(): %s", _DCC_NAME, exc)

        if self._resources is not None:
            try:
                self._resources.unbind()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[%s] resources.unbind failed: %s", _DCC_NAME, exc)

        super().stop()

    def register_builtin_actions(
        self,
        extra_skill_paths: Optional[List[str]] = None,
        include_bundled: bool = True,
        minimal_mode: Optional["MinimalModeConfig"] = None,
        strict_scan: Optional[bool] = None,
    ) -> "MaxMcpServer":
        """Discover built-in skills + attach 3ds Max-specific core integrations."""
        # Stash minimal_mode so the core phase can access it.
        self._registration_minimal_mode = minimal_mode

        context = RegistrationContext(
            server=self,
            extra_skill_paths=extra_skill_paths,
            include_bundled=include_bundled,
            minimal=minimal_mode is not None,
            strict_scan=strict_scan,
        )
        report = run_registration_phases(_registration.default_registration_phases(), context)
        self._registration_report = report
        return self

    def _register_core_builtin_actions(self, context: RegistrationContext) -> None:
        """Discover skills via the base-class registration path (phase helper)."""
        paths = list(context.extra_skill_paths or []) + self._extra_skill_paths
        minimal_mode = getattr(self, "_registration_minimal_mode", None)
        super().register_builtin_actions(
            extra_skill_paths=paths,
            include_bundled=context.include_bundled,
            minimal_mode=minimal_mode,
        )

    def _run_strict_skill_scan_phase(self, context: RegistrationContext) -> None:
        """Run strict skill validation when enabled (phase helper)."""
        if _env.resolve_strict_skill_scan(context.strict_scan):
            paths = list(context.extra_skill_paths or []) + self._extra_skill_paths
            self._strict_skill_scan(paths, include_bundled=context.include_bundled)

    # ── Optional core integration phases ───────────────────────────────────────

    def _scan_skill_metadata_for_sidecars(
        self,
        extra_skill_paths: Optional[List[str]],
        include_bundled: bool,
    ) -> List[Any]:
        """Return ``SkillMetadata`` list aligned with the discovery paths (read-only)."""
        from dcc_mcp_core import scan_and_load_lenient  # noqa: PLC0415

        paths = self.collect_skill_search_paths(
            extra_paths=extra_skill_paths,
            include_bundled=include_bundled,
            filter_existing=True,
        )
        extra = paths if paths else None
        skills, _skipped = scan_and_load_lenient(extra_paths=extra, dcc_name=_DCC_NAME)
        return skills

    def _register_recipes_tools(self, extra_skill_paths: Optional[List[str]], include_bundled: bool) -> None:
        """Register ``recipes__*`` for skills declaring ``metadata.dcc-mcp.recipes``."""
        try:
            from dcc_mcp_core.recipes import register_recipes_tools  # noqa: PLC0415
        except ImportError as exc:
            logger.debug("[%s] recipes tools skipped (import): %s", _DCC_NAME, exc)
            return
        try:
            skills = self._scan_skill_metadata_for_sidecars(extra_skill_paths, include_bundled)
            register_recipes_tools(self._server, skills=skills, dcc_name=_DCC_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] recipes tools failed: %s", _DCC_NAME, exc)

    def _register_skill_reference_docs_tools(
        self,
        extra_skill_paths: Optional[List[str]],
        include_bundled: bool,
    ) -> None:
        """Register ``skill_refs__*`` for sibling reference docs beside a skill."""
        try:
            from dcc_mcp_core.skill_reference_docs import register_skill_reference_docs_tools  # noqa: PLC0415
        except ImportError as exc:
            logger.debug("[%s] skill_refs tools skipped (import): %s", _DCC_NAME, exc)
            return
        try:
            skills = self._scan_skill_metadata_for_sidecars(extra_skill_paths, include_bundled)
            register_skill_reference_docs_tools(self._server, skills=skills, dcc_name=_DCC_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] skill_refs tools failed: %s", _DCC_NAME, exc)

    def _register_introspect_tools(self) -> None:
        """Register the four ``dcc_introspect__*`` tools (core)."""
        try:
            from dcc_mcp_core.introspect import register_introspect_tools  # noqa: PLC0415
        except ImportError as exc:
            logger.debug("[%s] introspect tools skipped (import): %s", _DCC_NAME, exc)
            return
        try:
            register_introspect_tools(self._server, dcc_name=_DCC_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] introspect tools failed: %s", _DCC_NAME, exc)

    def _register_feedback_tool(self) -> None:
        """Register the ``dcc_feedback__report`` MCP tool (core)."""
        try:
            from dcc_mcp_core.feedback import register_feedback_tool  # noqa: PLC0415
        except ImportError as exc:
            logger.debug("[%s] feedback tool skipped (import): %s", _DCC_NAME, exc)
            return
        try:
            register_feedback_tool(self._server, dcc_name=_DCC_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] feedback tool failed: %s", _DCC_NAME, exc)

    def _register_qt_ui_inspector(self) -> None:
        """Adopt the shared core ``qt_ui_inspector__*`` tools (main-thread routed)."""
        try:
            _qt_inspector.register_3dsmax_qt_ui_inspector(
                self._server,
                dcc_name=_DCC_NAME,
                dispatcher=self._max_dispatcher,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] qt-ui-inspector registration failed: %s", _DCC_NAME, exc)

    def _register_capability_manifest_tool(self) -> None:
        """Register the ``dcc_capability_manifest`` MCP tool."""
        try:
            register_capability_mcp_tool(self, builder=self._capability_builder)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] capability manifest MCP tool registration failed: %s", _DCC_NAME, exc)

    def _attach_project_tools(self) -> None:
        """Register the four ``project_*`` MCP tools."""
        try:
            self._project_tools = _project_tools.attach_to_server(self)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] project tools registration failed: %s", _DCC_NAME, exc)

    def _attach_resources(self) -> None:
        """Publish ``scene://current`` + dynamic resource producers."""
        try:
            self._resources = _resources.install_resources(
                self,
                snapshot_provider=self._snapshot_provider_impl.collect,
                busy_checker=_executor.is_busy,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] resources registration failed: %s", _DCC_NAME, exc)

    # ── Readiness (parity #184) ────────────────────────────────────────────────

    def readiness_report(self) -> dict:
        """Return the current six-state readiness snapshot as a dict.

        Keys: ``process`` / ``dispatcher`` / ``dcc`` / ``skill_catalog`` /
        ``host_execution_bridge`` / ``main_thread_executor`` (all booleans).
        """
        return self._readiness.report()

    @property
    def readiness(self) -> "_readiness.ReadinessBinder":
        """Expose the :class:`ReadinessBinder` for tests and orchestrators."""
        return self._readiness

    # ── Gateway capability manifest + metadata (parity #163 / #165) ─────────────

    def build_capability_manifest(self, *, loaded_only: bool = False) -> dict:
        """Return the compact 3ds Max capability manifest as a dict."""
        records = self._capability_builder.build()
        if loaded_only:
            records = [r for r in records if r.loaded]
        instance_id = getattr(self, "instance_id", None)
        scene = getattr(self._config, "scene", None)
        version = getattr(self._config, "dcc_version", None)
        return build_manifest_payload(
            records,
            dcc_name=_DCC_NAME,
            dcc_version=version,
            scene=scene,
            instance_id=instance_id,
        )

    def publish_capability_snapshot(self, *, reason: str = "manual") -> bool:
        """Push current 3ds Max context into the gateway registry (best-effort)."""
        if not self.is_running:
            return False
        gateway_port = getattr(self._config, "gateway_port", 0)
        if not gateway_port or gateway_port <= 0:
            return False
        try:
            meta = collect_gateway_metadata(self._snapshot_provider_impl)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] capability snapshot: provider failed: %s", _DCC_NAME, exc)
            return False
        if not any((meta.get("scene"), meta.get("version"), meta.get("display_name"))):
            logger.debug("[%s] capability snapshot (%s): skipped — no actionable state", _DCC_NAME, reason)
            return False
        try:
            ok = self.update_gateway_metadata(
                scene=meta.get("scene"),
                version=meta.get("version"),
                documents=meta.get("documents"),
                display_name=meta.get("display_name"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] update_gateway_metadata failed (%s): %s", _DCC_NAME, reason, exc)
            return False
        return bool(ok)

    def _strict_skill_scan(
        self,
        extra_skill_paths: Optional[List[str]] = None,
        include_bundled: bool = True,
    ) -> None:
        """Re-scan skill paths with core strict validation enabled."""
        scan_paths = self.collect_skill_search_paths(
            extra_paths=extra_skill_paths,
            include_bundled=include_bundled,
            filter_existing=True,
        )
        scan_and_load_strict(extra_paths=scan_paths, dcc_name=_DCC_NAME)

    # ── Progressive skill loading ──────────────────────────────────────────────

    def discover_skills(
        self,
        extra_paths: Optional[List[str]] = None,
    ) -> int:
        """Scan skill directories and register tool metadata without importing scripts.

        Args:
            extra_paths: Additional directories to scan beyond the configured paths.

        Returns:
            Number of newly discovered skills (0 if server is not running).
        """
        if self._handle is None:
            logger.warning("discover_skills called before server was started")
            return 0
        paths = self._collect_skill_paths()
        if extra_paths:
            paths = list(extra_paths) + paths
        count = self._server.discover(extra_paths=paths, dcc_name=_DCC_NAME)
        logger.debug("MaxMcpServer: discovered %d new skill(s)", count)
        return count

    def load_skill(self, skill_name: str) -> bool:
        """Load a skill by name.

        Args:
            skill_name: Skill name as declared in ``SKILL.md`` (e.g. ``"3dsmax-scene"``).

        Returns:
            ``True`` when the core server accepted the load request.
        """
        if self._handle is None:
            return False
        try:
            self._server.load_skill(skill_name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MaxMcpServer: load_skill(%r) failed: %s", skill_name, exc)
            return False
        return True

    def unload_skill(self, skill_name: str) -> bool:
        """Unload a skill, removing its tools from the registry.

        Args:
            skill_name: Skill name to unload.

        Returns:
            ``True`` when the core server accepted the unload request.
        """
        if self._handle is None:
            return False
        try:
            self._server.unload_skill(skill_name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MaxMcpServer: unload_skill(%r) failed: %s", skill_name, exc)
            return False
        return True

    def list_skills(self, status: Optional[str] = None) -> List[Dict[str, Any]]:  # type: ignore[override]
        """List all discovered skills with their load status."""
        if self._handle is None:
            return []
        return list(self._server.list_skills(status=status))

    def search_skills(  # type: ignore[override]
        self,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        dcc: Optional[str] = None,
        scope: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Search for skills matching the given criteria."""
        if self._handle is None:
            return []
        try:
            base = list(
                self._server.search_skills(
                    query=query,
                    tags=tags or [],
                    dcc=dcc or _DCC_NAME,
                    scope=scope,
                    limit=limit,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("MaxMcpServer: search_skills failed: %s", exc)
            return []

        # Parity #313: opt-in semantic augmentation. ``base`` ordering is
        # preserved (promote, never demote); morphology-only recalls append.
        semantic = getattr(self, "_semantic", None)
        if semantic is not None and query:
            try:
                return semantic.augment(base, query, self.list_skills(), limit=limit)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[%s] semantic augment failed: %s", _DCC_NAME, exc)
        return base

    def is_skill_loaded(self, skill_name: str) -> bool:
        """Return ``True`` if the named skill is currently loaded."""
        if self._handle is None:
            return False
        return self._server.is_loaded(skill_name)

    def loaded_skill_count(self) -> int:
        """Return the number of currently loaded skills."""
        if self._handle is None:
            return 0
        return self._server.loaded_count()


# ── module-level singleton helpers ────────────────────────────────────────────

_server_instance: Optional[MaxMcpServer] = None


def start_server(
    port: int = DEFAULT_PORT,
    extra_skill_paths: Optional[List[str]] = None,
    options: Optional[MaxServerOptions] = None,
    register_builtins: bool = True,
    include_bundled: bool = True,
    enable_hot_reload: bool = False,
    gateway_port: Optional[int] = DEFAULT_GATEWAY_PORT,
    registry_dir: Optional[str] = None,
    dcc_version: Optional[str] = None,
    scene: Optional[str] = None,
    enable_gateway_failover: Optional[bool] = None,
    metrics_enabled: Optional[bool] = None,
    job_storage_path: Optional[str] = None,
    job_recovery: Optional[str] = None,
    enable_workflows: Optional[bool] = None,
    dcc_pid: Optional[int] = None,
    dcc_window_title: Optional[str] = None,
    dcc_window_handle: Optional[int] = None,
    snapshot_provider: Optional[Any] = None,
    dispatcher: Optional[Any] = None,
    execution_bridge: Optional[Any] = None,
    minimal_mode: Optional["MinimalModeConfig"] = None,
    strict_scan: Optional[bool] = None,
) -> MaxMcpServer:
    """Start the 3ds Max MCP server (creates a process-level singleton).

    The first call creates and starts the server.  Subsequent calls return the
    existing instance without restarting it.

    Args:
        port: Preferred TCP port (default 0 = random instance port).
        extra_skill_paths: Additional skill directories beyond built-ins.
        register_builtins: If ``True``, discover and load all skills.
        include_bundled: Include dcc-mcp-core bundled skills.
        enable_hot_reload: Enable skill hot-reload on file changes.
        gateway_port: Stable gateway port (default 9765).
        registry_dir: Shared registry directory.
        dcc_version: 3ds Max version for gateway registry.
        scene: Currently open scene file path for the gateway registry.
        enable_gateway_failover: Enable automatic gateway failover.
        metrics_enabled: Force Prometheus ``/metrics`` (``None`` = env ``DCC_MCP_3DSMAX_METRICS``).
        job_storage_path: SQLite job DB path (``None`` = env / default).
        job_recovery: Job recovery policy (``"drop"`` or ``"requeue"``).
        enable_workflows: Enable workflow MCP tools (``None`` = env).
        dispatcher: Optional host dispatcher for main-thread execution.
        execution_bridge: Optional execution bridge supplied by dcc-mcp-core.
        minimal_mode: Optional core progressive-loading configuration.
        strict_scan: Validate skill directories after discovery.

    Returns:
        The running :class:`MaxMcpServer` instance.
    """
    global _server_instance  # noqa: PLW0603
    if _server_instance is not None and _server_instance.is_running:
        return _server_instance

    if options is None:
        options = MaxServerOptions(
            port=port,
            extra_skill_paths=extra_skill_paths,
            gateway_port=gateway_port,
            registry_dir=registry_dir,
            dcc_version=dcc_version,
            scene=scene,
            enable_gateway_failover=enable_gateway_failover,
            metrics_enabled=metrics_enabled,
            job_storage_path=job_storage_path,
            job_recovery=job_recovery,
            enable_workflows=enable_workflows,
            dcc_pid=dcc_pid,
            dcc_window_title=dcc_window_title,
            dcc_window_handle=dcc_window_handle,
            snapshot_provider=snapshot_provider,
            dispatcher=dispatcher,
            execution_bridge=execution_bridge,
        )

    _server_instance = MaxMcpServer(options=options)
    if register_builtins:
        _server_instance.register_builtin_actions(
            include_bundled=include_bundled,
            minimal_mode=minimal_mode,
            strict_scan=strict_scan,
        )
    if enable_hot_reload:
        _server_instance.enable_hot_reload()
    _server_instance.start()
    return _server_instance


def prepare_server(
    extra_skill_paths: Optional[List[str]] = None,
    register_builtins: bool = True,
    include_bundled: bool = True,
    options: Optional[MaxServerOptions] = None,
) -> MaxMcpServer:
    """Create the singleton server and register tools without starting HTTP."""
    global _server_instance  # noqa: PLW0603
    if _server_instance is not None:
        return _server_instance

    if options is None:
        options = MaxServerOptions(
            port=0,
            gateway_port=DEFAULT_GATEWAY_PORT,
            extra_skill_paths=extra_skill_paths,
            enable_gateway_failover=False,
            job_storage_path="",
        )

    _server_instance = MaxMcpServer(options=options)
    if register_builtins:
        _server_instance.register_builtin_actions(include_bundled=include_bundled)
    return _server_instance


def stop_server() -> None:
    """Stop the running 3ds Max MCP server."""
    global _server_instance  # noqa: PLW0603
    if _server_instance is None:
        return
    _server_instance.stop()
    _server_instance = None


def get_server() -> Optional[MaxMcpServer]:
    """Return the current server instance, or ``None`` if not started."""
    return _server_instance
