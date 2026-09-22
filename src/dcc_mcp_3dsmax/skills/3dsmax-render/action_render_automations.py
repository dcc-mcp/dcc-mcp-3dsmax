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
    normalized: Optional[list] = None
    if actions is not None:
        # A bare string would iterate character by character and be rejected as
        # a list of unsupported single-letter actions; report the real problem.
        if isinstance(actions, str):
            return {
                "success": False,
                "status": "error",
                "message": "actions must be a list of strings, not a bare string",
                "data": {"actions": actions, "supported_actions": list(RENDER_AUTOMATION_ACTIONS)},
            }
        try:
            normalized = [str(action).strip().lower() for action in actions]
        except TypeError:
            return {
                "success": False,
                "status": "error",
                "message": "actions must be a list of strings",
                "data": {"actions": str(actions), "supported_actions": list(RENDER_AUTOMATION_ACTIONS)},
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
