"""3ds Max host-pump helpers backed by ``dcc-mcp-core``.

The core ``HostPumpController`` owns pump lifecycle, scheduling, backoff, and
statistics.  This module maps 3ds Max's built-in Qt timer (primary) or .NET
timer (legacy fallback) to the core timer adapter contract and chooses the
interactive versus standalone dispatcher.

Adapter priority:
1. ``QtHostTimerAdapter`` — built-in Qt (PySide2/PySide6), 3ds Max 2021+, zero external deps
2. ``MaxDotNetTimerAdapter`` — pythonnet / System.Windows.Forms.Timer (legacy)
3. No pump — bridge/server still functional, degraded mode
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from dcc_mcp_core import HostPumpController, HostPumpSnapshot, QtHostTimerAdapter

from dcc_mcp_3dsmax.dispatcher.standalone import MaxStandaloneDispatcher
from dcc_mcp_3dsmax.dispatcher.ui import MaxUiDispatcher

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_MS = 8
OVERRUN_MULTIPLIER = 2.0

# Module-level singleton guards against repeated create_dispatcher() calls
# that would leak .NET Timer instances (see MaxDotNetTimerAdapter).
_dispatcher_instance: Optional[Any] = None
_pump_instance: Optional["MaxUiPump"] = None


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


def _build_adapters() -> List[Any]:
    """Build timer adapters in priority order for 3ds Max UI pump.

    Priority: QtHostTimerAdapter (built-in Qt, 3ds Max 2021+) →
    MaxDotNetTimerAdapter (pythonnet/.NET, legacy).
    """
    adapters: List[Any] = []
    # Primary: Qt-based timer — built into all 3ds Max 2021+ via PySide2/PySide6
    try:
        adapters.append(QtHostTimerAdapter())
    except Exception:
        pass
    # Fallback: .NET WinForms timer — requires pythonnet (rarely installed)
    adapters.append(MaxDotNetTimerAdapter())
    return adapters


class MaxUiPump:
    """Compatibility wrapper around ``HostPumpController`` for 3ds Max.

    Tries timer adapters in priority order: Qt (primary) → .NET (legacy).
    Falls back to degraded no-pump mode if no adapter can be installed.
    """

    def __init__(
        self,
        dispatcher: MaxUiDispatcher,
        budget_ms: float = DEFAULT_BUDGET_MS,
        *,
        timer_adapter: Optional[Any] = None,
    ) -> None:
        self._dispatcher = dispatcher
        if timer_adapter is not None:
            self._adapters = [timer_adapter]
        else:
            self._adapters = _build_adapters()
        self._controller = HostPumpController(
            dispatcher,
            self._adapters[0],
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
        """Install the pump, trying adapters in priority order.

        Returns True if a timer adapter was successfully installed,
        False if all adapters failed (degraded no-pump mode).
        """
        for adapter in self._adapters:
            try:
                self._controller.timer_adapter = adapter
                self._controller.start()
                logger.info(
                    "MaxUiPump installed via HostPumpController (budget=%d ms, adapter=%s)",
                    self._controller.budget_ms,
                    type(adapter).__name__,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "MaxUiPump: adapter %s unavailable: %s",
                    type(adapter).__name__,
                    exc,
                )
                continue

        context = _pump_degradation_context()
        logger.warning(
            "MaxUiPump: install skipped (degradable — bridge/server still functional): "
            "no timer adapter available | "
            "max_version=%s qt_available=%s pythonnet=%s clr=%s",
            context.get("max_version", "unknown"),
            context.get("qt_available", False),
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
    """Create the dispatcher/pump pair for the current 3ds Max environment.

    Returns a cached singleton on subsequent calls to guard against
    repeated .NET Timer creation (see MaxDotNetTimerAdapter).
    """
    global _dispatcher_instance, _pump_instance

    if _dispatcher_instance is not None:
        return _dispatcher_instance, _pump_instance

    if _is_standalone_environment():
        _dispatcher_instance = MaxStandaloneDispatcher()
        _pump_instance = None
        return _dispatcher_instance, _pump_instance

    dispatcher = MaxUiDispatcher()
    pump = MaxUiPump(dispatcher, budget_ms=budget_ms)
    _dispatcher_instance = dispatcher
    _pump_instance = pump
    return dispatcher, pump


def reset_dispatcher() -> None:
    """Clear the cached singleton so a fresh dispatcher/pump is created next call."""
    global _dispatcher_instance, _pump_instance
    _dispatcher_instance = None
    _pump_instance = None


def get_dispatcher() -> Tuple[Any, Optional[MaxUiPump]]:
    """Return the cached dispatcher/pump singleton, or ``(None, None)``."""
    return _dispatcher_instance, _pump_instance


def create_pumped_dispatcher(
    budget_ms: float = DEFAULT_BUDGET_MS,
) -> Tuple[Any, Optional[MaxUiPump]]:
    """Backward-compatible alias for the core-backed dispatcher factory."""
    return create_dispatcher(budget_ms=budget_ms)


def _is_standalone_environment() -> bool:
    executable = ""
    try:
        executable = sys.executable.lower()
    except Exception:  # noqa: BLE001
        executable = ""
    if "3dsmaxbatch" in executable or "3dsmaxcmd" in executable:
        return True

    try:
        import pymxs  # noqa: PLC0415

        runtime = getattr(pymxs, "runtime", None)
    except ImportError:
        return True
    if runtime is None:
        return True
    return not _has_interactive_3dsmax_ui(runtime)


def _has_interactive_3dsmax_ui(runtime: Any) -> bool:
    """Return True when pymxs appears to be attached to an interactive Max UI."""
    for path in (
        ("windows", "getMAXHWND"),
        ("windows", "getMAXWindowHandle"),
        ("getMAXHWND",),
        ("getMAXWindowHandle",),
        ("GetMAXWindowHandle",),
        ("maxHWnd",),
        ("maxHwnd",),
    ):
        value = _runtime_value(runtime, path)
        if _truthy_window_handle(value):
            return True

    try:
        import qtmax  # noqa: PLC0415

        for name in ("GetQMaxMainWindow", "getQMaxMainWindow"):
            getter = getattr(qtmax, name, None)
            if callable(getter) and getter() is not None:
                return True
    except Exception:  # noqa: BLE001
        pass

    return False


def _runtime_value(runtime: Any, path: Tuple[str, ...]) -> Any:
    current = runtime
    for name in path:
        current = getattr(current, name, None)
        if current is None:
            return None
    try:
        return current() if callable(current) else current
    except Exception:  # noqa: BLE001
        return None


def _truthy_window_handle(value: Any) -> bool:
    if value is None:
        return False
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return bool(value)


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
        "qt_available": False,
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

    # Qt availability (PySide2/PySide6 — built into 3ds Max 2021+)
    for module_name in ("PySide6.QtCore", "PySide2.QtCore", "PyQt6.QtCore", "PyQt5.QtCore"):
        try:
            __import__(module_name)
            context["qt_available"] = True
            break
        except ImportError:
            continue

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
