"""Create, query, and close the dedicated agent viewport.

The agent viewport is a separate floating viewport for agent work: it has its
own shading and camera so the user's active view is never moved. Creating it
requires a host that exposes an extended/floating viewport factory; a host that
does not is reported as a failure instead of silently falling back to the
user's viewport.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._viewport_utils import (
    SHADING_MODES,
    agent_viewport_status,
    close_agent_viewport,
    ensure_agent_viewport,
    resolve_viewport_camera,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

DEFAULT_AGENT_VIEWPORT = "dcc_mcp_agent_viewport"


@with_max
def main(
    action: str = "ensure",
    name: str = DEFAULT_AGENT_VIEWPORT,
    shading: Optional[str] = None,
    shading_value: Optional[int] = None,
    camera_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Ensure, inspect, or close the dedicated agent viewport."""
    runtime = get_runtime()
    action = str(action or "ensure").lower()
    if action not in ("ensure", "status", "close"):
        return {
            "success": False,
            "status": "error",
            "message": "Unsupported agent viewport action",
            "data": {"action": action, "supported_actions": ["ensure", "status", "close"]},
        }
    if action == "status":
        return agent_viewport_status(runtime, name)
    if action == "close":
        return close_agent_viewport(runtime, name)

    options: Dict[str, Any] = {}
    if shading is not None:
        normalized = str(shading).strip().lower()
        if normalized not in SHADING_MODES:
            return {
                "success": False,
                "status": "error",
                "message": "Unsupported shading mode",
                "data": {"shading": shading, "supported_shading": list(SHADING_MODES)},
            }
        options["shading"] = normalized
    elif shading_value is not None:
        # Renderers index their shading dropdown; an explicit index bypasses
        # the adapter's name table the same way the light enums do.
        options["shading"] = int(shading_value)
    if camera_name is not None:
        camera, error = resolve_viewport_camera(runtime, camera_name)
        if error is not None:
            return {
                "success": False,
                "status": "error",
                "message": error,
                "data": {"camera_name": camera_name},
            }
        if camera is not None:
            options["camera"] = camera
    return ensure_agent_viewport(runtime, name, options=options)
