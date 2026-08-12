"""Environment-variable resolution for ``MaxMcpServer``.

Centralises every ``DCC_MCP_3DSMAX_*`` env var used by the server so the
composition root in :mod:`dcc_mcp_3dsmax.server` stays a thin orchestrator.

All helpers are pure functions: they read :data:`os.environ` and return
plain Python values; they never mutate global state.
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Public env-var names ─────────────────────────────────────────────────────
ENV_METRICS = "DCC_MCP_3DSMAX_METRICS"
ENV_JOB_STORAGE = "DCC_MCP_3DSMAX_JOB_STORAGE"
ENV_JOB_RECOVERY = "DCC_MCP_3DSMAX_JOB_RECOVERY"
ENV_3DSMAX_PATH = "DCC_MCP_3DSMAX_PATH"
ENV_3DSMAX_VERSION = "DCC_MCP_3DSMAX_VERSION"
#: When set to ``"1"``, ``register_builtin_actions`` runs
#: :func:`dcc_mcp_core.scan_and_load_strict` after discovery so any
#: silently-skipped skill directory raises ``ValueError`` at startup
#: instead of disappearing into a debug-level log line.
ENV_STRICT_SKILL_SCAN = "DCC_MCP_3DSMAX_STRICT_SKILL_SCAN"
#: Opt-in workflow engine surface
#: (``workflows.run``, ``workflows.resume``, ``workflows.list_runs`` MCP
#: tools).  Off by default so the minimal-mode tools/list stays small.
ENV_ENABLE_WORKFLOWS = "DCC_MCP_3DSMAX_ENABLE_WORKFLOWS"
#: When set, overrides the ``enable_gateway_failover`` constructor flag
ENV_ENABLE_GATEWAY_FAILOVER = "DCC_MCP_3DSMAX_ENABLE_GATEWAY_FAILOVER"
#: When ``"1"`` / ``"true"`` / ``"yes"``, ``execute_python`` returns a
#: structured refusal so agents must use ``load_skill`` + typed tools.
ENV_DISABLE_EXECUTE_PYTHON = "DCC_MCP_3DSMAX_DISABLE_EXECUTE_PYTHON"
#: When set, both ``execute_python`` and ``execute_maxscript`` are refused
ENV_DISABLE_ARBITRARY_SCRIPT = "DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT"
#: Optional per-tool opt-out for MAXScript
ENV_DISABLE_EXECUTE_MAXSCRIPT = "DCC_MCP_3DSMAX_DISABLE_EXECUTE_MAXSCRIPT"
#: Advisory readiness timeout (positive int seconds) for ``/v1/readyz``.
ENV_READINESS_TIMEOUT_SECS = "DCC_MCP_3DSMAX_READINESS_TIMEOUT_SECS"
#: ``"0"`` disables ``scene://current`` + dynamic resource producers.
ENV_RESOURCES = "DCC_MCP_3DSMAX_RESOURCES"
#: ``"0"`` disables the four ``project_*`` MCP tools.
ENV_PROJECT_TOOLS = "DCC_MCP_3DSMAX_PROJECT_TOOLS"
#: Falsey token skips the shared ``qt_ui_inspector__*`` tools.
ENV_QT_UI_INSPECTOR = "DCC_MCP_3DSMAX_QT_UI_INSPECTOR"
#: ``"1"`` enables morphology-aware semantic recall in ``search_skills``.
ENV_SEMANTIC_INDEX = "DCC_MCP_3DSMAX_SEMANTIC_INDEX"
#: ``hashed`` (default) or ``onnx`` embedder for the semantic index.
ENV_SEMANTIC_EMBEDDER = "DCC_MCP_3DSMAX_SEMANTIC_EMBEDDER"
#: Default SQLite filename inside the platform data directory.
DEFAULT_JOB_DB_FILENAME = "jobs.db"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def resolve_execute_python_disabled() -> bool:
    """Return True when ``execute_python`` must refuse all calls."""
    if _env_truthy(ENV_DISABLE_ARBITRARY_SCRIPT):
        return True
    return _env_truthy(ENV_DISABLE_EXECUTE_PYTHON)


def resolve_execute_maxscript_disabled() -> bool:
    """Return True when ``execute_maxscript`` must refuse all calls."""
    if _env_truthy(ENV_DISABLE_ARBITRARY_SCRIPT):
        return True
    return _env_truthy(ENV_DISABLE_EXECUTE_MAXSCRIPT)


def resolve_metrics_enabled(metrics_enabled: Optional[bool]) -> bool:
    """Resolve the Prometheus ``/metrics`` endpoint flag.

    Priority: explicit argument > ``DCC_MCP_3DSMAX_METRICS=1`` > ``False``.
    """
    if metrics_enabled is not None:
        return bool(metrics_enabled)
    return os.environ.get(ENV_METRICS, "").strip() == "1"


def resolve_job_storage(job_storage_path: Optional[str]) -> Optional[str]:
    """Resolve the SQLite job-storage path.

    Returns ``None`` when callers should leave whatever path
    :class:`DccServerBase._init_job_persistence` selected.  Returns the
    empty string ``""`` when the caller passed ``""`` explicitly to
    request in-memory operation (no persistence).

    Priority order:
    1. Explicit ``job_storage_path`` argument (when not ``None``).
    2. ``DCC_MCP_3DSMAX_JOB_STORAGE`` env var.
    3. ``<platform_data_dir>/dcc-mcp-3dsmax/jobs.db`` (auto-created).
    """
    if job_storage_path is not None:
        if not str(job_storage_path).strip():
            return ""  # explicit "disable persistence"
        return job_storage_path

    env_val = os.environ.get(ENV_JOB_STORAGE)
    if env_val is not None:
        return env_val

    try:
        from dcc_mcp_core import get_data_dir  # noqa: PLC0415

        data_dir = Path(get_data_dir()) / "dcc-mcp-3dsmax"
        data_dir.mkdir(parents=True, exist_ok=True)
        return str(data_dir / DEFAULT_JOB_DB_FILENAME)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not resolve default job storage path: %s", exc)
        return None


def resolve_job_recovery(job_recovery: Optional[str]) -> str:
    """Resolve the interrupted-job recovery policy.

    Returns either ``"drop"`` (default) or ``"requeue"``.
    """
    raw: Optional[str]
    if job_recovery is not None:
        raw = job_recovery
    else:
        raw = os.environ.get(ENV_JOB_RECOVERY, "drop")
    normalised = (raw or "drop").strip().lower()
    return normalised if normalised in ("drop", "requeue") else "drop"


def resolve_strict_skill_scan(strict: Optional[bool] = None) -> bool:
    """Resolve whether to run :func:`scan_and_load_strict` after discovery."""
    if strict is not None:
        return bool(strict)
    return os.environ.get(ENV_STRICT_SKILL_SCAN, "").strip() == "1"


def resolve_enable_workflows(enable_workflows: Optional[bool] = None) -> bool:
    """Resolve whether to enable the upstream workflow engine."""
    if enable_workflows is not None:
        return bool(enable_workflows)
    return os.environ.get(ENV_ENABLE_WORKFLOWS, "").strip() == "1"


def resolve_enable_gateway_failover(
    enable_gateway_failover: Optional[bool],
    *,
    default: bool = True,
) -> bool:
    """Resolve gateway failover from constructor + optional env override."""
    if enable_gateway_failover is not None:
        return bool(enable_gateway_failover)
    raw = os.environ.get(ENV_ENABLE_GATEWAY_FAILOVER, "").strip()
    if raw:
        return _env_truthy(ENV_ENABLE_GATEWAY_FAILOVER)
    return bool(default)


def resolve_3dsmax_path() -> Optional[str]:
    """Resolve 3ds Max executable path."""
    path = os.environ.get(ENV_3DSMAX_PATH, "").strip()
    return path if path else None


def resolve_3dsmax_version() -> Optional[str]:
    """Resolve 3ds Max version from environment."""
    version = os.environ.get(ENV_3DSMAX_VERSION, "").strip()
    return version if version else None


# ── Optional core-integration toggles ───────────────────────────────────────
# Thin wrappers that delegate to each feature module so a single registry of
# ``DCC_MCP_3DSMAX_*`` names lives here while resolution logic stays beside the
# feature it gates.


def resolve_readiness_timeout_secs(timeout_secs: Optional[int] = None) -> Optional[int]:
    """Resolve the advisory readiness timeout (positive int seconds, or ``None``)."""
    from dcc_mcp_3dsmax._readiness import resolve_readiness_timeout_secs as _resolve  # noqa: PLC0415

    return _resolve(timeout_secs)


def resolve_resources_enabled(flag: Optional[bool] = None) -> bool:
    """Resolve whether ``scene://current`` resource wiring should run."""
    from dcc_mcp_3dsmax._resources import resolve_enabled as _resolve  # noqa: PLC0415

    return _resolve(flag)


def resolve_project_tools_enabled(flag: Optional[bool] = None) -> bool:
    """Resolve whether the four ``project_*`` MCP tools should be registered."""
    from dcc_mcp_3dsmax._project_tools import resolve_enabled as _resolve  # noqa: PLC0415

    return _resolve(flag)


def resolve_qt_ui_inspector_enabled() -> bool:
    """Resolve whether the shared ``qt_ui_inspector__*`` tools should register."""
    from dcc_mcp_3dsmax._qt_inspector import resolve_qt_ui_inspector_enabled as _resolve  # noqa: PLC0415

    return _resolve()


def resolve_semantic_index_enabled() -> bool:
    """Resolve whether opt-in semantic recall augments ``search_skills``."""
    from dcc_mcp_3dsmax._semantic_index import resolve_semantic_index_enabled as _resolve  # noqa: PLC0415

    return _resolve()


def resolve_semantic_embedder_kind() -> str:
    """Resolve the semantic embedder kind: ``"hashed"`` (default) or ``"onnx"``."""
    from dcc_mcp_3dsmax._semantic_index import resolve_embedder_kind as _resolve  # noqa: PLC0415

    return _resolve()
