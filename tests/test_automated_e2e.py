"""Automated E2E debug tests for dcc-mcp-3dsmax.

Run: pytest tests/test_automated_e2e.py -v
"""

from __future__ import annotations

import json
import sys
import threading
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Fake pymxs runtime ────────────────────────────────────────────────────
class FakePoint3:
    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class FakeNode:
    def __init__(self, name="Node", handle=1001):
        self.name = name
        self.handle = handle
        self.pos = FakePoint3(0, 0, 0)


class FakeUnits:
    @staticmethod
    def SystemType():
        return "Centimeters"


class FakeRuntime:
    def __init__(self):
        self.created = []
        self.nodes = {}
        self.selection = []
        self.executed_script = None
        self.maxFileName = ""
        self.objects = []
        self.units = FakeUnits()
        self.sliderTime = 0.0

    def maxVersion(self):
        return (26000,)

    def Point3(self, x, y, z):
        return FakePoint3(x, y, z)

    def Sphere(self, radius=50.0):
        n = FakeNode(f"Sphere{len(self.created) + 1:03d}", 2000 + len(self.created))
        n.radius = radius
        self.created.append(n)
        self.nodes[n.name] = n
        return n

    def Box(self, width=100.0, height=100.0, depth=100.0, **kw):
        n = FakeNode(f"Box{len(self.created) + 1:03d}", 3000 + len(self.created))
        n.width = width
        n.height = height
        n.depth = depth
        self.created.append(n)
        self.nodes[n.name] = n
        return n

    def Cylinder(self, radius=30.0, height=100.0, **kw):
        n = FakeNode(f"Cylinder{len(self.created) + 1:03d}", 4000 + len(self.created))
        n.radius = radius
        n.height = height
        self.created.append(n)
        self.nodes[n.name] = n
        return n

    def Plane(self, width=100.0, length=100.0, **kw):
        n = FakeNode(f"Plane{len(self.created) + 1:03d}", 5000 + len(self.created))
        n.width = width
        n.length = length
        self.created.append(n)
        self.nodes[n.name] = n
        return n

    def getNodeByName(self, name):
        return self.nodes.get(name)

    def clearSelection(self):
        self.selection.clear()

    def selectMore(self, node):
        self.selection.append(node)

    def execute(self, script):
        self.executed_script = script
        return {"ok": True}

    def playAnimation(self):
        return None

    def StandardMaterial(self, name="StandardMat", **kw):
        n = FakeNode(name, 6000 + len(self.created))
        self.created.append(n)
        return n

    def color(self, r, g, b):
        return f"color({r},{g},{b})"

    def animate(self, on):
        pass

    def setKey(self, controller, time):
        pass

    def point3(self, x, y, z):
        return FakePoint3(x, y, z)

    def quat(self, x, y, z, w=0):
        return {"x": x, "y": y, "z": z, "w": w}


def _install_fake_pymxs(monkeypatch):
    rt = FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=rt))
    return rt


SKILLS_DIR = Path(__file__).parent.parent / "src" / "dcc_mcp_3dsmax" / "skills"
EXPECTED_SKILLS = {
    "3dsmax-animation",
    "3dsmax-asset-import",
    "3dsmax-asset-source",
    "3dsmax-camera-lighting",
    "3dsmax-display",
    "3dsmax-geometry-io",
    "3dsmax-import-to-scene",
    "3dsmax-lookdev",
    "3dsmax-materials",
    "3dsmax-mesh-ops",
    "3dsmax-modeling",
    "3dsmax-render",
    "3dsmax-rigging",
    "3dsmax-scene",
    "3dsmax-scripting",
    "3dsmax-transform",
    "3dsmax-uv-atlas",
    "3dsmax-validation",
    "3dsmax-viewport",
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Environment
# ═══════════════════════════════════════════════════════════════════════════
class TestEnvironment:
    def test_python_version(self):
        assert sys.version_info >= (3, 7)

    def test_package_import(self):
        import dcc_mcp_3dsmax

        assert dcc_mcp_3dsmax.__version__

    def test_core_import(self):
        from dcc_mcp_core import DccServerBase, DccServerOptions

        assert DccServerOptions and DccServerBase

    def test_all_submodules(self):
        for m in [
            "dcc_mcp_3dsmax",
            "dcc_mcp_3dsmax.server",
            "dcc_mcp_3dsmax.api",
            "dcc_mcp_3dsmax._executor",
            "dcc_mcp_3dsmax.max_bootstrap",
            "dcc_mcp_3dsmax.capabilities",
        ]:
            __import__(m)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Skill Discovery & Validation
# ═══════════════════════════════════════════════════════════════════════════
class TestSkillDiscovery:
    def test_all_skills_present(self):
        actual = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name.startswith("3dsmax-")}
        assert actual == EXPECTED_SKILLS, f"Missing: {EXPECTED_SKILLS - actual}"

    def test_skill_md_and_tools_yaml(self):
        for d in SKILLS_DIR.iterdir():
            if d.is_dir() and d.name.startswith("3dsmax-"):
                assert (d / "SKILL.md").is_file(), f"{d.name}: SKILL.md missing"
                assert (d / "tools.yaml").is_file(), f"{d.name}: tools.yaml missing"

    def test_tools_yaml_schema_valid(self):
        for d in SKILLS_DIR.iterdir():
            if not d.is_dir() or not d.name.startswith("3dsmax-"):
                continue
            data = yaml.safe_load((d / "tools.yaml").read_text(encoding="utf-8"))
            assert "tools" in data and isinstance(data["tools"], list), f"{d.name}: invalid tools.yaml"
            for tool in data["tools"]:
                for k in ("name", "description", "source_file"):
                    assert k in tool, f"{d.name}/{tool.get('name', '?')}: missing '{k}'"
                sf = d / tool["source_file"]
                assert sf.is_file(), f"{d.name}/{tool['name']}: source_file '{tool['source_file']}' not found"
                content = sf.read_text(encoding="utf-8")
                assert "def main(" in content, f"{sf.name}: missing main()"

    def test_total_tools_count(self):
        total = sum(
            len(yaml.safe_load((d / "tools.yaml").read_text(encoding="utf-8")).get("tools", []))
            for d in SKILLS_DIR.iterdir()
            if d.is_dir() and d.name.startswith("3dsmax-")
        )
        assert total >= 13, f"Expected at least 13 tools, got {total}"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Server Lifecycle
# ═══════════════════════════════════════════════════════════════════════════
class TestServerLifecycle:
    def test_create_and_stop(self):
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        s = MaxMcpServer(options=MaxServerOptions(port=0, enable_gateway_failover=False, job_storage_path=""))
        assert s and not s.is_running
        s.stop()

    def test_register_builtins(self):
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

        s = MaxMcpServer(options=MaxServerOptions(port=0, enable_gateway_failover=False, job_storage_path=""))
        s.register_builtin_actions(include_bundled=True)
        s.stop()

    def test_options_to_core(self):
        from dcc_mcp_3dsmax.server import MaxServerOptions

        opts = MaxServerOptions(
            port=0,
            gateway_port=9765,
            server_name="test",
            server_version="0.1.7",
            enable_gateway_failover=False,
            job_storage_path="",
        )
        core = opts.to_core_options()
        assert core and core.server_name == "test"

    def test_singleton_reuse(self):
        from dcc_mcp_3dsmax.server import MaxServerOptions, start_server, stop_server

        stop_server()
        try:
            opts = MaxServerOptions(port=0, enable_gateway_failover=False, job_storage_path="")
            s1 = start_server(options=opts)
            assert s1.is_running
            s2 = start_server(options=opts)
            assert s1 is s2
        finally:
            stop_server()

    def test_prepare_no_http(self):
        from dcc_mcp_3dsmax.server import MaxServerOptions, prepare_server, stop_server

        try:
            s = prepare_server(options=MaxServerOptions(port=0, enable_gateway_failover=False, job_storage_path=""))
            assert s and not s.is_running
        finally:
            stop_server()


# ═══════════════════════════════════════════════════════════════════════════
# 4. Executor
# ═══════════════════════════════════════════════════════════════════════════
class TestExecutor:
    def test_run_and_return(self, tmp_path):
        from dcc_mcp_3dsmax._executor import run_skill_script

        (tmp_path / "action.py").write_text(
            "def main(name='world', count=1):\n    return {'success': True, 'message': f'Hello {name}', 'count': count}\n"
        )
        r = run_skill_script(str(tmp_path / "action.py"), {"name": "Alice", "count": 5})
        assert r == {"success": True, "message": "Hello Alice", "count": 5}

    def test_reject_non_dict(self, tmp_path):
        from dcc_mcp_3dsmax._executor import run_skill_script

        (tmp_path / "bad.py").write_text("def main():\n    return 42\n")
        r = run_skill_script(str(tmp_path / "bad.py"), {})
        assert r["success"] is False and "must return a dict" in r["message"]

    def test_missing_script(self):
        from dcc_mcp_3dsmax._executor import run_skill_script

        r = run_skill_script("/nonexistent/script.py", {})
        assert r["success"] is False and "not found" in r["message"]

    def test_exception_caught(self, tmp_path):
        from dcc_mcp_3dsmax._executor import run_skill_script

        (tmp_path / "error.py").write_text("def main():\n    raise RuntimeError('Boom!')\n")
        r = run_skill_script(str(tmp_path / "error.py"), {})
        assert r["success"] is False and "Boom!" in r.get("message", "")

    def test_all_builtin_actions(self, monkeypatch):
        from dcc_mcp_3dsmax._executor import run_skill_script

        _install_fake_pymxs(monkeypatch)
        for d in SKILLS_DIR.iterdir():
            if not d.is_dir() or not d.name.startswith("3dsmax-"):
                continue
            for sf in d.glob("action_*.py"):
                r = run_skill_script(str(sf), {})
                assert isinstance(r, dict), f"{d.name}/{sf.name}: not dict"
                assert "status" in r or "success" in r, f"{d.name}/{sf.name}: missing status/success: {r}"

    def test_thread_safety(self, tmp_path):
        from dcc_mcp_3dsmax._executor import run_skill_script

        (tmp_path / "slow.py").write_text(
            "import time\ndef main(delay=0.01):\n    time.sleep(delay)\n    return {'success': True}\n"
        )
        results, errs = [], []

        def w():
            try:
                results.append(run_skill_script(str(tmp_path / "slow.py"), {"delay": 0.02}))
            except Exception as e:
                errs.append(e)

        threads = [threading.Thread(target=w) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert not errs and all(r["success"] for r in results)


# ═══════════════════════════════════════════════════════════════════════════
# 5. API Helpers
# ═══════════════════════════════════════════════════════════════════════════
class TestApiHelpers:
    def test_responses(self):
        from dcc_mcp_3dsmax.api import max_error, max_from_exception, max_success, max_warning

        for fn, status in [(max_success, "success"), (max_error, "error"), (max_warning, "warning")]:
            assert fn("msg", extra=1)["status"] == status
        r = max_from_exception(ValueError("bad"))
        assert r["status"] == "error" and r["exception_type"] == "ValueError"

    def test_params(self):
        from dcc_mcp_3dsmax.api import (
            MissingParamError,
            get_param,
            missing_param_error,
            require_any_param,
            require_param,
        )

        assert require_param({"x": 1}, "x") == 1
        with pytest.raises(MissingParamError):
            require_param({}, "x")
        assert require_any_param({"a": 1}, "a", "b") == 1
        assert require_any_param({"b": 2}, "a", "b") == 2
        with pytest.raises(MissingParamError):
            require_any_param({}, "a", "b")
        assert get_param({"x": 1}, "x", 0) == 1
        assert get_param({}, "x", 42) == 42
        assert missing_param_error("r")["status"] == "error"

    def test_is_max_available(self, monkeypatch):
        from dcc_mcp_3dsmax.api import is_max_available

        assert is_max_available() is False
        _install_fake_pymxs(monkeypatch)
        assert is_max_available() is True

    def test_with_max_decorator(self, monkeypatch):
        from dcc_mcp_3dsmax.api import with_max

        @with_max
        def f():
            return {"status": "success"}

        assert f()["status"] == "error"  # no pymxs
        _install_fake_pymxs(monkeypatch)
        assert f()["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Sidecar Bridge
# ═══════════════════════════════════════════════════════════════════════════
class TestSidecarBridge:
    def _http_get(self, url, timeout=5):
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read().decode())

    def _http_post(self, url, data, timeout=10):
        req = urllib.request.Request(
            url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
        )
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())

    def test_health_check(self):
        import dcc_mcp_3dsmax.sidecar.bridge as bridge_module

        bridge_module.stop_bridge()
        b = bridge_module.start_bridge(port=0)
        try:
            d = self._http_get(f"http://127.0.0.1:{b.server_address[1]}/health")
            assert d["status"] == "ok" and d["port"] == b.server_address[1]
        finally:
            bridge_module.stop_bridge()

    def test_dispatch_action(self, tmp_path):
        import dcc_mcp_3dsmax.sidecar.bridge as bridge_module

        bridge_module.stop_bridge()
        (tmp_path / "action.py").write_text("def main(value=42):\n    return {'success': True, 'value': value}\n")
        b = bridge_module.start_bridge(port=0)
        try:
            d = self._http_post(
                f"http://127.0.0.1:{b.server_address[1]}/dispatch",
                {"script_path": str(tmp_path / "action.py"), "args": {"value": 99}, "request_id": "t1"},
            )
            assert d["success"] is True and d["value"] == 99
        finally:
            bridge_module.stop_bridge()

    def test_dispatch_builtin_scene_action(self, monkeypatch):
        import dcc_mcp_3dsmax.sidecar.bridge as bridge_module

        _install_fake_pymxs(monkeypatch)
        bridge_module.stop_bridge()
        scene_file = SKILLS_DIR / "3dsmax-scene" / "action_get_scene_info.py"
        b = bridge_module.start_bridge(port=0)
        try:
            d = self._http_post(
                f"http://127.0.0.1:{b.server_address[1]}/dispatch",
                {"script_path": str(scene_file), "args": {}, "request_id": "t2"},
            )
            assert d["status"] == "success" and "scene_name" in d
        finally:
            bridge_module.stop_bridge()

    def test_404(self):
        import dcc_mcp_3dsmax.sidecar.bridge as bridge_module

        bridge_module.stop_bridge()
        b = bridge_module.start_bridge(port=0)
        try:
            with pytest.raises(urllib.error.HTTPError, match="404"):
                urllib.request.urlopen(f"http://127.0.0.1:{b.server_address[1]}/unknown")
        finally:
            bridge_module.stop_bridge()

    def test_stop_restart(self):
        import dcc_mcp_3dsmax.sidecar.bridge as bridge_module

        bridge_module.stop_bridge()
        p1 = bridge_module.start_bridge(port=0).server_address[1]
        bridge_module.stop_bridge()
        p2 = bridge_module.start_bridge(port=0).server_address[1]
        bridge_module.stop_bridge()
        assert p1 != p2

    def test_sidecar_resolve_bundled_action(self, monkeypatch):
        _install_fake_pymxs(monkeypatch)
        from dcc_mcp_3dsmax.sidecar._dispatcher import _resolve_bundled_script_path, dispatch_payload

        p = _resolve_bundled_script_path("3dsmax-scene__get_scene_info")
        assert p and p.name == "action_get_scene_info.py"
        r = json.loads(dispatch_payload({"script_path": str(p), "args": {}, "request_id": "d1"}))
        assert r["status"] == "success" and "scene_name" in r


# ═══════════════════════════════════════════════════════════════════════════
# 7. Dispatchers
# ═══════════════════════════════════════════════════════════════════════════
class TestDispatchers:
    def test_standalone(self):
        from dcc_mcp_3dsmax.dispatcher import MaxStandaloneDispatcher

        d = MaxStandaloneDispatcher()
        assert d.dispatch_callable(lambda x: x * 2, 21) == 42

    def test_extra_kwargs_passed_to_callable(self):
        """Extra keyword args beyond named params are forwarded to the callable."""
        from dcc_mcp_3dsmax.dispatcher import MaxStandaloneDispatcher

        d = MaxStandaloneDispatcher()
        r = d.dispatch_callable(lambda **kw: kw, extra_data="hello")
        assert r["extra_data"] == "hello"

    def test_exception_wraps_in_runtime_error(self):
        """Exceptions from callable are wrapped as RuntimeError by dispatch_callable."""
        from dcc_mcp_3dsmax.dispatcher import MaxStandaloneDispatcher

        d = MaxStandaloneDispatcher()
        with pytest.raises(RuntimeError, match="test"):
            d.dispatch_callable(lambda: (_ for _ in ()).throw(ValueError("test")))


# ═══════════════════════════════════════════════════════════════════════════
# 8. Bootstrap Modes
# ═══════════════════════════════════════════════════════════════════════════
class TestBootstrapModes:
    def test_default_runtime(self, monkeypatch):
        from dcc_mcp_3dsmax import max_bootstrap

        monkeypatch.delenv("DCC_MCP_3DSMAX_BOOT_MODE", raising=False)
        monkeypatch.setattr(max_bootstrap, "start_sidecar_bridge", lambda: {"mode": "sidecar"})
        assert max_bootstrap.main() == {"mode": "sidecar"}

    def test_sidecar(self, monkeypatch):
        from dcc_mcp_3dsmax import max_bootstrap

        monkeypatch.setenv("DCC_MCP_3DSMAX_BOOT_MODE", "runtime")
        monkeypatch.setattr(max_bootstrap, "start_sidecar_bridge", lambda: {"mode": "sidecar"})
        assert max_bootstrap.main() == {"mode": "sidecar"}

    def test_embedded(self, monkeypatch):
        from dcc_mcp_3dsmax import max_bootstrap

        monkeypatch.setenv("DCC_MCP_3DSMAX_BOOT_MODE", "embedded")
        monkeypatch.setattr(max_bootstrap, "_register_process_cleanup", lambda: None)
        monkeypatch.setattr(max_bootstrap, "_install_max_integration", lambda: None)
        monkeypatch.setattr(max_bootstrap, "start_embedded_sidecar_bridge", lambda: {"mode": "embedded-runtime"})
        assert max_bootstrap.main() == {"mode": "embedded-runtime"}

    def test_server(self, monkeypatch):
        from dcc_mcp_3dsmax import max_bootstrap

        monkeypatch.setenv("DCC_MCP_3DSMAX_BOOT_MODE", "server")
        monkeypatch.setattr(max_bootstrap, "_register_process_cleanup", lambda: None)
        monkeypatch.setattr(max_bootstrap, "_install_max_integration", lambda: None)
        monkeypatch.setattr(max_bootstrap, "start_embedded_server", lambda **kw: {"mode": "server"})
        assert max_bootstrap.main() == {"mode": "server"}

    def test_invalid_raises(self, monkeypatch):
        from dcc_mcp_3dsmax import max_bootstrap

        monkeypatch.setenv("DCC_MCP_3DSMAX_BOOT_MODE", "bad")
        monkeypatch.setattr(max_bootstrap, "_register_process_cleanup", lambda: None)
        monkeypatch.setattr(max_bootstrap, "_install_max_integration", lambda: None)
        with pytest.raises(ValueError, match="unsupported"):
            max_bootstrap.main()


# ═══════════════════════════════════════════════════════════════════════════
# 9. Version & Capabilities
# ═══════════════════════════════════════════════════════════════════════════
class TestVersionAndCapabilities:
    def test_version_label_2024(self, monkeypatch):
        from dcc_mcp_3dsmax import max_bootstrap

        monkeypatch.setitem(
            sys.modules, "pymxs", types.SimpleNamespace(runtime=types.SimpleNamespace(maxVersion=lambda: (26000,)))
        )
        assert max_bootstrap._max_version_label() == "2024"

    def test_capabilities_structure(self):
        import dcc_mcp_3dsmax.capabilities as caps

        cap_list = caps.get_3dsmax_capabilities()
        assert isinstance(cap_list, list) and len(cap_list) > 0
        assert "scene_info" in cap_list
        cap_dict = caps.get_3dsmax_capabilities_dict()
        assert isinstance(cap_dict, dict) and cap_dict["dcc_name"] == "3dsmax"
        assert "capabilities" in cap_dict


# ═══════════════════════════════════════════════════════════════════════════
# 10. Full Lifecycle with Fake Runtime
# ═══════════════════════════════════════════════════════════════════════════
class TestFullMcpLifecycle:
    def test_full_start_register_stop(self, monkeypatch):
        _install_fake_pymxs(monkeypatch)
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions, stop_server

        stop_server()
        s = MaxMcpServer(options=MaxServerOptions(port=0, enable_gateway_failover=False, job_storage_path=""))
        try:
            s.register_builtin_actions(include_bundled=True)
            skills = s.list_skills()
            assert isinstance(skills, list)
            results = s.search_skills(query="scene", dcc="3dsmax")
            assert isinstance(results, list)
            assert s.loaded_skill_count() >= 0
        finally:
            s.stop()
            stop_server()

    def test_every_action_executes(self, monkeypatch):
        """All built-in actions load correctly via the executor without crashing.

        Some actions need specific required params (script, code, offset, node_name).
        Those are expected to return error responses, not crash.
        """
        from dcc_mcp_3dsmax._executor import run_skill_script

        _install_fake_pymxs(monkeypatch)

        # Actions that need specific params to succeed
        parameterized = {
            "3dsmax-scripting/action_execute_maxscript.py": {"script": "print 1"},
            "3dsmax-scripting/action_execute_python.py": {"code": "print(1)"},
            "3dsmax-transform/action_move_nodes.py": {"offset": [10, 0, 0]},
            "3dsmax-transform/action_set_node_position.py": {"node_name": "Box001", "position": [0, 0, 0]},
        }

        executed, crashed = 0, []
        for d in sorted(SKILLS_DIR.iterdir()):
            if not d.is_dir() or not d.name.startswith("3dsmax-"):
                continue
            for sf in sorted(d.glob("action_*.py")):
                rel = f"{d.name}/{sf.name}"
                params = parameterized.get(rel, {})
                try:
                    r = run_skill_script(str(sf), params)
                except Exception as e:
                    crashed.append(f"{rel}: CRASHED: {e}")
                    continue
                executed += 1
                is_dict = isinstance(r, dict)
                if not is_dict:
                    crashed.append(f"{rel}: returned {type(r).__name__} instead of dict")
        assert len(crashed) == 0, f"\n  Crashed actions ({len(crashed)}/{executed}):\n  " + "\n  ".join(crashed)
        assert executed >= 13, f"Expected at least 13 actions, executed {executed}"


# ── final summary when run directly ───────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("  dcc-mcp-3dsmax Automated E2E Debug")
    print("=" * 70)
    print(f"  Python: {sys.version}")
    import dcc_mcp_3dsmax

    print(f"  Package: dcc-mcp-3dsmax {dcc_mcp_3dsmax.__version__}")
    import dcc_mcp_core

    print(f"  Core:    dcc-mcp-core {getattr(dcc_mcp_core, '__version__', '?')}")
    print(f"  Skills dir: {SKILLS_DIR}")
    print()
    print("  Run: pytest tests/test_automated_e2e.py -v")
    print("=" * 70)
