"""Render completion signals ("render automations") for 3ds Max.

A render automation arms the host to report when the *next* render finishes:
the adapter registers a post-render callback whose MAXScript body writes a
JSON signal file, then optionally waits for that file and reports the record.

Every step is verified or reported:

* an unsupported automation action is a failure, never an ignored keyword
* a host that refuses the callback is a failure, never an armed-looking success
* a host with no callback read-back contract is reported as ``unverified``
* a wait that expires returns ``status="timeout"`` with the signal still armed

The callback script is plain MAXScript, so no Python bridge is required inside
the host: the host only has to be able to run ``callbacks.addScript``.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dcc_mcp_3dsmax._render_utils import render_error, render_success

# Automation actions the generated signal script understands. Anything else is
# reported as unsupported instead of being silently dropped.
RENDER_AUTOMATION_ACTIONS = ("log", "notify", "save_output")

CALLBACK_HOST_ATTRS = ("callbacks", "Callbacks")
ADD_SCRIPT_METHODS = ("addScript", "add_script")
REMOVE_SCRIPT_METHODS = ("removeScripts", "removeScriptsByID", "remove_script")
SCRIPT_READBACK_METHODS = ("getScript", "getScriptByID", "isRegistered", "contains")
POST_RENDER_SYMBOL = "postRender"

DEFAULT_POLL_INTERVAL = 0.05

_ARMED_SIGNALS: Dict[str, Dict[str, Any]] = {}


def armed_signals() -> Dict[str, Dict[str, Any]]:
    """Return the signals this process armed (read-only view for tests/tools)."""
    return dict(_ARMED_SIGNALS)


def render_automations(
    runtime: Any,
    *,
    actions: Optional[Sequence[str]] = None,
    wait: bool = True,
    timeout_sec: float = 120.0,
    signal_file: Optional[str] = None,
    message: Optional[str] = None,
    output_path: Optional[str] = None,
    label: Optional[str] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    sleep: Any = None,
) -> Dict[str, Any]:
    """Arm a completion signal for the next render, then report its outcome."""
    requested = list(actions) if actions else ["log"]
    unsupported = [action for action in requested if action not in RENDER_AUTOMATION_ACTIONS]
    if unsupported:
        return render_error(
            "Unsupported render automation action(s): {}".format(", ".join(sorted(unsupported))),
            requested=requested,
            supported_actions=list(RENDER_AUTOMATION_ACTIONS),
        )
    if "save_output" in requested and not output_path:
        return render_error("The save_output action needs an output_path", actions=requested)
    try:
        timeout = float(timeout_sec)
    except (TypeError, ValueError):
        return render_error("timeout_sec must be a number", timeout_sec=timeout_sec)
    if timeout < 0:
        return render_error("timeout_sec must be zero or greater", timeout_sec=timeout_sec)

    signal_path, path_error = _resolve_signal_path(signal_file)
    if path_error is not None:
        return render_error(path_error, signal_file=signal_file)

    signal_id = "dcc_mcp_render_signal_{}".format(uuid.uuid4().hex[:12])
    warnings: List[str] = []

    cleared, clear_error = _clear_signal_file(signal_path)
    if clear_error is not None:
        return render_error(clear_error, signal_file=str(signal_path))
    if cleared:
        warnings.append("Removed a stale signal file from a previous render")

    # Re-arming replaces every signal that would write the same file or that
    # was armed under the same label: leaving an older callback registered
    # would fire on the next render and overwrite the file this call waits on.
    stale_keys = [
        key
        for key, record in list(_ARMED_SIGNALS.items())
        if key == (label or signal_id) or str(record.get("signal_file")) == str(signal_path)
    ]
    for key in stale_keys:
        previous = _ARMED_SIGNALS[key]
        removal = _remove_callback(runtime, previous)
        warnings.extend(removal["warnings"])
        if removal["removed"]:
            # Only forget a signal the host actually released: dropping the
            # record first would leave a live callback no later call can find.
            _ARMED_SIGNALS.pop(key, None)
            continue
        # Arming a second callback over one the host kept would leave two
        # live callbacks writing the same signal file, so refuse instead.
        return render_error(
            "The previously armed signal {} is still registered on the host; disarm it before arming a new one".format(
                previous["signal_id"]
            ),
            signal_id=previous["signal_id"],
            signal_file=str(previous["signal_file"]),
            warnings=warnings,
        )

    script = build_signal_script(
        signal_path,
        signal_id=signal_id,
        label=label or "",
        actions=requested,
        message=message or "",
        output_path=output_path or "",
    )
    registration, register_error = _register_callback(runtime, signal_id, script)
    if register_error is not None:
        return render_error(
            register_error,
            signal_id=signal_id,
            signal_file=str(signal_path),
            probed={"hosts": list(CALLBACK_HOST_ATTRS), "methods": list(ADD_SCRIPT_METHODS)},
            warnings=warnings,
        )

    record = {
        "signal_id": signal_id,
        "label": label,
        "signal_file": str(signal_path),
        "actions": requested,
        "output_path": output_path,
        "contract": registration["contract"],
        "script": script,
    }
    _ARMED_SIGNALS[label or signal_id] = record
    warnings.extend(registration["warnings"])

    data: Dict[str, Any] = {
        "signal_id": signal_id,
        "signal_file": str(signal_path),
        "actions": requested,
        "armed": True,
        "registration": registration["status"],
        "contract": registration["contract"],
        "probed": registration["probed"],
        "warnings": warnings,
    }
    if not wait:
        return render_success("Render automation armed; poll the signal file for completion", **data)

    completed, payload, wait_error = _wait_for_signal(
        signal_path, timeout=timeout, poll_interval=poll_interval, sleeper=sleep or time.sleep
    )
    if not completed:
        data["completed"] = False
        data["status"] = "timeout"
        if wait_error is not None:
            cleanup = _disarm(runtime, record, warnings)
            data["armed"] = not cleanup["removed"]
            data["cleanup"] = cleanup
            data["errors"] = [{"setting": "signal_file", "error": wait_error}]
            return render_error(wait_error, **data)
        data["armed"] = True
        warnings.append("No render finished within {}s; the signal is still armed".format(timeout))
        return render_error(
            "No render finished within {}s; the signal is still armed".format(timeout), **data
        )

    cleanup = _disarm(runtime, record, warnings)
    data["completed"] = True
    data["status"] = "completed"
    # Still armed when the host kept the callback: the next render would fire it.
    data["armed"] = not cleanup["removed"]
    data["record"] = payload
    data["cleanup"] = cleanup
    return render_success("Render finished; reported the completion signal", **data)


def disarm_render_automations(runtime: Any, label: Optional[str] = None) -> Dict[str, Any]:
    """Remove every armed render automation (or the one under ``label``)."""
    keys = [label] if label is not None else list(_ARMED_SIGNALS)
    if label is not None and label not in _ARMED_SIGNALS:
        return render_error("No armed render automation matches {}".format(label), label=label, armed=list(_ARMED_SIGNALS))
    removed: List[str] = []
    warnings: List[str] = []
    failed: List[Dict[str, Any]] = []
    for key in keys:
        record = _ARMED_SIGNALS.get(key)
        if record is None:
            continue
        result = _remove_callback(runtime, record)
        warnings.extend(result["warnings"])
        if result["removed"]:
            _ARMED_SIGNALS.pop(key, None)
            cleared, clear_error = _clear_signal_file(Path(record["signal_file"]))
            if clear_error is not None:
                warnings.append(clear_error)
            removed.append(record["signal_id"])
        else:
            failed.append({"signal_id": record["signal_id"], "error": result["error"] or "not removed"})
    data = {"removed": removed, "failed": failed, "warnings": warnings, "still_armed": sorted(_ARMED_SIGNALS)}
    if failed:
        return render_error("Could not remove every armed render automation", **data)
    return render_success("Removed {} render automation(s)".format(len(removed)), **data)


def build_signal_script(
    signal_path: Path,
    *,
    signal_id: str,
    label: str,
    actions: Sequence[str],
    message: str,
    output_path: str,
) -> str:
    """Build the MAXScript body that writes the completion signal file.

    Every quote that reaches the inside of the MAXScript ``format`` string is
    escaped. A bare ``"`` would terminate the string literal and turn the rest
    of the script into a syntax error, so the callback would either be
    rejected at registration or would fail when it fires -- in both cases no
    completion record is ever written.
    """
    actions_literal = ", ".join('\\"{}\\"'.format(_maxscript_escape(action)) for action in actions)
    lines = [
        "(",
        "local dccMcpSignalPath = @\"{}\"".format(str(signal_path).replace('"', "")),
        "local dccMcpSignal = createFile dccMcpSignalPath",
        "if dccMcpSignal != undefined then (",
        "format \"{{\\\"completed\\\": true, \\\"signal_id\\\": \\\"{}\\\", \\\"label\\\": \\\"{}\\\", ".format(
            _maxscript_escape(signal_id), _maxscript_escape(label)
        )
        + "\\\"time\\\": \\\"%\\\", \\\"actions\\\": [{}], ".format(actions_literal)
        + "\\\"output_path\\\": \\\"{}\\\", \\\"message\\\": \\\"{}\\\"}}\\n\" ".format(
            _maxscript_escape(output_path), _maxscript_escape(message)
        )
        + "(localTime) to:dccMcpSignal",
        "close dccMcpSignal",
        ") else (",
        "format \"dcc-mcp: could not open the render signal file %\\n\" dccMcpSignalPath",
        ")",
        ")",
    ]
    return "\n".join(lines)


def _maxscript_escape(value: str) -> str:
    """Escape text that is embedded in a MAXScript ``format`` string literal.

    ``%`` is doubled as well: inside a ``format`` string it introduces a
    placeholder, so an unescaped percent in caller-supplied text would consume
    the ``localTime`` argument and corrupt the completion record.
    """
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return text.replace("%", "%%").replace("\n", " ")


def _resolve_signal_path(signal_file: Optional[str]) -> Tuple[Optional[Path], Optional[str]]:
    if signal_file is None or not str(signal_file).strip():
        return Path(_temp_dir()) / "dcc_mcp_3dsmax_render_signal.json", None
    path = Path(str(signal_file)).expanduser()
    if path.exists() and not path.is_file():
        return None, "The signal path exists and is not a file"
    if not path.parent.exists():
        return None, "The signal file directory does not exist"
    return path, None


def _temp_dir() -> str:
    import tempfile  # noqa: PLC0415 - resolved per call so tests can redirect it.

    return tempfile.gettempdir()


def _clear_signal_file(path: Path) -> Tuple[bool, Optional[str]]:
    try:
        if not path.exists():
            return False, None
        path.unlink()
    except OSError as exc:
        return False, "Could not remove the stale signal file {}: {}".format(path, exc)
    if path.exists():
        return False, "The stale signal file {} is still present after removing it".format(path)
    return True, None


def _register_callback(runtime: Any, signal_id: str, script: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """Register the post-render callback and verify the host reports it back."""
    callbacks = _callback_host(runtime)
    if callbacks is None:
        return {}, "No callback system is available on this host"
    adder = _first_method(callbacks, ADD_SCRIPT_METHODS)
    if adder is None:
        return {}, "The host exposes no callback registration method"
    symbol = _callback_symbol(runtime, POST_RENDER_SYMBOL)
    probed = ["{}({!r}, script)".format(adder[0], symbol)]
    error: Optional[str] = None
    for args, kwargs in (
        ((symbol, script), {"id": signal_id}),
        ((symbol,), {"script": script, "id": signal_id}),
        ((symbol, script), {}),
        ((symbol, script, signal_id), {}),
    ):
        try:
            handle = adder[1](*args, **kwargs)
            break
        except TypeError:
            error = None
            continue
        except Exception as exc:  # noqa: BLE001 - a host rejection is reported.
            error = "Callback registration failed: {}".format(exc)
            break
    else:
        return (
            {"status": "rejected", "contract": None, "probed": probed, "warnings": []},
            error or "The host callback registration accepted none of the probed signatures",
        )
    if error is not None:
        return {"status": "rejected", "contract": None, "probed": probed, "warnings": []}, error

    state, contract = _callback_registered(callbacks, signal_id, handle)
    registration: Dict[str, Any] = {
        "status": "registered",
        "contract": adder[0],
        "probed": probed,
        "warnings": [],
        "handle": _jsonable(handle),
    }
    if state is None:
        registration["status"] = "unverified"
        registration["warnings"].append(
            "The host exposes no callback read-back contract, so the registration is unverified"
        )
        return registration, None
    probed.append(contract or "readback")
    if state is False:
        registration["status"] = "rejected"
        return registration, "The host does not report the callback back after registering it"
    registration["status"] = "registered"
    return registration, None


def _remove_callback(runtime: Any, record: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove an armed callback and report whether the host confirmed it."""
    result: Dict[str, Any] = {"removed": False, "verified": False, "warnings": [], "error": None}
    callbacks = _callback_host(runtime)
    signal_id = str(record.get("signal_id", ""))
    if callbacks is None:
        result["error"] = "No callback system is available on this host"
        result["warnings"].append(result["error"])
        return result
    remover = _first_method(callbacks, REMOVE_SCRIPT_METHODS)
    if remover is None:
        result["error"] = "The host exposes no callback removal method"
        result["warnings"].append(result["error"])
        return result
    for args, kwargs in (((signal_id,), {}), ((), {"id": signal_id})):
        try:
            remover[1](*args, **kwargs)
            break
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001 - a host rejection is reported.
            result["error"] = "Callback removal failed: {}".format(exc)
            result["warnings"].append(result["error"])
            return result
    state, _contract = _callback_registered(callbacks, signal_id, None)
    if state is None:
        result["removed"] = True
        result["warnings"].append(
            "The host exposes no callback read-back contract, so the removal is unverified"
        )
        return result
    if state is False:
        result["removed"] = True
        result["verified"] = True
        return result
    result["error"] = "The host still reports the callback after removing it"
    result["warnings"].append(result["error"])
    return result


def _disarm(runtime: Any, record: Mapping[str, Any], warnings: List[str]) -> Dict[str, Any]:
    """Remove one armed signal, forgetting it only once the host released it."""
    result = _remove_callback(runtime, record)
    warnings.extend(result["warnings"])
    if result["removed"]:
        _ARMED_SIGNALS.pop(str(record.get("label") or record.get("signal_id")), None)
    return result


def _callback_registered(
    callbacks: Any, signal_id: str, handle: Any
) -> Tuple[Optional[bool], Optional[str]]:
    """Return ``(True/False/None, contract)`` for a callback registration."""
    for name in SCRIPT_READBACK_METHODS:
        func = _safe_getattr(callbacks, name)
        if not callable(func):
            continue
        try:
            value = func(signal_id)
        except TypeError:
            try:
                value = func(id=signal_id)
            except Exception:  # noqa: BLE001 - unreadable means unknown.
                continue
        except Exception:  # noqa: BLE001 - unreadable means unknown.
            continue
        return _truthy(value, handle), name
    scripts = _safe_getattr(callbacks, "scripts")
    if isinstance(scripts, Mapping):
        return (signal_id in scripts or any(str(key).endswith(signal_id) for key in scripts)), "scripts"
    return None, None


def _truthy(value: Any, handle: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value)
    return bool(text.strip())


def _callback_host(runtime: Any) -> Any:
    for attribute in CALLBACK_HOST_ATTRS:
        host = _safe_getattr(runtime, attribute)
        if host is not None:
            return host
    return None


def _callback_symbol(runtime: Any, symbol: str) -> Any:
    """Wrap a callback symbol the way the host expects (``rt.name(...)``)."""
    for name in ("name", "Name"):
        factory = _safe_getattr(runtime, name)
        if callable(factory):
            try:
                return factory(symbol)
            except Exception:  # noqa: BLE001 - fall back to the plain string.
                continue
    return symbol


def _wait_for_signal(
    path: Path, *, timeout: float, poll_interval: float, sleeper: Any
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Poll the signal file until the render completes or the wait expires."""
    deadline = time.monotonic() + timeout
    interval = max(0.0, float(poll_interval))
    raw = ""
    while True:
        if path.is_file():
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return False, None, "Could not read the signal file {}: {}".format(path, exc)
            if raw.strip():
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = None
                else:
                    if not isinstance(payload, dict):
                        return False, None, "The signal file does not hold a JSON object: {}".format(path)
                    if payload.get("completed") is not True:
                        return False, None, "The signal file reported no completion: {}".format(path)
                    return True, payload, None
        if time.monotonic() >= deadline:
            break
        sleeper(interval)
    if raw.strip():
        return False, None, "The signal file does not hold a valid completion record: {}".format(raw[:200])
    return False, None, None


def _first_method(owner: Any, names: Sequence[str]) -> Optional[Tuple[str, Any]]:
    for name in names:
        func = _safe_getattr(owner, name)
        if callable(func):
            return name, func
    return None


def _safe_getattr(owner: Any, name: str) -> Any:
    try:
        return getattr(owner, name)
    except Exception:  # noqa: BLE001 - a missing contract is not an error.
        return None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
