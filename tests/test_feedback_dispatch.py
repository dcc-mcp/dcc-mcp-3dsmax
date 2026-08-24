"""Feedback dispatch contract owned jointly by the adapter and shared Core."""

from pathlib import Path

from packaging.requirements import Requirement

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_FORWARDER_VERSION = "0.20.11"


def _project_dependencies():
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {Requirement(raw).name: Requirement(raw) for raw in payload["project"]["dependencies"]}


def test_runtime_dependency_floor_includes_core_feedback_forwarder():
    """Normal installs must select the Core-owned gateway feedback sidecar."""
    dependencies = _project_dependencies()

    for package_name in ("dcc-mcp-core", "dcc-mcp-server"):
        requirement = dependencies[package_name]
        assert requirement.specifier.contains(FEEDBACK_FORWARDER_VERSION)
        assert not requirement.specifier.contains("0.20.10")
        assert not requirement.specifier.contains("0.20.9")


def test_documented_install_surfaces_use_feedback_forwarder_floor():
    """Operator and agent install paths must not resolve a legacy server."""
    expected = "dcc-mcp-core>=0.20.11"
    expected_server = "dcc-mcp-server>=0.20.11"

    for relative_path in ("README.md", "llms-full.txt", "justfile"):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert expected in text.replace(" ", "")
        assert expected_server in text.replace(" ", "")


def test_adapter_registration_does_not_override_core_feedback_forwarder():
    """The shared Core registration remains the only feedback tool owner."""
    from dcc_mcp_3dsmax import _registration
    from dcc_mcp_3dsmax.server import MaxMcpServer

    phase_names = [phase.name for phase in _registration.default_registration_phases()]

    assert "feedback_tool" not in phase_names
    assert "_register_feedback_tool" not in MaxMcpServer.__dict__


def test_core_registration_keeps_feedback_discoverable_without_adapter_override():
    """Search and describe metadata must come from the same Core-owned tool."""
    from dcc_mcp_3dsmax.dispatcher import MaxStandaloneDispatcher
    from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

    server = MaxMcpServer(
        options=MaxServerOptions(
            port=0,
            dispatcher=MaxStandaloneDispatcher(),
            enable_gateway_failover=False,
            job_storage_path="",
        )
    )
    try:
        server.register_builtin_actions(include_bundled=False)
        metadata = server._server.registry.get_action("dcc_feedback__report")
    finally:
        server.stop()

    assert metadata is not None
    assert metadata["name"] == "dcc_feedback__report"
    assert metadata["dcc"] == "3dsmax"
    assert metadata["execution"] == "sync"
    assert metadata["source_file"] is None
    assert metadata["input_schema"]["additionalProperties"] is False
    assert "shared Core handler forwards" in metadata["description"]


def test_feedback_reaching_host_dispatch_fails_closed_with_request_id():
    """A legacy server must never turn an unforwarded report into success."""
    from dcc_mcp_3dsmax.sidecar._dispatcher import dispatch_payload_dict

    response = dispatch_payload_dict(
        {
            "action": "dcc_feedback__report",
            "args": {"summary": "private details must not be reflected"},
            "request_id": "feedback-request-149",
        },
        server_lookup=lambda: None,
    )

    assert response == {
        "success": False,
        "error": "gateway-feedback-forwarder-required",
        "message": "Feedback must be forwarded by dcc-mcp-server before host dispatch",
        "request_id": "feedback-request-149",
        "action": "dcc_feedback__report",
    }


def test_feedback_host_fallback_keeps_core_payload_validation():
    """The diagnostic path must not bypass Core's typed wire boundary."""
    from dcc_mcp_3dsmax.sidecar._dispatcher import dispatch_payload_dict

    invalid_args = dispatch_payload_dict(
        {
            "action": "dcc_feedback__report",
            "args": "not-an-object",
            "request_id": "feedback-invalid-args",
        },
        server_lookup=lambda: None,
    )
    invalid_request_id = dispatch_payload_dict(
        {
            "action": "dcc_feedback__report",
            "args": {},
            "request_id": ["not", "a", "string"],
        },
        server_lookup=lambda: None,
    )

    assert invalid_args["error"] == "payload-malformed"
    assert invalid_args["context"]["reason"] == "invalid-args"
    assert invalid_request_id["error"] == "payload-malformed"
    assert invalid_request_id["context"]["reason"] == "invalid-request-id"


def test_feedback_host_fallback_cannot_be_shadowed_by_adapter_action(monkeypatch):
    """No host skill may replace the Core-owned feedback action."""
    from dcc_mcp_3dsmax.sidecar import _dispatcher

    class ShadowingServer:
        @staticmethod
        def list_actions():
            return [
                {
                    "action_name": "dcc_feedback__report",
                    "source_file": __file__,
                }
            ]

    def reject_execution(*args, **kwargs):
        raise AssertionError("feedback must never execute a host script")

    monkeypatch.setattr(_dispatcher._executor, "run_skill_script", reject_execution)
    response = _dispatcher.dispatch_payload_dict(
        {
            "action": "dcc_feedback__report",
            "args": {},
            "request_id": "feedback-shadow-attempt",
        },
        server_lookup=ShadowingServer,
    )

    assert response["success"] is False
    assert response["error"] == "gateway-feedback-forwarder-required"
    assert response["request_id"] == "feedback-shadow-attempt"


def test_unrelated_unknown_action_remains_rejected():
    """The feedback diagnostic must not broaden the host dispatch surface."""
    from dcc_mcp_3dsmax.sidecar._dispatcher import dispatch_payload_dict

    response = dispatch_payload_dict(
        {
            "action": "unregistered_action",
            "args": {},
            "request_id": "unknown-request-149",
        },
        server_lookup=lambda: None,
    )

    assert response["success"] is False
    assert response["error"] == "unknown-action"
    assert response["request_id"] == "unknown-request-149"
    assert response["action"] == "unregistered_action"
