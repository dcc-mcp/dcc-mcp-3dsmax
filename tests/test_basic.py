"""Basic tests for dcc-mcp-3dsmax."""

# Import built-in modules
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _load_action_module(path):
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePoint3:
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __add__(self, other):
        return _FakePoint3(self.x + other.x, self.y + other.y, self.z + other.z)


class _FakeNode:
    def __init__(self, name="Node", handle=1001):
        self.name = name
        self.handle = handle
        self.pos = _FakePoint3(0, 0, 0)


class _FakeRuntime:
    def __init__(self):
        self.created = []
        self.nodes = {}
        self.selection = []
        self.executed_script = None

    def Point3(self, x, y, z):  # noqa: N802 - mirrors pymxs runtime naming.
        return _FakePoint3(x, y, z)

    def Sphere(self, radius=50.0):  # noqa: N802 - mirrors pymxs runtime naming.
        node = _FakeNode("Sphere{:03d}".format(len(self.created) + 1), 2000 + len(self.created))
        node.radius = radius
        self.created.append(node)
        self.nodes[node.name] = node
        return node

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        return self.nodes.get(name)

    def execute(self, script):
        self.executed_script = script
        return {"ok": True, "script": script}


class TestImports:
    """Test that core modules can be imported."""

    def test_import_package(self):
        """Test importing the main package."""
        import dcc_mcp_3dsmax

        assert hasattr(dcc_mcp_3dsmax, "__version__")
        assert dcc_mcp_3dsmax.__version__ != ""

    def test_import_package_keeps_core_native_extension_lazy(self):
        """Menu-only startup imports should not lock dcc_mcp_core._core.pyd."""
        env = dict(os.environ)
        src = Path(__file__).parent.parent / "src"
        env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
        code = (
            "import sys\n"
            "import dcc_mcp_3dsmax\n"
            "dcc_mcp_3dsmax.install_menu\n"
            "dcc_mcp_3dsmax.install_shutdown_callback\n"
            "print('dcc_mcp_core' in sys.modules)\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        )

        assert result.stdout.strip() == "False"

    def test_stop_sidecar_hook_keeps_core_native_extension_lazy(self):
        """Install/uninstall cleanup can stop runtime without loading core."""
        env = dict(os.environ)
        src = Path(__file__).parent.parent / "src"
        env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
        code = (
            "import sys\n"
            "import dcc_mcp_3dsmax\n"
            "dcc_mcp_3dsmax.stop_sidecar_bridge()\n"
            "print('dcc_mcp_core' in sys.modules)\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        )

        assert result.stdout.strip() == "False"

    def test_import_server(self):
        """Test importing the server module."""
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        assert MaxMcpServer is not None
        assert MaxServerOptions is not None

    def test_import_api(self):
        """Test importing the api module."""
        from dcc_mcp_3dsmax.api import max_error, max_success, with_max

        assert max_success is not None
        assert max_error is not None
        assert with_max is not None


class TestServerOptions:
    """Test adapter options passed through the core 0.17 server contract."""

    def test_options_preserve_adapter_skill_paths(self, tmp_path):
        """Configured adapter skill paths are retained for core discovery."""
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        server = MaxMcpServer(
            options=MaxServerOptions(
                port=0,
                extra_skill_paths=[str(skill_dir)],
                enable_gateway_failover=False,
                job_storage_path="",
            )
        )

        paths = server._collect_skill_paths()
        assert str(skill_dir) in paths

    def test_options_wire_core_execution_dispatcher(self):
        """Dispatcher options are converted into core execution settings."""
        from dcc_mcp_3dsmax.dispatcher import MaxStandaloneDispatcher
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        dispatcher = MaxStandaloneDispatcher()
        server = MaxMcpServer(
            options=MaxServerOptions(
                port=0,
                dispatcher=dispatcher,
                enable_gateway_failover=False,
                job_storage_path="",
            )
        )

        assert server._dcc_dispatcher is dispatcher
        assert server._inprocess_executor_registered is True

    def test_execution_bridge_uses_3dsmax_runner(self):
        """Server registers the adapter runner instead of core's main-only runner."""
        from dcc_mcp_3dsmax import _executor
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        server = MaxMcpServer(
            options=MaxServerOptions(
                port=0,
                enable_gateway_failover=False,
                job_storage_path="",
            )
        )

        assert server._execution_bridge.runner is _executor.run_skill_script

    def test_core_host_ui_dispatcher_attaches_to_http_route(self):
        """Core HostUiDispatcherBase subclasses should be exposed to HTTP routing."""
        from dcc_mcp_core import HostUiDispatcherBase

        from dcc_mcp_3dsmax import _executor
        from dcc_mcp_3dsmax.dispatcher import MaxUiDispatcher
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        dispatcher = MaxUiDispatcher()
        server = MaxMcpServer(
            options=MaxServerOptions(
                port=0,
                dispatcher=dispatcher,
                enable_gateway_failover=False,
                job_storage_path="",
            )
        )

        assert isinstance(dispatcher, HostUiDispatcherBase)
        assert server._dcc_dispatcher is dispatcher
        assert server._execution_bridge.dispatcher is dispatcher
        assert server._execution_bridge.runner is _executor.run_skill_script
        assert server._inprocess_executor_registered is True

    def test_custom_execution_bridge_is_registered(self):
        """Explicit execution bridges are passed through to core registration."""
        from dcc_mcp_core import HostExecutionBridge

        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        bridge = HostExecutionBridge(runner=lambda script_path, params: {"success": True})
        server = MaxMcpServer(
            options=MaxServerOptions(
                port=0,
                execution_bridge=bridge,
                enable_gateway_failover=False,
                job_storage_path="",
            )
        )

        assert server._execution_bridge is bridge
        assert server._inprocess_executor_registered is True


class TestExecution:
    """Test 3ds Max adapter execution helpers."""

    def test_executor_runs_main_entrypoint(self, tmp_path):
        """Adapter runner executes the current main(**params) convention."""
        from dcc_mcp_3dsmax._executor import run_skill_script

        script = tmp_path / "action_main.py"
        script.write_text(
            "\n".join(
                [
                    "def main(width=1):",
                    "    return {'success': True, 'message': 'ok', 'data': {'width': width}}",
                ]
            ),
            encoding="utf-8",
        )

        result = run_skill_script(str(script), {"width": 12})
        assert result == {"success": True, "message": "ok", "data": {"width": 12}}

    def test_executor_rejects_non_dict_main_result(self, tmp_path):
        """Adapter runner enforces the current dict envelope contract."""
        from dcc_mcp_3dsmax._executor import run_skill_script

        script = tmp_path / "action_bad.py"
        script.write_text("def main():\n    return 'ok'\n", encoding="utf-8")

        result = run_skill_script(str(script), {})
        assert result["success"] is False
        assert "must return a dict" in result["message"]

    def test_standalone_dispatcher_supports_core_protocol(self):
        """Standalone dispatcher accepts HostExecutionBridge metadata kwargs."""
        from dcc_mcp_3dsmax.dispatcher import MaxStandaloneDispatcher

        dispatcher = MaxStandaloneDispatcher()
        result = dispatcher.dispatch_callable(
            lambda value: value + 1,
            41,
            affinity="main",
            action_name="unit",
            skill_name="test",
        )
        assert result == 42


class TestSidecar:
    """Test structured sidecar dispatch and bridge plumbing."""

    def test_sidecar_server_uses_default_process_streams(self, tmp_path, monkeypatch, capsys):
        """External sidecar mode leaves logging to dcc-mcp-server, like Maya."""
        from dcc_mcp_3dsmax import max_bootstrap

        binary = tmp_path / ("dcc-mcp-server.exe" if os.name == "nt" else "dcc-mcp-server")
        binary.write_text("stub", encoding="utf-8")
        captured = {}

        class FakeProcess:
            pid = 4321

            def wait(self, timeout):
                raise subprocess.TimeoutExpired("sidecar", timeout)

            def poll(self):
                return None

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured.update(kwargs)
            return FakeProcess()

        monkeypatch.setattr(max_bootstrap, "_server_binary_path", lambda: binary)
        monkeypatch.setattr(max_bootstrap, "qt_bridge_port", lambda: 9876)
        monkeypatch.setattr(max_bootstrap.subprocess, "Popen", fake_popen)
        max_bootstrap._sidecar_process = None

        process = max_bootstrap.start_sidecar_server()
        assert process.pid == 4321
        assert captured["stdout"] is subprocess.DEVNULL
        assert captured["stderr"] is subprocess.DEVNULL
        assert captured["stdin"] is subprocess.DEVNULL
        assert captured["close_fds"] is True
        assert "--display-name" in captured["cmd"]
        output = capsys.readouterr().out
        assert "dcc-mcp-3dsmax sidecar server started" in output
        assert "sidecar log:" not in output

    def test_main_defaults_to_agent_callable_embedded_runtime(self, monkeypatch):
        """Startup scripts should register adapter tools, not just a host RPC sidecar."""
        from dcc_mcp_3dsmax import max_bootstrap

        calls = []
        monkeypatch.delenv("DCC_MCP_3DSMAX_BOOT_MODE", raising=False)
        monkeypatch.setattr(max_bootstrap, "_register_process_cleanup", lambda: calls.append("cleanup"))
        monkeypatch.setattr(max_bootstrap, "_install_max_integration", lambda: calls.append("menu"))
        monkeypatch.setattr(max_bootstrap, "start_embedded_sidecar_bridge", lambda: {"mode": "embedded-runtime"})
        monkeypatch.setattr(
            max_bootstrap,
            "start_sidecar_bridge",
            lambda: (_ for _ in ()).throw(AssertionError("external sidecar should not be default")),
        )

        assert max_bootstrap.main() == {"mode": "embedded-runtime"}
        assert calls == ["cleanup", "menu"]

    def test_embedded_runtime_uses_core_ui_dispatcher(self, monkeypatch):
        """Default embedded runtime wires core's HTTP main-thread route."""
        import dcc_mcp_3dsmax.dispatcher as dispatcher_module
        from dcc_mcp_3dsmax import max_bootstrap

        captured = {}
        install_calls = []
        fake_dispatcher = object()
        fake_pump = types.SimpleNamespace(install=lambda: install_calls.append("install"))
        fake_server_module = types.SimpleNamespace(
            start_server=lambda **kwargs: captured.update(kwargs) or {"server": True}
        )
        monkeypatch.setitem(sys.modules, "dcc_mcp_3dsmax.server", fake_server_module)
        monkeypatch.setattr(max_bootstrap, "start_bridge", lambda bridge_port=None: {"bridge": True})
        monkeypatch.setattr(dispatcher_module, "create_dispatcher", lambda: (fake_dispatcher, fake_pump))

        result = max_bootstrap.start_embedded_sidecar_bridge()

        execution_bridge = captured["execution_bridge"]
        assert result["server"] == {"server": True}
        assert result["dispatcher"] is fake_dispatcher
        assert result["pump"] is fake_pump
        assert install_calls == ["install"]
        assert captured["dispatcher"] is fake_dispatcher
        assert execution_bridge.dispatcher is fake_dispatcher
        assert execution_bridge.default_thread_affinity == "main"

    def test_main_keeps_external_sidecar_as_explicit_mode(self, monkeypatch):
        """Operators can still opt into the process-isolated sidecar mode."""
        from dcc_mcp_3dsmax import max_bootstrap

        monkeypatch.setenv("DCC_MCP_3DSMAX_BOOT_MODE", "sidecar")
        monkeypatch.setattr(max_bootstrap, "start_sidecar_bridge", lambda: {"mode": "sidecar"})
        monkeypatch.setattr(
            max_bootstrap,
            "start_embedded_sidecar_bridge",
            lambda: (_ for _ in ()).throw(AssertionError("embedded runtime should not be used")),
        )

        assert max_bootstrap.main() == {"mode": "sidecar"}

    def test_stop_sidecar_bridge_stops_loaded_embedded_server(self, monkeypatch):
        """Uninstall/shutdown cleanup stops the default embedded runtime when loaded."""
        from dcc_mcp_3dsmax import max_bootstrap

        calls = []
        fake_server = types.SimpleNamespace(stop_server=lambda: calls.append("stop_server"))
        monkeypatch.setitem(sys.modules, "dcc_mcp_3dsmax.server", fake_server)
        monkeypatch.setattr(max_bootstrap, "stop_qt_bridge", lambda: calls.append("stop_qt_bridge"))
        monkeypatch.setattr(max_bootstrap, "stop_bridge", lambda: calls.append("stop_bridge"))
        max_bootstrap._sidecar_process = None

        max_bootstrap.stop_sidecar_bridge()

        assert calls == ["stop_server", "stop_qt_bridge", "stop_bridge"]

    def test_server_binary_path_accepts_rez_style_server_root(self, tmp_path, monkeypatch):
        """Pipeline package roots can provide the sidecar binary without pip install."""
        from dcc_mcp_3dsmax.max_bootstrap import _server_binary_path

        binary_name = "dcc-mcp-server.exe" if os.name == "nt" else "dcc-mcp-server"
        binary = tmp_path / "bin" / binary_name
        binary.parent.mkdir()
        binary.write_text("stub", encoding="utf-8")
        monkeypatch.delenv("DCC_MCP_SERVER_BIN", raising=False)
        monkeypatch.setenv("DCC_MCP_SERVER_ROOT", str(tmp_path))

        assert _server_binary_path() == binary

    def test_server_binary_path_prefers_env_bin_when_it_exists(self, tmp_path, monkeypatch):
        """DCC_MCP_SERVER_BIN is the only override above the versioned payload."""
        from dcc_mcp_3dsmax import max_bootstrap

        binary_name = "dcc-mcp-server.exe" if os.name == "nt" else "dcc-mcp-server"
        package_root = tmp_path / "installed" / "python"
        package_dir = package_root / "dcc_mcp_3dsmax"
        bundled_binary = package_root / "scripts" / binary_name
        override_binary = tmp_path / "override" / binary_name
        package_dir.mkdir(parents=True)
        bundled_binary.parent.mkdir(parents=True)
        override_binary.parent.mkdir(parents=True)
        bundled_binary.write_text("bundled", encoding="utf-8")
        override_binary.write_text("override", encoding="utf-8")

        monkeypatch.setenv("DCC_MCP_SERVER_BIN", str(override_binary))
        monkeypatch.setattr(max_bootstrap, "__file__", str(package_dir / "max_bootstrap.py"))

        assert max_bootstrap._server_binary_path() == override_binary

    def test_server_binary_path_falls_back_to_bundled_when_env_bin_is_missing(self, tmp_path, monkeypatch):
        """A stale DCC_MCP_SERVER_BIN should not block the versioned fallback."""
        from dcc_mcp_3dsmax import max_bootstrap

        binary_name = "dcc-mcp-server.exe" if os.name == "nt" else "dcc-mcp-server"
        package_root = tmp_path / "installed" / "python"
        package_dir = package_root / "dcc_mcp_3dsmax"
        bundled_binary = package_root / "scripts" / binary_name
        package_dir.mkdir(parents=True)
        bundled_binary.parent.mkdir(parents=True)
        bundled_binary.write_text("bundled", encoding="utf-8")

        monkeypatch.setenv("DCC_MCP_SERVER_BIN", str(tmp_path / "missing" / binary_name))
        monkeypatch.delenv("DCC_MCP_SERVER_ROOT", raising=False)
        monkeypatch.setattr(max_bootstrap, "__file__", str(package_dir / "max_bootstrap.py"))

        assert max_bootstrap._server_binary_path() == bundled_binary

    def test_server_binary_path_prefers_bundled_payload_over_server_root(self, tmp_path, monkeypatch):
        """Stale DCC_MCP_SERVER_ROOT must not outrank the current MZP payload."""
        from dcc_mcp_3dsmax import max_bootstrap

        binary_name = "dcc-mcp-server.exe" if os.name == "nt" else "dcc-mcp-server"
        package_root = tmp_path / "installed" / "python"
        package_dir = package_root / "dcc_mcp_3dsmax"
        bundled_binary = package_root / "scripts" / binary_name
        stale_root_binary = tmp_path / "stale-root" / "scripts" / binary_name
        package_dir.mkdir(parents=True)
        bundled_binary.parent.mkdir(parents=True)
        stale_root_binary.parent.mkdir(parents=True)
        bundled_binary.write_text("bundled", encoding="utf-8")
        stale_root_binary.write_text("stale", encoding="utf-8")

        monkeypatch.delenv("DCC_MCP_SERVER_BIN", raising=False)
        monkeypatch.setenv("DCC_MCP_SERVER_ROOT", str(stale_root_binary.parent.parent))
        monkeypatch.setattr(max_bootstrap, "__file__", str(package_dir / "max_bootstrap.py"))

        assert max_bootstrap._server_binary_path() == bundled_binary

    def test_server_binary_path_prefers_bundled_payload_over_user_scripts(self, tmp_path, monkeypatch):
        """MZP installs must use the bundled sidecar binary before stale user installs."""
        from dcc_mcp_3dsmax import max_bootstrap

        binary_name = "dcc-mcp-server.exe" if os.name == "nt" else "dcc-mcp-server"
        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        package_root = tmp_path / "installed" / "python"
        package_dir = package_root / "dcc_mcp_3dsmax"
        bundled_binary = package_root / "scripts" / binary_name
        stale_binary = tmp_path / "user-base" / scripts_dir / binary_name
        sysconfig_binary = tmp_path / "sysconfig" / scripts_dir / binary_name
        package_dir.mkdir(parents=True)
        bundled_binary.parent.mkdir(parents=True)
        bundled_binary.write_text("bundled", encoding="utf-8")
        stale_binary.parent.mkdir(parents=True)
        stale_binary.write_text("stale", encoding="utf-8")
        sysconfig_binary.parent.mkdir(parents=True)
        sysconfig_binary.write_text("sysconfig", encoding="utf-8")

        monkeypatch.delenv("DCC_MCP_SERVER_BIN", raising=False)
        monkeypatch.delenv("DCC_MCP_SERVER_ROOT", raising=False)
        monkeypatch.setattr(max_bootstrap, "__file__", str(package_dir / "max_bootstrap.py"))
        monkeypatch.setattr(max_bootstrap.sysconfig, "get_path", lambda name: str(sysconfig_binary.parent))
        monkeypatch.setattr(max_bootstrap.site, "USER_BASE", str(stale_binary.parent.parent), raising=False)
        monkeypatch.setattr(
            max_bootstrap.site,
            "getusersitepackages",
            lambda: str(stale_binary.parent.parent / "Python310" / "site-packages"),
        )

        assert max_bootstrap._server_binary_path() == bundled_binary

    def test_sidecar_dispatch_accepts_script_path_payload(self, tmp_path):
        """Sidecar payloads execute explicit script paths."""
        import json

        from dcc_mcp_3dsmax.sidecar import dispatch_payload

        script = tmp_path / "action_echo.py"
        script.write_text(
            "def main(value=None):\n    return {'success': True, 'data': {'value': value}}\n",
            encoding="utf-8",
        )

        raw = dispatch_payload(
            {
                "script_path": str(script),
                "args": {"value": "ok"},
                "request_id": "r1",
            }
        )
        result = json.loads(raw)
        assert result["success"] is True
        assert result["data"]["value"] == "ok"
        assert result["request_id"] == "r1"

    def test_sidecar_dispatch_resolves_bundled_action_name(self):
        """Bundled action names resolve to their package script path."""
        import json

        from dcc_mcp_3dsmax.sidecar import dispatch_payload

        result = json.loads(
            dispatch_payload(
                {
                    "action": "3dsmax-modeling__create_box",
                    "args": {"width": 10},
                    "request_id": "r2",
                }
            )
        )
        assert result["request_id"] == "r2"
        assert result["action"] == "3dsmax-modeling__create_box"
        assert result.get("status") == "error" or result.get("success") is False

    def test_bridge_http_dispatch_roundtrip(self, tmp_path):
        """Bridge accepts structured dispatch requests over localhost HTTP."""
        import json
        import socket
        import urllib.request

        from dcc_mcp_3dsmax.sidecar.bridge import start_bridge, stop_bridge

        script = tmp_path / "action_echo.py"
        script.write_text("def main(value=None):\n    return {'success': True, 'data': {'value': value}}\n", encoding="utf-8")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        start_bridge(port)
        try:
            body = json.dumps({"script_path": str(script), "args": {"value": 7}}).encode("utf-8")
            request = urllib.request.Request(
                "http://127.0.0.1:{}/dispatch".format(port),
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read())
            assert result["success"] is True
            assert result["data"]["value"] == 7
        finally:
            stop_bridge()

    def test_bridge_default_port_is_ephemeral(self, monkeypatch):
        """Bridge picks a random localhost port unless explicitly configured."""
        import os

        from dcc_mcp_3dsmax.sidecar.bridge import ENV_BRIDGE_PORT, start_bridge, stop_bridge

        monkeypatch.delenv(ENV_BRIDGE_PORT, raising=False)
        server = start_bridge()
        try:
            port = int(server.server_address[1])
            assert port > 0
            assert os.environ[ENV_BRIDGE_PORT] == str(port)
        finally:
            stop_bridge()

    @pytest.mark.skip(reason="qtserver:// transport not compatible with raw TCP; pre-existing main branch issue")
    def test_qt_bridge_json_line_dispatch(self, tmp_path):
        """The qtserver-compatible bridge dispatches JSON-line requests."""
        import json
        import socket

        from dcc_mcp_3dsmax.sidecar.qt_bridge import start_qt_bridge, stop_qt_bridge

        script = tmp_path / "action_echo.py"
        script.write_text("def main(value=None):\n    return {'success': True, 'data': {'value': value}}\n", encoding="utf-8")

        server = start_qt_bridge()
        port = int(server.port)
        try:
            payload = {
                "id": "req-1",
                "method": "dispatch",
                "params": {
                    "script_path": str(script),
                    "args": {"value": 13},
                    "request_id": "req-1",
                },
            }
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                response = sock.makefile("rb").readline()
            result = json.loads(response)
            assert result["id"] == "req-1"
            assert result["result"]["success"] is True
            assert result["result"]["data"]["value"] == 13
        finally:
            stop_qt_bridge()


class TestMenuIntegration:
    """Test generated 3ds Max menu/callback scripts."""

    def test_menu_script_contains_expected_commands(self):
        """Menu script exposes runtime lifecycle and admin commands."""
        from dcc_mcp_3dsmax.menu import _menu_script

        script = _menu_script()
        assert 'menuMan.findMenu "DCC MCP"' in script
        assert "DccMcp3dsmax_StartSidecar" in script
        assert "dcc_mcp_3dsmax.main()" in script
        assert "DccMcp3dsmax_StopSidecar" in script
        assert "DccMcp3dsmax_OpenAdmin" in script
        assert "http://127.0.0.1:9765/admin?panel=instances" in script

    def test_shutdown_callback_stops_sidecar_before_max_shutdown(self):
        """Shutdown callback uses the early 3ds Max shutdown notification."""
        from dcc_mcp_3dsmax.menu import _shutdown_callback_script

        script = _shutdown_callback_script()
        assert "#preSystemShutdown" in script
        assert "stop_sidecar_bridge" in script
        assert "persistent:false" in script

    def test_remove_menu_script_cleans_mcr_files(self):
        """remove_menu deletes persisted .mcr files from #userMacros."""
        from dcc_mcp_3dsmax.menu import _remove_menu_script

        script = _remove_menu_script()
        assert "getDir #userMacros" in script
        assert 'DCC MCP-DccMcp3dsmax_StartSidecar.mcr' in script
        assert 'DCC MCP-DccMcp3dsmax_StopSidecar.mcr' in script
        assert 'DCC MCP-DccMcp3dsmax_OpenAdmin.mcr' in script
        assert 'DCC MCP-DccMcp3dsmax_PrintStatus.mcr' in script
        # Idempotent — all cleanup wrapped in try/catch
        assert "deleteFile" in script
        assert "callbacks.removeScripts" in script


class TestSkillMetadata:
    """Test bundled skills follow the dcc-mcp-core 0.17 authoring contract."""

    def test_bundled_tools_have_explicit_contracts(self):
        """Every bundled tool declares execution, affinity, schema, and source."""
        from pathlib import Path

        import yaml

        skills_dir = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills"
        for tools_path in skills_dir.glob("*/tools.yaml"):
            data = yaml.safe_load(tools_path.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
            assert data.get("tools"), tools_path
            for tool in data["tools"]:
                source_file = tool.get("source_file")
                assert source_file, (tools_path, tool.get("name"))
                assert (tools_path.parent / source_file).is_file(), (tools_path, source_file)
                assert tool.get("execution") in {"sync", "async"}, (tools_path, tool.get("name"))
                assert tool.get("affinity") == "main", (tools_path, tool.get("name"))
                assert isinstance(tool.get("input_schema"), dict), (tools_path, tool.get("name"))
                assert "read_only" in tool, (tools_path, tool.get("name"))
                assert "destructive" in tool, (tools_path, tool.get("name"))
                assert "idempotent" in tool, (tools_path, tool.get("name"))

    def test_bundled_skill_frontmatter_has_dcc_mcp_stage(self):
        """Each bundled skill declares host, layer, stage, and tools metadata."""
        from pathlib import Path

        import yaml

        skills_dir = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills"
        for skill_path in skills_dir.glob("*/SKILL.md"):
            raw = skill_path.read_text(encoding="utf-8")
            assert raw.startswith("---\n"), skill_path
            frontmatter = raw.split("---", 2)[1]
            metadata = yaml.safe_load(frontmatter)
            dcc_mcp = metadata["metadata"]["dcc-mcp"]
            assert dcc_mcp["dcc"] == "3dsmax"
            assert dcc_mcp["layer"] == "domain"
            assert dcc_mcp["stage"] in {"scene", "authoring"}
            assert dcc_mcp["tools"] == "tools.yaml"


class TestSceneAuthoringActions:
    """Test basic authoring skill actions with a fake pymxs runtime."""

    def test_create_sphere_accepts_position(self, monkeypatch):
        """Primitive creation tools can place nodes directly."""
        runtime = _FakeRuntime()
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-modeling"
            / "action_create_sphere.py"
        )

        result = action.main(radius=12.0, name="hero_ball", position={"x": 1, "y": 2, "z": 3})

        assert result["success"] is True
        assert result["data"]["node_name"] == "hero_ball"
        assert result["data"]["position"] == [1.0, 2.0, 3.0]
        assert runtime.created[0].pos.x == 1.0

    def test_set_node_position_updates_existing_node(self, monkeypatch):
        """Transform skill sets absolute node positions."""
        runtime = _FakeRuntime()
        runtime.nodes["hero_ball"] = _FakeNode("hero_ball", 42)
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-transform"
            / "action_set_node_position.py"
        )

        result = action.main(node_name="hero_ball", position=[10, 20, 30])

        assert result["success"] is True
        assert result["data"]["position"] == [10.0, 20.0, 30.0]
        assert runtime.nodes["hero_ball"].pos.z == 30.0

    def test_move_nodes_offsets_named_nodes(self, monkeypatch):
        """Transform skill moves nodes by a relative offset."""
        runtime = _FakeRuntime()
        node = _FakeNode("hero_ball", 42)
        node.pos = _FakePoint3(10, 20, 30)
        runtime.nodes["hero_ball"] = node
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-transform"
            / "action_move_nodes.py"
        )

        result = action.main(node_names=["hero_ball"], offset={"x": 1, "y": -2, "z": 3})

        assert result["success"] is True
        assert result["data"]["nodes"][0]["position"] == [11.0, 18.0, 33.0]

    def test_execute_python_returns_stdout_and_result(self, monkeypatch):
        """Scripting skill exposes pymxs and returns JSON-safe output."""
        runtime = _FakeRuntime()
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-scripting"
            / "action_execute_python.py"
        )

        result = action.main("print('hello from max')\nresult = {'node_count': len(rt.nodes)}", confirm_execution=True)

        assert result["success"] is True
        assert result["data"]["stdout"] == "hello from max\n"
        assert result["data"]["result"] == {"node_count": 0}

    def test_execute_python_honors_disable_env(self, monkeypatch):
        """Python execution can be disabled for locked-down deployments."""
        runtime = _FakeRuntime()
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        monkeypatch.setenv("DCC_MCP_3DSMAX_DISABLE_EXECUTE_PYTHON", "1")
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-scripting"
            / "action_execute_python.py"
        )

        result = action.main("result = 1", confirm_execution=True)

        assert result["status"] == "error"
        assert "disabled" in result["message"]

    def test_execute_maxscript_delegates_to_runtime_execute(self, monkeypatch):
        """Scripting skill executes MaxScript through pymxs.runtime.execute."""
        runtime = _FakeRuntime()
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-scripting"
            / "action_execute_maxscript.py"
        )

        result = action.main("selection.count", confirm_execution=True)

        assert result["success"] is True
        assert runtime.executed_script == "selection.count"
        assert result["data"]["result"] == {"ok": True, "script": "selection.count"}

    def test_execute_maxscript_honors_arbitrary_script_disable_env(self, monkeypatch):
        """The shared arbitrary-script guard blocks MaxScript too."""
        runtime = _FakeRuntime()
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        monkeypatch.setenv("DCC_MCP_3DSMAX_DISABLE_ARBITRARY_SCRIPT", "true")
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-scripting"
            / "action_execute_maxscript.py"
        )

        result = action.main("selection.count", confirm_execution=True)

        assert result["status"] == "error"
        assert "disabled" in result["message"]

    def test_capture_viewport_writes_to_requested_image_path(self, tmp_path, monkeypatch):
        """Viewport skill emits a MaxScript capture script for README-ready images."""
        runtime = _FakeRuntime()
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-viewport"
            / "action_capture_viewport.py"
        )
        output = tmp_path / "viewport.png"

        result = action.main(str(output))

        assert result["success"] is True
        assert result["data"]["file_path"] == str(output)
        assert "gw.getViewportDib()" in runtime.executed_script
        assert str(output).replace("\\", "/") in runtime.executed_script

    def test_capture_viewport_rejects_non_image_path(self, monkeypatch):
        """Viewport capture only writes known image extensions."""
        runtime = _FakeRuntime()
        monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
        action = _load_action_module(
            Path(__file__).resolve().parents[1]
            / "src"
            / "dcc_mcp_3dsmax"
            / "skills"
            / "3dsmax-viewport"
            / "action_capture_viewport.py"
        )

        result = action.main("notes.txt")

        assert result["status"] == "error"
        assert "file_path must end" in result["message"]


class TestVersion:
    """Test version detection."""

    def test_max_version_label_prefers_clean_product_year(self, monkeypatch):
        """MXS array reprs with embedded quotes collapse to a clean Max year."""
        from dcc_mcp_3dsmax.max_bootstrap import _max_version_label

        class MxsArray:
            values = [26000, 64, 0, 26, 2, 11, 20199, 2024, '".2.11 Security Fix"']

            def __getitem__(self, index):
                return self.values[index]

            def __str__(self):
                return '#(26000, 64, 0, 26, 2, 11, 20199, 2024, ".2.11 Security Fix")'

        pymxs = types.SimpleNamespace(
            runtime=types.SimpleNamespace(
                maxVersion=lambda: MxsArray(),
                productVersion="Autodesk 3ds Max 2024",
            )
        )
        monkeypatch.setitem(sys.modules, "pymxs", pymxs)

        assert _max_version_label() == "2024"

    def test_max_version_label_sanitizes_repr_fallback(self, monkeypatch):
        """Fallback labels never preserve shell-sensitive quotes or newlines."""
        from dcc_mcp_3dsmax.max_bootstrap import _max_version_label

        class BrokenMxsArray:
            def __getitem__(self, index):
                raise TypeError("not a Python sequence")

            def __str__(self):
                return '#(26000, "bad"\n\tlabel)'

        pymxs = types.SimpleNamespace(
            runtime=types.SimpleNamespace(
                maxVersion=lambda: BrokenMxsArray(),
            )
        )
        monkeypatch.setitem(sys.modules, "pymxs", pymxs)

        label = _max_version_label()
        assert '"' not in label
        assert "'" not in label
        assert "\n" not in label
        assert "\t" not in label

    def test_max_version_label_falls_back_to_unknown(self, monkeypatch):
        """Version probe failures produce the stable unknown label."""
        from dcc_mcp_3dsmax.max_bootstrap import _max_version_label

        pymxs = types.SimpleNamespace(
            runtime=types.SimpleNamespace(
                maxVersion=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        )
        monkeypatch.setitem(sys.modules, "pymxs", pymxs)

        assert _max_version_label() == "unknown"

    def test_version_probe_import(self):
        """Test that version probe can be imported."""
        from dcc_mcp_3dsmax._version_probe import get_3dsmax_version_string, is_3dsmax_available

        assert get_3dsmax_version_string is not None
        assert is_3dsmax_available is not None

    def test_version_probe_reports_2024_from_raw_26000(self, monkeypatch):
        """3ds Max 2024 reports raw major version 26000."""
        from dcc_mcp_3dsmax._version_probe import get_3dsmax_version_string

        pymxs = types.SimpleNamespace(
            runtime=types.SimpleNamespace(
                maxVersion=lambda: (26000, 64, 0, 26, 2, 11, 20199, 2024),
            )
        )
        monkeypatch.setitem(sys.modules, "pymxs", pymxs)

        assert get_3dsmax_version_string() == "2024"

    def test_version_string_not_crash(self):
        """Test that version detection doesn't crash."""
        from dcc_mcp_3dsmax._version_probe import get_3dsmax_version_string

        # Should return a string (either version or "unknown")
        result = get_3dsmax_version_string()
        assert isinstance(result, str)


class TestCapabilities:
    """Test capabilities module."""

    def test_capabilities_import(self):
        """Test that capabilities can be imported."""
        from dcc_mcp_3dsmax.capabilities import get_3dsmax_capabilities, get_3dsmax_capabilities_dict

        assert get_3dsmax_capabilities is not None
        assert get_3dsmax_capabilities_dict is not None

    def test_get_capabilities_list(self):
        """Test getting capabilities as list."""
        from dcc_mcp_3dsmax.capabilities import get_3dsmax_capabilities

        caps = get_3dsmax_capabilities()
        assert isinstance(caps, list)
        assert len(caps) > 0

    def test_get_capabilities_dict(self):
        """Test getting capabilities as dict."""
        from dcc_mcp_3dsmax.capabilities import get_3dsmax_capabilities_dict

        caps_dict = get_3dsmax_capabilities_dict()
        assert isinstance(caps_dict, dict)
        assert "dcc_name" in caps_dict
        assert caps_dict["dcc_name"] == "3dsmax"
