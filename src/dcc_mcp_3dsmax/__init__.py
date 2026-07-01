"""dcc-mcp-3dsmax — 3ds Max plugin for the DCC Model Context Protocol ecosystem.

Embeds a standards-compliant MCP Streamable HTTP server (2025-03-26 spec)
directly inside 3ds Max using dcc-mcp-core.  No external gateway or dcc-mcp-ipc
required.

Quickstart (inside 3ds Max's MAXScript Listener):

    import dcc_mcp_3dsmax
    handle = dcc_mcp_3dsmax.start_server()
    # MCP host connects to http://127.0.0.1:9765/mcp
    handle.shutdown()

Skill authoring helpers (for 3ds Max skills developers):

    from dcc_mcp_3dsmax.api import (
        max_success, max_error, max_from_exception, with_max,
        require_param, validate_node_exists,
    )

    @with_max
    def create_box(width: float = 100.0, height: float = 100.0, depth: float = 100.0) -> dict:
        import pymxs
        rt = pymxs.runtime
        box_obj = rt.Box(width=width, height=height, depth=depth)
        return max_success("Created box", object_name=str(box_obj))
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
from importlib import import_module
from typing import Any

# Import local modules
from dcc_mcp_3dsmax.__version__ import __version__
from dcc_mcp_3dsmax._env import (
    ENV_DISABLE_ARBITRARY_SCRIPT,
    ENV_DISABLE_EXECUTE_MAXSCRIPT,
    ENV_DISABLE_EXECUTE_PYTHON,
    ENV_ENABLE_GATEWAY_FAILOVER,
    ENV_PROJECT_TOOLS,
    ENV_QT_UI_INSPECTOR,
    ENV_APP_UI,
    ENV_READINESS_TIMEOUT_SECS,
    ENV_RESOURCES,
    ENV_SEMANTIC_EMBEDDER,
    ENV_SEMANTIC_INDEX,
    resolve_enable_gateway_failover,
    resolve_execute_maxscript_disabled,
    resolve_execute_python_disabled,
    resolve_project_tools_enabled,
    resolve_qt_ui_inspector_enabled,
    resolve_app_ui_enabled,
    resolve_readiness_timeout_secs,
    resolve_resources_enabled,
    resolve_semantic_embedder_kind,
    resolve_semantic_index_enabled,
)
from dcc_mcp_3dsmax._version_probe import (
    get_3dsmax_version_number,
    get_3dsmax_version_string,
    is_3dsmax_available,
)
from dcc_mcp_3dsmax.api import (
    MissingParamError,
    clear_selection,
    get_param,
    get_runtime,
    get_selection,
    is_max_available,
    max_error,
    max_from_exception,
    max_success,
    max_warning,
    missing_param_error,
    require_any_param,
    require_param,
    select_nodes,
    with_max,
)
from dcc_mcp_3dsmax.capabilities import (
    get_3dsmax_capabilities,
    get_3dsmax_capabilities_dict,
)

_LAZY_EXPORTS = {
    "main": ("dcc_mcp_3dsmax.max_bootstrap", "main"),
    "start_sidecar_bridge": ("dcc_mcp_3dsmax.max_bootstrap", "start_sidecar_bridge"),
    "start_embedded_sidecar_bridge": ("dcc_mcp_3dsmax.max_bootstrap", "start_embedded_sidecar_bridge"),
    "stop_sidecar_bridge": ("dcc_mcp_3dsmax.max_bootstrap", "stop_sidecar_bridge"),
    "install_menu": ("dcc_mcp_3dsmax.menu", "install_menu"),
    "remove_menu": ("dcc_mcp_3dsmax.menu", "remove_menu"),
    "install_shutdown_callback": ("dcc_mcp_3dsmax.menu", "install_shutdown_callback"),
    "MaxMcpServer": ("dcc_mcp_3dsmax.server", "MaxMcpServer"),
    "MaxServerOptions": ("dcc_mcp_3dsmax.server", "MaxServerOptions"),
    "start_server": ("dcc_mcp_3dsmax.server", "start_server"),
    "stop_server": ("dcc_mcp_3dsmax.server", "stop_server"),
    "get_server": ("dcc_mcp_3dsmax.server", "get_server"),
    "prepare_server": ("dcc_mcp_3dsmax.server", "prepare_server"),
    "DEFAULT_GATEWAY_PORT": ("dcc_mcp_3dsmax.server", "DEFAULT_GATEWAY_PORT"),
    "DEFAULT_PORT": ("dcc_mcp_3dsmax.server", "DEFAULT_PORT"),
    "SERVER_NAME": ("dcc_mcp_3dsmax.server", "SERVER_NAME"),
    # Readiness (parity #184)
    "ReadinessBinder": ("dcc_mcp_3dsmax._readiness", "ReadinessBinder"),
    "install_readiness": ("dcc_mcp_3dsmax._readiness", "install_readiness"),
    "wait_until_ready": ("dcc_mcp_3dsmax._readiness", "wait_until_ready"),
    # Capability manifest + context snapshot (parity #163 / #165)
    "MaxCapabilityManifestBuilder": ("dcc_mcp_3dsmax._capability_manifest", "MaxCapabilityManifestBuilder"),
    "CapabilityRecord": ("dcc_mcp_3dsmax._capability_manifest", "CapabilityRecord"),
    "build_manifest_payload": ("dcc_mcp_3dsmax._capability_manifest", "build_manifest_payload"),
    "register_capability_mcp_tool": ("dcc_mcp_3dsmax._capability_manifest", "register_capability_mcp_tool"),
    "MaxContextSnapshotProvider": ("dcc_mcp_3dsmax.context_snapshot", "MaxContextSnapshotProvider"),
    "collect_gateway_metadata": ("dcc_mcp_3dsmax.context_snapshot", "collect_gateway_metadata"),
    "make_snapshot_provider": ("dcc_mcp_3dsmax.context_snapshot", "make_snapshot_provider"),
    # Resources (parity #187)
    "MaxResourceBinder": ("dcc_mcp_3dsmax._resources", "MaxResourceBinder"),
    "install_resources": ("dcc_mcp_3dsmax._resources", "install_resources"),
    # Project tools (parity #576)
    "ProjectToolsIntegration": ("dcc_mcp_3dsmax._project_tools", "ProjectToolsIntegration"),
    "MaxSceneResolver": ("dcc_mcp_3dsmax._project_tools", "MaxSceneResolver"),
    # Qt UI inspector (parity #307)
    "register_3dsmax_qt_ui_inspector": ("dcc_mcp_3dsmax._qt_inspector", "register_3dsmax_qt_ui_inspector"),
    # Semantic index (parity #313)
    "MaxSemanticIndex": ("dcc_mcp_3dsmax._semantic_index", "MaxSemanticIndex"),
    "build_semantic_index": ("dcc_mcp_3dsmax._semantic_index", "build_semantic_index"),
}


def __getattr__(name: str) -> Any:
    """Resolve heavier runtime exports only when callers actually use them."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    "__version__",
    # Server
    "MaxMcpServer",
    "MaxServerOptions",
    "start_server",
    "stop_server",
    "get_server",
    "prepare_server",
    "DEFAULT_GATEWAY_PORT",
    "DEFAULT_PORT",
    "SERVER_NAME",
    "main",
    "start_sidecar_bridge",
    "start_embedded_sidecar_bridge",
    "stop_sidecar_bridge",
    "install_menu",
    "remove_menu",
    "install_shutdown_callback",
    # Version
    "get_3dsmax_version_string",
    "get_3dsmax_version_number",
    "is_3dsmax_available",
    # Capabilities
    "get_3dsmax_capabilities",
    "get_3dsmax_capabilities_dict",
    # API helpers
    "max_success",
    "max_error",
    "max_warning",
    "max_from_exception",
    "with_max",
    "require_param",
    "require_any_param",
    "get_param",
    "missing_param_error",
    "MissingParamError",
    "is_max_available",
    "get_runtime",
    "get_selection",
    "clear_selection",
    "select_nodes",
    # Environment
    "ENV_DISABLE_EXECUTE_PYTHON",
    "ENV_DISABLE_EXECUTE_MAXSCRIPT",
    "ENV_DISABLE_ARBITRARY_SCRIPT",
    "ENV_ENABLE_GATEWAY_FAILOVER",
    "ENV_READINESS_TIMEOUT_SECS",
    "ENV_RESOURCES",
    "ENV_PROJECT_TOOLS",
    "ENV_QT_UI_INSPECTOR",
    "ENV_SEMANTIC_INDEX",
    "ENV_SEMANTIC_EMBEDDER",
    "resolve_enable_gateway_failover",
    "resolve_execute_python_disabled",
    "resolve_execute_maxscript_disabled",
    "resolve_readiness_timeout_secs",
    "resolve_resources_enabled",
    "resolve_project_tools_enabled",
    "resolve_qt_ui_inspector_enabled",
    "resolve_semantic_index_enabled",
    "resolve_semantic_embedder_kind",
    # Readiness (parity #184)
    "ReadinessBinder",
    "install_readiness",
    "wait_until_ready",
    # Capability manifest + context snapshot (parity #163 / #165)
    "MaxCapabilityManifestBuilder",
    "CapabilityRecord",
    "build_manifest_payload",
    "register_capability_mcp_tool",
    "MaxContextSnapshotProvider",
    "collect_gateway_metadata",
    "make_snapshot_provider",
    # Resources (parity #187)
    "MaxResourceBinder",
    "install_resources",
    # Project tools (parity #576)
    "ProjectToolsIntegration",
    "MaxSceneResolver",
    # Qt UI inspector (parity #307)
    "register_3dsmax_qt_ui_inspector",
    # Semantic index (parity #313)
    "MaxSemanticIndex",
    "build_semantic_index",
]
