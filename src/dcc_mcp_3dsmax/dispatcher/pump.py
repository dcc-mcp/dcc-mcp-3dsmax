"""3ds Max host-pump helpers backed by ``dcc-mcp-core``.

The core ``HostPumpController`` owns pump lifecycle, scheduling, backoff, and
statistics.  This module only maps 3ds Max's .NET timer to the core timer
adapter contract and chooses the interactive versus standalone dispatcher.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from dcc_mcp_core import HostPumpController, HostPumpSnapshot

from dcc_mcp_3dsmax.dispatcher.standalone import MaxStandaloneDispatcher
from dcc_mcp_3dsmax.dispatcher.ui import MaxUiDispatcher

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_MS = 8
OVERRUN_MULTIPLIER = 2.0


class MaxDotNetTimerAdapter:
    """Adapt ``System.Windows.Forms.Timer`` to core's host pump contract."""

    def __init__(self, default_interval_ms: int = 100) -> None:
        self.default_interval_ms = max(int(default_interval_ms), 1)
        self._timer: Any = None
        self._tick: Optional[Callable[[], Optional[float]]] = None
        self._installed = False

    @property
    def installed(self) -> bool:
        return self._installed

    def install(self, tick: Callable[[], Optional[float]]) -> None:
        self._tick = tick
        if self._installed:
            return
        try:
            import clr  # noqa: F401, PLC0415
            from System.Windows.Forms import Timer  # noqa: PLC0415
        except ImportError:
            raise RuntimeError("3ds Max .NET timer is not available")

        self._timer = Timer()
        self._timer.Interval = self.default_interval_ms
        self._timer.Tick += self._on_timer_tick
        self._installed = True

    def uninstall(self) -> None:
        timer = self._timer
        self._timer = None
        self._tick = None
        self._installed = False
        if timer is None:
            return
        try:
            timer.Stop()
            timer.Tick -= self._on_timer_tick
            timer.Dispose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MaxDotNetTimerAdapter: error removing timer: %s", exc)

    def schedule_soon(self) -> None:
        self._start(0.0)

    def _on_timer_tick(self, sender: Any, event: Any) -> None:
        _ = sender, event
        timer = self._timer
        if timer is not None:
            timer.Stop()
        tick = self._tick
        if tick is None or not self._installed:
            return
        interval = tick()
        if interval is not None and self._installed:
            self._start(interval)

    def _start(self, interval_secs: float) -> None:
        timer = self._timer
        if timer is None or not self._installed:
            return
        timer.Stop()
        timer.Interval = max(int(interval_secs * 1000), 1)
        timer.Start()


class MaxUiPump:
    """Compatibility wrapper around ``HostPumpController`` for 3ds Max."""

    def __init__(
        self,
        dispatcher: MaxUiDispatcher,
        budget_ms: float = DEFAULT_BUDGET_MS,
        *,
        timer_adapter: Optional[MaxDotNetTimerAdapter] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._timer_adapter = timer_adapter or MaxDotNetTimerAdapter()
        self._controller = HostPumpController(
            dispatcher,
            self._timer_adapter,
            budget_ms=max(int(budget_ms), 1),
        )
        attach = getattr(dispatcher, "attach_pump_controller", None)
        if callable(attach):
            attach(self._controller)

    @property
    def controller(self) -> HostPumpController:
        return self._controller

    @property
    def is_installed(self) -> bool:
        return bool(self._controller.is_running)

    @property
    def budget_ms(self) -> float:
        return float(self._controller.budget_ms)

    @budget_ms.setter
    def budget_ms(self, value: float) -> None:
        self._controller.budget_ms = max(int(value), 1)

    @property
    def stats(self) -> Dict[str, Any]:
        return _snapshot_to_legacy_stats(self._controller.stats)

    def install(self) -> bool:
        try:
            self._controller.start()
            logger.info(
                "MaxUiPump installed via HostPumpController (budget=%d ms)",
                self._controller.budget_ms,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            context = _pump_degradation_context()
            logger.warning(
                "MaxUiPump: install skipped (degradable — bridge/server still functional): %s | "
                "max_version=%s pythonnet=%s clr=%s",
                exc,
                context.get("max_version", "unknown"),
                context.get("pythonnet_available", False),
                context.get("clr_available", False),
            )
            return False

    def uninstall(self) -> None:
        self._controller.stop()
        detach = getattr(self._dispatcher, "detach_pump_controller", None)
        if callable(detach):
            detach(self._controller)


def create_dispatcher(
    budget_ms: float = DEFAULT_BUDGET_MS,
) -> Tuple[Any, Optional[MaxUiPump]]:
    """Create the dispatcher/pump pair for the current 3ds Max environment."""
    if _is_standalone_environment():
        return MaxStandaloneDispatcher(), None

    dispatcher = MaxUiDispatcher()
    pump = MaxUiPump(dispatcher, budget_ms=budget_ms)
    return dispatcher, pump


def create_pumped_dispatcher(
    budget_ms: float = DEFAULT_BUDGET_MS,
) -> Tuple[Any, Optional[MaxUiPump]]:
    """Backward-compatible alias for the core-backed dispatcher factory."""
    return create_dispatcher(budget_ms=budget_ms)


def _is_standalone_environment() -> bool:
    try:
        import pymxs  # noqa: PLC0415

        getattr(pymxs, "runtime", None)
        return False
    except ImportError:
        return True


def _snapshot_to_legacy_stats(snapshot: HostPumpSnapshot) -> Dict[str, Any]:
    return {
        "total_executed": snapshot.drained_jobs,
        "total_cycles": snapshot.ticks,
        "total_elapsed_ms": snapshot.last_elapsed_ms,
        "overrun_cycles": snapshot.overrun_count,
        "longest_job_ms": snapshot.last_elapsed_ms,
        "queue_size": snapshot.queue_size,
        "active_jobs": snapshot.active_jobs,
        "interval_secs": snapshot.interval_secs,
        "shutdown": snapshot.shutdown,
    }


def _pump_degradation_context() -> Dict[str, Any]:
    """Probe 3ds Max environment for MaxUiPump degradation diagnostics."""
    context: Dict[str, Any] = {
        "max_version": "unknown",
        "pythonnet_available": False,
        "clr_available": False,
    }
    try:
        import pymxs  # noqa: PLC0415
        rt = pymxs.runtime
        try:
            context["max_version"] = str(rt.maxVersion())
        except Exception:  # noqa: BLE001
            pass
    except ImportError:
        pass

    try:
        import pythonnet  # noqa: F401, PLC0415
        context["pythonnet_available"] = True
    except ImportError:
        pass

    try:
        import clr  # noqa: F401, PLC0415
        context["clr_available"] = True
    except ImportError:
        pass

    return context
