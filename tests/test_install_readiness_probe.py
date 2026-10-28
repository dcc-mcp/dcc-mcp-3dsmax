"""Readiness probes must resolve to real, read-only host tools."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from dcc_mcp_3dsmax import install_cli as cli
from dcc_mcp_3dsmax.sidecar import _dispatcher


def _configured_probe(monkeypatch):
    from dcc_mcp_core import deployment

    captured = {}

    def wait(**kwargs):
        captured.update(kwargs)
        return {"success": True, "status": "ready"}

    monkeypatch.setattr(deployment, "wait_for_sidecar_ready", wait)
    assert cli._wait_readiness(0.25)["success"] is True
    assert captured["dcc_type"] == "3dsmax"
    assert captured["timeout_secs"] == 0.25
    return captured["probe_tool"]


def test_configured_probe_is_bundled_read_only_main_thread_tool(monkeypatch):
    probe = _configured_probe(monkeypatch)
    skill, name = probe.split("__")
    root = Path(cli.__file__).parent / "skills" / skill
    tool = next(tool for tool in yaml.safe_load((root / "tools.yaml").read_text())["tools"] if tool["name"] == name)
    assert tool["read_only"] is True
    assert tool["destructive"] is False
    assert tool["affinity"] == "main"
    assert tool["enforce_thread_affinity"] is True
    assert _dispatcher._resolve_action_source(probe, server=None) == str(root / tool["source_file"])


def test_probe_dispatches_before_skills_are_loaded(monkeypatch):
    probe = _configured_probe(monkeypatch)
    runtime = SimpleNamespace(currentTime=7, animationRange=SimpleNamespace(start=0, end=100), frameRate=24)
    monkeypatch.setitem(sys.modules, "pymxs", SimpleNamespace(runtime=runtime))
    server = SimpleNamespace(list_actions=lambda: [])
    result = _dispatcher.dispatch_payload_dict({"action": probe, "args": {}}, server_lookup=lambda: server)
    assert result["success"] is True, result
    assert result["data"]["settings"]["current_time"] == 7
    assert runtime.currentTime == 7


def test_nonexistent_probe_is_not_success(monkeypatch):
    _configured_probe(monkeypatch)
    result = _dispatcher.dispatch_payload_dict(
        {"action": "3dsmax_diagnostics__ping", "args": {}},
        server_lookup=lambda: SimpleNamespace(list_actions=lambda: []),
    )
    assert result["success"] is False
    assert result["error"] == "unknown-action"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"success": True, "status": "ready"}, (True, "ready")),
        ({"success": False, "status": "timeout", "last_status": "probe_failed"}, (False, "probe_failed")),
        ({"success": False, "status": "timeout", "last_status": "missing"}, (False, "missing")),
        ({"success": False, "status": "timeout", "last_status": "booting"}, (False, "timeout")),
        ({"success": False, "status": "timeout", "last_status": "private/path"}, (False, "timeout")),
    ],
)
def test_readiness_distinguishes_probe_failure_missing_host_and_startup_timeout(monkeypatch, result, expected):
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: result)
    assert cli._readiness_verdict(0) == expected


@pytest.mark.parametrize("host_present", [True, False])
def test_core_polling_result_preserves_probe_failure_or_absent_host(monkeypatch, host_present):
    from dcc_mcp_core import _install_lifecycle_readiness as readiness

    entries = [{"dispatch_ready": True, "instance_id": "test-max"}] if host_present else []
    monkeypatch.setattr(readiness, "_query_runtime_state", lambda *args, **kwargs: {"entries": entries})
    calls = []

    def failed_probe(entry, tool, arguments, **kwargs):
        calls.append(tool)
        return {"success": False, "status": "probe_failed", "result": {"error": "unknown-action"}}

    monkeypatch.setattr(readiness, "probe_sidecar_tool", failed_probe)
    assert cli._readiness_verdict(0) == (False, "probe_failed" if host_present else "missing")
    assert len(calls) == int(host_present)
