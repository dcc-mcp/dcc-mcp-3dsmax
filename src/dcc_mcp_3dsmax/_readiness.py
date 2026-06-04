"""Runtime readiness wiring for :class:`MaxMcpServer`.

Ports the Houdini readiness template so ``GET /v1/readyz`` stops lying during
3ds Max's boot window.  The six-state probe itself
(:class:`dcc_mcp_core.ReadinessProbe`) lives in ``dcc-mcp-core``; this module
owns only the 3ds Max-side wiring:

* ``process``                — always true while the Python interpreter is alive.
* ``dispatcher``             — flipped the moment the probe is published to the server.
* ``dcc``                    — flipped after the 3ds Max UI dispatcher pumps one deferred
  no-op (or immediately in inline / standalone / sidecar mode where the HTTP
  worker thread *is* the executor).
* ``skill_catalog``          — (core 0.17.56+) defaults true; the empty catalog counts as ready.
* ``host_execution_bridge``  — (core 0.17.56+) flipped when ``HostExecutionBridge`` is registered.
* ``main_thread_executor``   — (core 0.17.56+) flipped when the core HTTP main-thread route is wired.

Operator opt-in for an advisory timeout:
``DCC_MCP_3DSMAX_READINESS_TIMEOUT_SECS``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from dcc_mcp_core import ReadinessProbe

logger = logging.getLogger(__name__)

ENV_READINESS_TIMEOUT_SECS = "DCC_MCP_3DSMAX_READINESS_TIMEOUT_SECS"
READINESS_PROBE_REQUEST_ID = "dcc_mcp_3dsmax__readiness__dcc_ready_probe"

ProbeScheduler = Callable[[Any, Callable[[], None]], bool]


def resolve_readiness_timeout_secs(
    readiness_timeout_secs: Optional[int] = None,
) -> Optional[int]:
    """Resolve :data:`ENV_READINESS_TIMEOUT_SECS` into a positive integer."""
    if readiness_timeout_secs is not None:
        try:
            val = int(readiness_timeout_secs)
        except (TypeError, ValueError):
            return None
        return val if val > 0 else None

    raw = os.environ.get(ENV_READINESS_TIMEOUT_SECS)
    if not raw or not raw.strip():
        return None
    try:
        val = int(raw.strip())
    except ValueError:
        logger.warning(
            "Ignoring invalid %s=%r (expected positive integer seconds)",
            ENV_READINESS_TIMEOUT_SECS,
            raw,
        )
        return None
    return val if val > 0 else None


def _default_probe_scheduler(dispatcher: Any, on_done: Callable[[], None]) -> bool:
    """Schedule a dcc-ready probe on *dispatcher*.

    The 3ds Max UI dispatcher exposes ``submit_async_callable``; a main-thread
    no-op is queued and ``on_done`` runs from the completion callback once the
    UI pump drains it.  Dispatchers without the async API (or no dispatcher at
    all) collapse to inline readiness — ``on_done`` runs immediately.
    """
    submit_async = getattr(dispatcher, "submit_async_callable", None)
    if submit_async is None:
        on_done()
        return True

    def _on_complete(_result: Any) -> None:
        on_done()

    submit_async(
        request_id=READINESS_PROBE_REQUEST_ID,
        task=lambda: None,
        affinity="main",
        timeout_ms=5_000,
        on_complete=_on_complete,
    )
    return True


class ReadinessBinder:
    """Drive a :class:`dcc_mcp_core.ReadinessProbe` across a 3ds Max lifecycle."""

    def __init__(
        self,
        *,
        timeout_secs: Optional[int] = None,
        probe_scheduler: Optional[ProbeScheduler] = None,
    ) -> None:
        self.timeout_secs: Optional[int] = resolve_readiness_timeout_secs(timeout_secs)
        self.probe: ReadinessProbe = ReadinessProbe()
        self.probe_scheduler: ProbeScheduler = probe_scheduler or _default_probe_scheduler
        self.bound_server: Any = None
        self.bound_dispatcher: Any = None
        self.dcc_scheduled: bool = False
        self.published_to_server: bool = False

    def report(self) -> dict:
        """Return the current multi-state readiness snapshot (core 0.17.56+)."""
        return self.probe.report()

    def is_ready(self) -> bool:
        """Return ``True`` when all bits are green."""
        return self.probe.is_ready()

    def bind(self, server: Any) -> bool:
        """Wire the probe into *server*."""
        if self.bound_server is server:
            return self.dcc_scheduled
        self.bound_server = server

        server._server.set_readiness_probe(self.probe)
        self.published_to_server = True
        self.mark_dispatcher_ready()

        dispatcher = getattr(server, "_max_dispatcher", None)
        if dispatcher is None:
            self.bound_dispatcher = None
            self.mark_dcc_ready()
            self.mark_main_thread_executor_ready()
            self.dcc_scheduled = True
            return True

        self.bound_dispatcher = dispatcher
        # Core 0.17.56+: first pump completion proves both dcc liveness
        # and main-thread executor availability.  Gateway host execution
        # summary requires dispatcher && dcc && host_execution_bridge &&
        # main_thread_executor; without this dual-flip the instance
        # diagnoses as host execution not_ready.
        def _on_first_pump() -> None:
            self.mark_dcc_ready()
            self.mark_main_thread_executor_ready()

        self.dcc_scheduled = bool(self.probe_scheduler(dispatcher, _on_first_pump))
        return self.dcc_scheduled

    def mark_dispatcher_ready(self, value: bool = True) -> None:
        """Flip the ``dispatcher`` bit."""
        self.probe.set_dispatcher_ready(value)

    def mark_dcc_ready(self, value: bool = True) -> None:
        """Flip the ``dcc`` bit."""
        self.probe.set_dcc_ready(value)
        if value:
            logger.info("[3dsmax] readiness: dcc-ready — main thread is pumping")

    # ── Core 0.17.56+ readiness states ───────────────────────────────────

    def mark_host_execution_bridge_ready(self, value: bool = True) -> None:
        """Flip the ``host_execution_bridge`` bit (core 0.17.56+)."""
        self.probe.set_host_execution_bridge_ready(value)

    def mark_main_thread_executor_ready(self, value: bool = True) -> None:
        """Flip the ``main_thread_executor`` bit (core 0.17.56+)."""
        self.probe.set_main_thread_executor_ready(value)

    def mark_skill_catalog_ready(self, value: bool = True) -> None:
        """Flip the ``skill_catalog`` bit (core 0.17.56+).

        The catalog defaults to ``True`` (empty catalog is valid); call
        ``mark_skill_catalog_ready(False)`` during re-scans or reloads.
        """
        self.probe.set_skill_catalog_ready(value)


def install_readiness(
    server: Any,
    *,
    timeout_secs: Optional[int] = None,
    probe_scheduler: Optional[ProbeScheduler] = None,
) -> ReadinessBinder:
    """One-shot helper used by :class:`MaxMcpServer.__init__`."""
    binder = ReadinessBinder(timeout_secs=timeout_secs, probe_scheduler=probe_scheduler)
    binder.bind(server)
    return binder


def wait_until_ready(server: Any, timeout: int = 30) -> bool:
    """Block until ``/v1/readyz`` returns 200 (or ``/health`` as fallback)."""
    import time
    import urllib.error
    import urllib.request

    port = getattr(server, "port", None)
    if port is None and hasattr(server, "_options"):
        port = getattr(server._options, "port", 8765)
    port = int(port or 8765)

    urls = (
        f"http://127.0.0.1:{port}/v1/readyz",
        f"http://127.0.0.1:{port}/health",
    )
    deadline = time.time() + timeout

    while time.time() < deadline:
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        logger.info("3ds Max MCP server ready on port %s (%s)", port, url)
                        return True
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
        time.sleep(0.5)

    logger.warning("3ds Max MCP server not ready after %ss", timeout)
    return False
