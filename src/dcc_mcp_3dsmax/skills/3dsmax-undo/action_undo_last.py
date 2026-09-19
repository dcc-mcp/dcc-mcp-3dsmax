"""Undo the most recent 3ds Max operations."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._undo_utils import UNDO_DIRECTION, run_history_steps
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(count: int = 1, allow_no_op: bool = False) -> Dict[str, Any]:
    """Undo the last *count* host operations, verifying every step.

    ``count`` steps are requested one at a time and each one is compared against
    a scene fingerprint. When the host accepts an undo but the scene does not
    change, the stack is treated as exhausted: the call fails (or, with
    ``allow_no_op``, succeeds with an explicit warning) instead of reporting a
    success that undid nothing.
    """
    return run_history_steps(get_runtime(), direction=UNDO_DIRECTION, count=count, allow_no_op=bool(allow_no_op))
