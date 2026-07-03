"""Get 3ds Max session and scene metadata."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._scene_utils import iter_scene_nodes
from dcc_mcp_3dsmax._version_probe import get_3dsmax_version_string
from dcc_mcp_3dsmax.api import get_runtime, with_max


def _normalize_unit(value: Any) -> str:
    """Normalize a unit value from MAXScript to a clean lowercase string."""
    if callable(value):
        try:
            raw = str(value())
        except Exception:  # noqa: BLE001
            return "unknown"
    else:
        raw = str(value)
    return raw.lstrip("#").strip().lower() or "unknown"


@with_max
def main() -> Dict[str, Any]:
    """Return session and scene metadata."""
    rt = get_runtime()
    scene_name = str(getattr(rt, "maxFileName", "") or "Untitled")
    scene_path = str(getattr(rt, "maxFilePath", "") or "")

    units = "unknown"
    system_unit: Optional[str] = None
    display_unit: Optional[str] = None
    if hasattr(rt, "units"):
        u = rt.units
        system_type = getattr(u, "SystemType", None)
        if system_type is not None:
            try:
                system_unit = _normalize_unit(system_type)
                units = system_unit
            except Exception:  # noqa: BLE001
                system_unit = None
        display_type = getattr(u, "DisplayType", None)
        if display_type is not None:
            try:
                display_unit = _normalize_unit(display_type)
            except Exception:  # noqa: BLE001
                display_unit = None

    nodes = iter_scene_nodes(rt)
    return {
        "success": True,
        "message": "Retrieved scene metadata",
        "data": {
            "scene_name": scene_name,
            "scene_path": scene_path,
            "node_count": len(nodes),
            "units": units,
            "system_unit": system_unit,
            "display_unit": display_unit,
            "3dsmax_version": get_3dsmax_version_string(),
        },
    }
