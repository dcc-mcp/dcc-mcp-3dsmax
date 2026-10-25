"""Get 3ds Max scene information."""

# Import future modules
from __future__ import annotations

# Import built-in modules
from typing import Any, Dict, Optional

# Import local modules
from dcc_mcp_3dsmax._scene_lifecycle import _string_value
from dcc_mcp_3dsmax.api import max_success, with_max


def _normalize_unit(value: Any) -> str:
    """Normalize a unit value from MAXScript to a clean lowercase string.

    Native Name values must be serialized even when their wrappers advertise
    callability. Python readers remain supported by the shared normalizer.
    """
    try:
        return _string_value(value).lower()
    except Exception:  # noqa: BLE001
        return "unknown"


@with_max
def main() -> Dict[str, Any]:
    """Get basic information about the current scene.

    Returns:
        Dictionary with scene information.
    """
    import pymxs

    rt = pymxs.runtime

    # Get scene name
    scene_name = rt.maxFileName if rt.maxFileName else "Untitled"

    # Get node count
    all_nodes = rt.objects
    node_count = len(list(all_nodes))

    # Get units — handle both callable and plain name-literal forms
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

    return max_success(
        "Retrieved scene information",
        scene_name=scene_name,
        node_count=node_count,
        units=units,
        system_unit=system_unit,
        display_unit=display_unit,
    )
