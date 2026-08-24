"""Execute explicit Python code in 3ds Max."""

from __future__ import annotations

import contextlib
import io
import traceback
from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._env import resolve_execute_python_disabled
from dcc_mcp_3dsmax._scene_utils import json_safe
from dcc_mcp_3dsmax.api import get_runtime, max_error, with_max


@with_max
def main(code: str, confirm_execution: bool, expression: Optional[str] = None) -> Dict[str, Any]:
    """Execute Python code with ``pymxs``, ``rt``, and ``runtime`` in scope."""
    if resolve_execute_python_disabled():
        return max_error("execute_python is disabled by environment configuration")
    if confirm_execution is not True:
        return max_error("confirm_execution must be true for arbitrary Python execution")
    if not isinstance(code, str) or not code.strip():
        return max_error("code must be a non-empty Python string")
    if expression is not None and (not isinstance(expression, str) or not expression.strip()):
        return max_error("expression must be a non-empty Python expression when provided")

    import pymxs  # noqa: PLC0415

    rt = get_runtime()
    stdout = io.StringIO()
    stderr = io.StringIO()
    namespace: Dict[str, Any] = {
        "pymxs": pymxs,
        "rt": rt,
        "runtime": rt,
    }

    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(code, "<dcc_mcp_3dsmax_execute_python>", "exec"), namespace, namespace)  # noqa: S102
            if expression is not None:
                result = eval(  # noqa: S307
                    compile(expression, "<dcc_mcp_3dsmax_execute_python_expression>", "eval"),
                    namespace,
                    namespace,
                )
            else:
                result = namespace.get("result")
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "message": "Python execution failed",
            "data": {
                "error": str(exc),
                "exception_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
            },
        }

    return {
        "success": True,
        "message": "Executed Python code",
        "data": {
            "result": json_safe(result),
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        },
    }
