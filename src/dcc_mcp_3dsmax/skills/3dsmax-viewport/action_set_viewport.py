"""Set display options on a viewport with verified writes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._render_utils import SETTING_REJECTED, SETTING_UNVERIFIED, render_error, render_success
from dcc_mcp_3dsmax._viewport_utils import (
    SHADING_MODES,
    VIEWPORT_LAYOUTS,
    active_viewport,
    agent_viewport_status,
    apply_viewport_options,
    find_agent_viewport,
    resolve_viewport_camera,
    viewport_summary,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max

DEFAULT_AGENT_VIEWPORT = "dcc_mcp_agent_viewport"


@with_max
def main(
    target: str = "agent",
    name: str = DEFAULT_AGENT_VIEWPORT,
    shading: Optional[str] = None,
    shading_value: Optional[int] = None,
    layout: Optional[str] = None,
    edged_faces: Optional[bool] = None,
    grid: Optional[bool] = None,
    safe_frame: Optional[bool] = None,
    statistics: Optional[bool] = None,
    camera_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Set shading, layout, and display toggles on the agent or active viewport."""
    runtime = get_runtime()
    target = str(target or "agent").lower()
    if target not in ("agent", "active"):
        return render_error(
            "Unsupported viewport target", target=target, supported_targets=["agent", "active"]
        )
    if layout is not None and str(layout).strip().lower() not in VIEWPORT_LAYOUTS:
        return render_error(
            "Unsupported viewport layout",
            layout=layout,
            supported_layouts=list(VIEWPORT_LAYOUTS),
        )
    if shading is not None:
        normalized = str(shading).strip().lower()
        if normalized not in SHADING_MODES:
            return render_error(
                "Unsupported shading mode", shading=shading, supported_shading=list(SHADING_MODES)
            )

    if target == "agent":
        viewport = find_agent_viewport(runtime, name)
        if viewport is None:
            return render_error(
                "No agent viewport named {} is open; create it with the agent_viewport tool "
                "instead of changing the user's view".format(name),
                name=name,
                status=agent_viewport_status(runtime, name).get("data", {}),
            )
    else:
        viewport = active_viewport(runtime)
        if viewport is None:
            return render_error("No active viewport is available on this host", target=target)

    options: Dict[str, Any] = {}
    if shading is not None:
        options["shading"] = str(shading).strip().lower()
    elif shading_value is not None:
        options["shading"] = int(shading_value)
    if layout is not None:
        options["layout"] = str(layout).strip().lower()
    for option, value in (
        ("edged_faces", edged_faces),
        ("grid", grid),
        ("safe_frame", safe_frame),
        ("statistics", statistics),
    ):
        if value is not None:
            options[option] = bool(value)
    if camera_name is not None:
        camera, error = resolve_viewport_camera(runtime, camera_name)
        if error is not None:
            return render_error(error, camera_name=camera_name, target=target)
        if camera is not None:
            options["camera"] = camera
    if not options:
        return render_success(
            "No viewport options requested",
            target=target,
            viewport=viewport_summary(viewport),
            warnings=[],
        )

    rows = apply_viewport_options(runtime, viewport, options)
    warnings = [row["warning"] for row in rows if row["status"] == SETTING_UNVERIFIED and row.get("warning")]
    warnings.extend(warning for row in rows for warning in row.get("warnings", []))
    rejected = [row for row in rows if row["status"] == SETTING_REJECTED]
    data = {
        "target": target,
        "viewport": viewport_summary(viewport),
        "options": rows,
        "applied": [row["option"] for row in rows if row["status"] != SETTING_REJECTED],
        "rejected": [row["option"] for row in rejected],
        "warnings": warnings,
    }
    if rejected:
        data["errors"] = rejected
        return render_error(
            "The viewport rejected {} option(s): {}".format(
                len(rejected), ", ".join(sorted(str(row["option"]) for row in rejected))
            ),
            **data,
        )
    return render_success("Updated {} viewport option(s)".format(len(rows)), **data)
