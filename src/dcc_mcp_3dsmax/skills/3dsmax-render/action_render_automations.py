"""Arm a completion signal for the next render and report when it finishes."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from dcc_mcp_3dsmax._render_signals import RENDER_AUTOMATION_ACTIONS, disarm_render_automations, render_automations
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    actions: Optional[Sequence[str]] = None,
    wait: bool = True,
    timeout_sec: float = 120.0,
    signal_file: Optional[str] = None,
    message: Optional[str] = None,
    output_path: Optional[str] = None,
    label: Optional[str] = None,
    disarm: bool = False,
) -> Dict[str, Any]:
    """Install a completion signal for the next render, then report the outcome."""
    runtime = get_runtime()
    if disarm:
        return disarm_render_automations(runtime, label=label)
    normalized = [str(action).strip().lower() for action in actions] if actions else None
    supported = list(RENDER_AUTOMATION_ACTIONS)
    if normalized is not None:
        unsupported = [action for action in normalized if action not in supported]
        if unsupported:
            return {
                "success": False,
                "status": "error",
                "message": "Unsupported render automation action(s): {}".format(", ".join(sorted(unsupported))),
                "data": {"actions": normalized, "supported_actions": supported},
            }
    return render_automations(
        runtime,
        actions=normalized,
        wait=bool(wait),
        timeout_sec=timeout_sec,
        signal_file=signal_file,
        message=message,
        output_path=output_path,
        label=label,
    )
