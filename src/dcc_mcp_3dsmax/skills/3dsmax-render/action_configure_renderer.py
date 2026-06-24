"""Configure active renderer settings."""

from __future__ import annotations
from typing import Any, Dict, Mapping, Optional
from dcc_mcp_3dsmax._render_advanced import configure_renderer, set_renderer
from dcc_mcp_3dsmax._render_utils import render_settings
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    renderer_type: Optional[str] = None,
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    runtime = get_runtime()
    if renderer_type is not None:
        result = set_renderer(runtime, renderer_type)
        if not result.get("success"):
            return result
    if settings:
        return configure_renderer(runtime, settings=settings)
    return {"success": True, "status": "success", "data": render_settings(runtime)}
