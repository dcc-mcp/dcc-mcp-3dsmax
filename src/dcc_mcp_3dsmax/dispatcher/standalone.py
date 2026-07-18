"""Standalone (3dsmax -batch) dispatcher.

Synchronous dispatcher used outside the interactive 3ds Max UI:
``3dsmax -batch`` scripts.  No queue, no threads, no main-thread distinction.

See: https://github.com/dcc-mcp/dcc-mcp-3dsmax/issues/1
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_STANDALONE_MAX_LOCK = threading.RLock()


class MaxStandaloneDispatcher:
    """Dispatcher for ``3dsmax -batch`` contexts.

    All jobs execute directly on the calling thread — there is no event
    loop, no idle callbacks, and no notion of a "main thread" distinct from
    the caller.  This is the right choice for:

    - ``3dsmax -batch`` mode
    - Python scripting without UI
    """

    def submit(
        self,
        action_name: str,
        payload: Optional[str] = None,
        affinity: str = "any",
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute a job synchronously on the calling thread.

        The *affinity* parameter is accepted but ignored — standalone mode
        has no thread scheduling.
        """
        return {
            "request_id": action_name,
            "affinity": affinity,
            "success": True,
            "output": payload,
            "error": None,
        }

    def submit_callable(
        self,
        request_id: str,
        task: Callable[[], Any],
        affinity: str = "any",
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute a callable synchronously."""
        with _STANDALONE_MAX_LOCK:
            try:
                output = task()
                return {
                    "request_id": request_id,
                    "affinity": affinity,
                    "success": True,
                    "output": output,
                    "error": None,
                }
            except Exception as exc:
                return {
                    "request_id": request_id,
                    "affinity": affinity,
                    "success": False,
                    "output": None,
                    "error": str(exc),
                }

    def dispatch_callable(
        self,
        func: Callable[..., Any],
        *args: Any,
        affinity: str = "any",
        context: Any = None,
        action_name: Optional[str] = None,
        skill_name: Optional[str] = None,
        execution: Optional[str] = None,
        timeout_hint_secs: Optional[int] = None,
        **kwargs: Any,
    ) -> Any:
        """Run a callable directly using the core dispatcher protocol."""
        _ = (context, skill_name, execution, affinity)
        kwargs.pop("thread_affinity", None)
        kwargs.pop("timeout_ms", None)
        timeout_ms = timeout_hint_secs * 1000 if timeout_hint_secs is not None else None
        with _STANDALONE_MAX_LOCK:
            result = self.submit_callable(
                request_id=action_name or "dispatch",
                task=lambda: func(*args, **kwargs),
                affinity=affinity,
                timeout_ms=timeout_ms,
            )
            if not result.get("success", True):
                raise RuntimeError(result.get("error") or "dispatch_callable failed")
            return result.get("output")

    def submit_async_callable(
        self,
        request_id: str,
        task: Callable[[], Any],
        *,
        job_id: Optional[str] = None,
        progress_token: Optional[str] = None,
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        affinity: str = "any",
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute a callable synchronously and invoke ``on_complete``.

        Standalone mode has no background queue, so this executes immediately
        on the calling thread and returns a completed envelope.
        """
        result = self.submit_callable(request_id, task, affinity, timeout_ms)
        result["job_id"] = job_id
        result["status"] = "completed" if result.get("success") else "failed"
        if on_complete is not None:
            try:
                on_complete(result)
            except Exception as exc:
                logger.warning(
                    "MaxStandaloneDispatcher.submit_async_callable on_complete raised: %s",
                    exc,
                )
        return result

    def supported(self) -> List[str]:
        """Return supported affinity values."""
        return ["any", "main"]

    def capabilities(self) -> Dict[str, bool]:
        """Return capability flags."""
        return {
            "supports_main_thread": True,
            "supports_named_threads": False,
            "supports_any_thread": True,
            "supports_time_slicing": False,
        }
