"""Redo the most recently undone 3ds Max operations."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._undo_utils import REDO_DIRECTION, run_history_steps
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(count: int = 1, allow_no_op: bool = False) -> Dict[str, Any]:
    """Redo the last *count* undone host operations, verifying every step.

    Redo is only available until the next edit is made: once a new operation is
    performed the redo stack is cleared and this call reports a no-op instead of
    pretending it restored something.
    """
    return run_history_steps(get_runtime(), direction=REDO_DIRECTION, count=count, allow_no_op=bool(allow_no_op))
