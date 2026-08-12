"""3ds Max sidecar action dispatch using the core sidecar dispatcher."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from dcc_mcp_core import SidecarActionDispatcher

from dcc_mcp_3dsmax import _executor

_BUILTIN_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

logger = logging.getLogger(__name__)


class _NoServer:
    """Placeholder used when explicit script dispatch does not need a server."""


def dispatch(payload: Union[Mapping[str, Any], str]) -> str:
    """Dispatch a sidecar payload against the currently running server."""
    return dispatch_payload(payload)


def dispatch_payload(
    payload: Union[Mapping[str, Any], str],
    *,
    server_lookup: Optional[Callable[[], Any]] = None,
) -> str:
    """Dispatch *payload* and return a single-line JSON response."""
    return _json_response(dispatch_payload_dict(payload, server_lookup=server_lookup))


def dispatch_payload_dict(
    payload: Union[Mapping[str, Any], str],
    *,
    server_lookup: Optional[Callable[[], Any]] = None,
) -> dict:
    """Dispatch *payload* and return the normalized response dict."""
    parsed = _normalise_payload(payload)
    if parsed is None:
        return {
            "success": False,
            "error": "payload-malformed",
            "message": "payload must be a JSON object",
            "request_id": "",
        }

    lookup = server_lookup or _default_server_lookup
    dispatcher = SidecarActionDispatcher(
        "3dsmax",
        server_provider=lambda: _server_or_placeholder(lookup),
        action_resolver=_resolve_action_source,
        executor=_run_skill_script,
        bundled_skill_roots=[_BUILTIN_SKILLS_DIR],
    )
    body = dispatcher.dispatch_payload(parsed)
    body.setdefault("request_id", str(parsed.get("request_id") or ""))
    action = parsed.get("action")
    if isinstance(action, str) and action:
        body.setdefault("action", action)
    return body


def _default_server_lookup() -> Any:
    from dcc_mcp_3dsmax import get_server  # noqa: PLC0415

    return get_server()


def _normalise_payload(payload: Union[Mapping[str, Any], str]) -> Optional[dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, Mapping):
        return None

    parsed = dict(payload)
    if not isinstance(parsed.get("action"), str):
        source = parsed.get("script_path") or parsed.get("source_file")
        if isinstance(source, str) and source.strip():
            parsed["action"] = Path(source).stem
    return parsed


def _server_or_placeholder(server_lookup: Callable[[], Any]) -> Any:
    try:
        return server_lookup() or _NoServer()
    except Exception as exc:  # noqa: BLE001
        logger.debug("sidecar server lookup failed: %s", exc)
        return _NoServer()


def _load_skill(server: Any, args: Mapping[str, Any]) -> dict:
    """Load one catalog skill through the same host-thread sidecar route."""
    skill_name = args.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return _lifecycle_error("payload-malformed", "load_skill requires a non-empty skill_name")
    skill_name = skill_name.strip()

    if isinstance(server, _NoServer):
        return _lifecycle_error("server-not-running", "3ds Max server is not running")

    loader = getattr(server, "load_skill", None)
    if not callable(loader):
        return _lifecycle_error("unknown-action", "3ds Max server does not support load_skill")

    try:
        accepted = bool(loader(skill_name))
    except Exception as exc:  # noqa: BLE001
        logger.exception("sidecar load_skill(%r) failed", skill_name)
        return _lifecycle_error(
            "dispatch-failed",
            "Failed to load skill: {}".format(skill_name),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    loaded = _is_skill_loaded(server, skill_name, fallback=accepted)
    if not accepted or not loaded:
        return _lifecycle_error("skill-load-failed", "Failed to load skill: {}".format(skill_name))

    return {
        "success": True,
        "loaded": True,
        "skill_name": skill_name,
        "dcc_type": "3dsmax",
        "registered_tools": _registered_tool_names(server, skill_name),
        "message": "Loaded skill: {}".format(skill_name),
    }


def _lifecycle_error(code: str, message: str, **context: Any) -> dict:
    result = {"success": False, "error": code, "message": message}
    if context:
        result["context"] = context
    return result


def _is_skill_loaded(server: Any, skill_name: str, *, fallback: bool) -> bool:
    checker = getattr(server, "is_skill_loaded", None)
    if not callable(checker):
        return fallback
    try:
        return bool(checker(skill_name))
    except Exception as exc:  # noqa: BLE001
        logger.debug("is_skill_loaded failed for %s: %s", skill_name, exc)
        return fallback


def _registered_tool_names(server: Any, skill_name: str) -> list:
    names = []
    for action in _list_actions(server):
        owner = _get_value(action, "skill_name") or _get_value(action, "skill")
        if owner != skill_name:
            continue
        name = _get_value(action, "name") or _get_value(action, "action")
        if isinstance(name, str) and name:
            names.append(name)
    return sorted(set(names))


def _list_actions(server: Any) -> list:
    lister = getattr(server, "list_actions", None)
    if not callable(lister):
        return []
    try:
        return list(lister() or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_actions failed while reporting load_skill result: %s", exc)
        return []


def _resolve_action_source(action_name: str, *, server: Any = None, payload: Any = None) -> Any:
    _ = payload
    if action_name == "load_skill":
        return {
            "source_file": str(Path(__file__).resolve()),
            "skill_name": "dcc-adapter",
            "thread_affinity": "main",
            "execution": "sync",
        }
    path = _resolve_script_path(server, action_name)
    if path is not None:
        return str(path)
    path = _resolve_bundled_script_path(action_name)
    return str(path) if path is not None else None


def _run_skill_script(request: Any) -> Any:
    if request.action == "load_skill":
        return _load_skill(request.server, request.args)
    result = _executor.run_skill_script(request.script_path, request.args)
    if isinstance(result, Mapping):
        result = dict(result)
        if "success" not in result:
            status = result.get("status")
            if status == "error":
                result["success"] = False
            elif status == "success":
                result["success"] = True
    return result


def _resolve_script_path(server: Any, action_name: str) -> Optional[Path]:
    if server is None or isinstance(server, _NoServer):
        return None

    try:
        actions = server.list_actions()
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_actions failed while resolving %s: %s", action_name, exc)
        return None

    for action in actions:
        names = {
            _get_value(action, "name"),
            _get_value(action, "action"),
            _get_value(action, "action_name"),
            _get_value(action, "tool_name"),
        }
        if action_name not in names:
            continue
        source = _get_value(action, "source_file") or _get_value(action, "script_path") or _get_value(action, "path")
        if isinstance(source, str):
            path = Path(source)
            if path.is_file():
                return path
    return None


def _resolve_bundled_script_path(action_name: str) -> Optional[Path]:
    normalised = action_name.replace(".", "__")
    skill_name = ""
    tool_name = normalised
    if "__" in normalised:
        skill_name, tool_name = normalised.split("__", 1)

    candidates = []
    if skill_name:
        candidates.append(_BUILTIN_SKILLS_DIR / skill_name / "action_{}.py".format(tool_name))
    candidates.extend(_BUILTIN_SKILLS_DIR.glob("*/action_{}.py".format(tool_name)))
    candidates.extend(_BUILTIN_SKILLS_DIR.glob("*/{}.py".format(tool_name)))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _get_value(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _json_response(body: Mapping[str, Any]) -> str:
    return json.dumps(dict(body), ensure_ascii=False, separators=(",", ":"))
