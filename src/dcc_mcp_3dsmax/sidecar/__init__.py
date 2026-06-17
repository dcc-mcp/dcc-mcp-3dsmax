"""3ds Max sidecar bridge helpers."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    "dispatch": ("dcc_mcp_3dsmax.sidecar._dispatcher", "dispatch"),
    "dispatch_payload": ("dcc_mcp_3dsmax.sidecar._dispatcher", "dispatch_payload"),
    "start_bridge": ("dcc_mcp_3dsmax.sidecar.bridge", "start_bridge"),
    "stop_bridge": ("dcc_mcp_3dsmax.sidecar.bridge", "stop_bridge"),
    "start_qt_bridge": ("dcc_mcp_3dsmax.sidecar.qt_bridge", "start_qt_bridge"),
    "stop_qt_bridge": ("dcc_mcp_3dsmax.sidecar.qt_bridge", "stop_qt_bridge"),
}


def __getattr__(name: str) -> Any:
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
    "dispatch",
    "dispatch_payload",
    "start_bridge",
    "start_qt_bridge",
    "stop_bridge",
    "stop_qt_bridge",
]
