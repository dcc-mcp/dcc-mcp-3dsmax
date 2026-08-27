"""Tests for the MZP package assembler."""

from __future__ import annotations

import importlib.util
import json
import stat
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "packaging" / "templates"


def _load_assembler():
    module_path = Path(__file__).resolve().parents[1] / "packaging" / "assemble_mzp.py"
    spec = importlib.util.spec_from_file_location("assemble_mzp", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_payload_checker():
    module_path = Path(__file__).resolve().parents[1] / "packaging" / "check_release_payload.py"
    spec = importlib.util.spec_from_file_location("check_release_payload", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generated_install_script(tmp_path: Path, version: str = "1.2.3") -> str:
    assembler = _load_assembler()
    assembler.write_install_script(tmp_path, version)
    return (tmp_path / "install.ms").read_text(encoding="utf-8")


def test_mzp_scripts_are_maintained_as_templates():
    """Long MZP scripts live as source files instead of inline assembler strings."""
    startup = TEMPLATES_DIR / "dcc_mcp_3dsmax_startup.ms"
    install = TEMPLATES_DIR / "install.ms"

    assert startup.is_file()
    assert install.is_file()
    assert "dcc_mcp_3dsmax.main()" in startup.read_text(encoding="utf-8")
    assert "{{VERSION}}" in install.read_text(encoding="utf-8")


def test_mzp_run_is_control_file(tmp_path):
    """mzp.run must contain MZP commands, not the installer MaxScript body."""
    assembler = _load_assembler()

    assembler.write_mzp_run(tmp_path, "1.2.3")

    text = (tmp_path / "mzp.run").read_text(encoding="utf-8")
    assert 'name "dcc-mcp-3dsmax"' in text
    assert 'description "dcc-mcp-3dsmax 1.2.3 drag-and-drop installer"' in text
    assert 'run "install.ms"' in text
    assert 'drop "install.ms"' in text
    assert "clear temp on MAX exit" in text
    assert "python.Execute" not in text
    assert "messageBox" not in text


def test_install_script_normalizes_paths_before_embedding_in_python(tmp_path):
    """Generated MaxScript avoids raw Python strings ending in backslashes."""
    text = _generated_install_script(tmp_path)
    assert "local sourceRoot = _dccMcpNormalizePath (getFilenamePath (getSourceFileName()))" in text
    assert "local userScripts = _dccMcpNormalizePath (getDir #userScripts)" in text
    assert "local userStartupScripts = _dccMcpNormalizePath (getDir #userStartupScripts)" in text
    assert "source = Path(r'''" in text
    assert "versions_dir = install_dir / 'versions'" in text
    assert "current_file = install_dir / 'current.txt'" in text
    assert "version_name = '1.2.3'" in text
    assert "{{VERSION}}" not in text
    assert "dcc_mcp_3dsmax.install_menu()" in text
    assert "dcc_mcp_3dsmax.install_shutdown_callback()" in text
    assert "dcc_mcp_3dsmax.main()" in text
    assert "capture_bootstrap_errors" in text
    assert "with capture_bootstrap_errors(" in text
    assert "def _cleanup_obsolete_payloads(active_key):" in text
    assert "_cleanup_obsolete_payloads(key)" in text
    assert 'button installBtn "Install"' in text
    assert 'button uninstallBtn "Uninstall"' in text
    assert "dcc_mcp_3dsmax.stop_sidecar_bridge()" in text
    assert "from dcc_mcp_core.install_lifecycle import safe_remove_tree" in text
    assert "sys.modules.pop(name, None)" in text
    assert "installed and runtime startup requested" in text
    assert "dcc-mcp-3dsmax install failed:" in text
    assert "Failed to stop dcc-mcp-3dsmax sidecar before uninstall" in text
    assert "uninstall requires a 3ds Max restart" in text
    assert "dcc-mcp-3dsmax uninstall failed:" in text
    assert "startup_script.unlink()" in text
    assert "uninstall_marker = Path(r'''" in text
    assert ") / 'dcc_mcp_3dsmax_uninstall_pending'" in text


def test_uninstall_script_escapes_pending_marker_newline_for_nested_python(tmp_path):
    """Uninstall marker Python must survive MaxScript string unescaping."""
    text = _generated_install_script(tmp_path)

    assert "uninstall_marker.write_text('pending\\\\n', encoding='utf-8')" in text
    assert "uninstall_marker.write_text('pending\\n', encoding='utf-8')" not in text


def test_startup_script_installs_menu_after_adding_package_path(tmp_path):
    """Restarting 3ds Max after MZP install should restore the DCC MCP menu."""
    assembler = _load_assembler()

    assembler.write_startup_template(tmp_path)

    text = (tmp_path / "startup" / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8")
    assert text == (TEMPLATES_DIR / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8")
    assert "local installRoot = _dccMcpNormalizePath" in text
    assert "DCC_MCP_3DSMAX_BOOTSTRAP_PATHS" in text
    assert "DCC_MCP_3DSMAX_ROOT" in text
    assert "DCC_MCP_CORE_ROOT" in text
    assert "DCC_MCP_SERVER_ROOT" in text
    assert "current = current_file.read_text" in text
    assert "root / 'versions' / current" in text
    assert "def _is_python37_root(path):" in text
    assert "base.parent / 'python'" in text
    assert "def _compatible_python_path(path):" in text
    assert "sys.path.insert(0, str(pkg))" in text
    assert "dcc_mcp_3dsmax.install_menu()" in text
    assert "dcc_mcp_3dsmax.install_shutdown_callback()" in text
    assert "dcc_mcp_3dsmax.main()" in text
    assert "capture_bootstrap_errors" in text
    assert "phase='mzp_startup'" in text
    assert "DCC_MCP_3DSMAX_PORT" not in text
    assert "DCC_MCP_GATEWAY_PORT" not in text
    assert "def _cleanup_obsolete_payloads(active_root):" in text
    assert "_cleanup_obsolete_payloads(install_payload)" in text
    assert "from dcc_mcp_core.install_lifecycle import safe_remove_tree" in text


def test_release_payload_checker_uses_modern_python_root_on_py38_plus(tmp_path, monkeypatch):
    """Release smoke must not import cp37 native payloads on modern 3ds Max Python."""
    checker = _load_payload_checker()
    payload = tmp_path / "payload"
    (payload / "python").mkdir(parents=True)
    (payload / "python37").mkdir()

    monkeypatch.setattr(checker.sys, "version_info", (3, 10, 14))

    assert checker.python_root(payload) == payload / "python"


def test_extract_wheel_maps_data_scripts_next_to_packages(tmp_path):
    """Wheel .data/scripts entries must land where dcc_mcp_server can find them."""
    assembler = _load_assembler()
    wheel = tmp_path / "dcc_mcp_server-0.17.56-py3-none-win_amd64.whl"
    dest = tmp_path / "python"

    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("dcc_mcp_server/__init__.py", "__version__ = '0.17.56'\n")
        zf.writestr("dcc_mcp_server-0.17.56.data/scripts/dcc-mcp-server.exe", b"binary")
        zf.writestr("dcc_mcp_server-0.17.56.dist-info/METADATA", "Name: dcc-mcp-server\n")

    assembler.extract_wheel(wheel, dest)

    assert (dest / "dcc_mcp_server" / "__init__.py").is_file()
    assert (dest / "scripts" / "dcc-mcp-server.exe").read_bytes() == b"binary"
    assert not (dest / "dcc_mcp_server-0.17.56.data").exists()
    assert not (dest / "dcc_mcp_server-0.17.56.dist-info").exists()


def _runtime_lock():
    return json.loads((ROOT / "packaging" / "mzp-runtime.lock.json").read_text(encoding="utf-8"))


def _write_wheel(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _runtime_wheel_files():
    return {
        "typing_extensions.py": "value = 1\n",
        "typing_extensions-4.7.1.dist-info/LICENSE": "PSF LICENSE\n",
        "typing_extensions-4.7.1.dist-info/METADATA": "Name: typing-extensions\nVersion: 4.7.1\n",
        "typing_extensions-4.7.1.dist-info/RECORD": "",
        "typing_extensions-4.7.1.dist-info/WHEEL": "Wheel-Version: 1.0\n",
    }


def _locked_dependency(assembler, wheel):
    dependency = _runtime_lock()["distributions"][0]
    dependency["artifact"].update(
        filename=wheel.name,
        sha256=assembler.sha256_file(wheel),
        size=wheel.stat().st_size,
    )
    return dependency


def test_runtime_lock_pins_authenticated_dependency_for_both_payload_lanes():
    assembler = _load_assembler()
    dependency = assembler.load_runtime_dependency(ROOT)

    assert dependency["version"] == "4.7.1"
    assert dependency["license"] == "PSF-2.0"
    assert dependency["payloads"] == ["python", "python37"]
    assert dependency["files"] == assembler.REQUIRED_RUNTIME_FILES
    assert len(dependency["artifact"]["sha256"]) == 64


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("distributions", [], "typing-extensions"),
        ("name", "typing-extensions-renamed", "typing-extensions"),
        ("sha256", "0" * 64, "sha256"),
    ],
)
def test_runtime_lock_rejects_missing_renamed_or_drifted_dependency(tmp_path, field, value, message):
    assembler = _load_assembler()
    data = _runtime_lock()
    if field == "distributions":
        data[field] = value
    elif field == "sha256":
        data["distributions"][0]["artifact"][field] = value
    else:
        data["distributions"][0][field] = value
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    (packaging / "mzp-runtime.lock.json").write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        assembler.load_runtime_dependency(tmp_path)


def test_locked_wheel_preserves_license_and_rejects_hash_or_file_drift(tmp_path):
    assembler = _load_assembler()
    wheel = tmp_path / "typing_extensions-4.7.1-py3-none-any.whl"
    files = _runtime_wheel_files()
    _write_wheel(wheel, files)
    dependency = _locked_dependency(assembler, wheel)

    assembler.extract_locked_wheel(wheel, tmp_path / "payload", dependency)
    assert (tmp_path / "payload" / "typing_extensions-4.7.1.dist-info" / "LICENSE").is_file()

    dependency["artifact"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="sha256"):
        assembler.extract_locked_wheel(wheel, tmp_path / "hash-drift", dependency)

    files["unexpected.py"] = "drift\n"
    _write_wheel(wheel, files)
    dependency = _locked_dependency(assembler, wheel)
    with pytest.raises(RuntimeError, match="file list"):
        assembler.extract_locked_wheel(wheel, tmp_path / "file-drift", dependency)


def test_locked_wheel_rejects_unsafe_paths_links_and_wrong_identity(tmp_path):
    assembler = _load_assembler()
    wheel = tmp_path / "typing_extensions-4.7.1-py3-none-any.whl"
    files = _runtime_wheel_files()
    files["../outside.py"] = "bad\n"
    _write_wheel(wheel, files)
    with pytest.raises(RuntimeError, match="unsafe"):
        assembler.extract_locked_wheel(wheel, tmp_path / "unsafe", _locked_dependency(assembler, wheel))
    assert not (tmp_path / "outside.py").exists()

    link = zipfile.ZipInfo("typing_extensions.py")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(link, "outside.py")
        for name, content in _runtime_wheel_files().items():
            if name != "typing_extensions.py":
                zf.writestr(name, content)
    with pytest.raises(RuntimeError, match="link"):
        assembler.extract_locked_wheel(wheel, tmp_path / "link", _locked_dependency(assembler, wheel))

    files = _runtime_wheel_files()
    files["typing_extensions-4.7.1.dist-info/METADATA"] = "Name: other\nVersion: 4.7.1\n"
    _write_wheel(wheel, files)
    with pytest.raises(RuntimeError, match="identity"):
        assembler.extract_locked_wheel(wheel, tmp_path / "identity", _locked_dependency(assembler, wheel))


def _metadata_wheel(path, name, requirements=(), version="0.20.21"):
    metadata = f"Metadata-Version: 2.1\nName: {name}\n"
    if version is not None:
        metadata += f"Version: {version}\n"
    metadata += "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
    dist_info = name.replace("-", "_") + "-0.20.21.dist-info/METADATA"
    _write_wheel(path, {dist_info: metadata})


def test_python37_core_server_dependency_closure_must_match_lock(tmp_path):
    assembler = _load_assembler()
    core = tmp_path / "dcc_mcp_core-0.20.21-cp37-cp37m-win_amd64.whl"
    server = tmp_path / "dcc_mcp_server-0.20.21-py3-none-any.whl"
    requirements = (
        "dcc-mcp-server>=0.18.17,<1.0.0",
        "typing-extensions==4.7.1 ; python_full_version < '3.8'",
    )
    _metadata_wheel(core, "dcc-mcp-core", requirements)
    _metadata_wheel(server, "dcc-mcp-server")
    dependency = _runtime_lock()["distributions"][0]

    assembler.verify_python37_runtime_contract([core], server, dependency)
    dependency["version"] = "4.7.2"
    with pytest.raises(RuntimeError, match="Core requires.*4.7.1.*lock.*4.7.2"):
        assembler.verify_python37_runtime_contract([core], server, dependency)

    dependency["version"] = "4.7.1"
    _metadata_wheel(server, "dcc-mcp-server", ("another-runtime-dependency>=1",))
    with pytest.raises(RuntimeError, match="unclosed dcc-mcp-server"):
        assembler.verify_python37_runtime_contract([core], server, dependency)


def test_python37_runtime_rejects_server_wheel_outside_core_specifier(tmp_path):
    assembler = _load_assembler()
    core = tmp_path / "dcc_mcp_core-0.20.21-cp37-cp37m-win_amd64.whl"
    server = tmp_path / "dcc_mcp_server-1.0.0-py3-none-any.whl"
    requirements = (
        "dcc-mcp-server>=0.18.17,<1.0.0",
        "typing-extensions==4.7.1 ; python_full_version < '3.8'",
    )
    _metadata_wheel(core, "dcc-mcp-core", requirements)
    _metadata_wheel(server, "dcc-mcp-server", version="1.0.0")

    with pytest.raises(RuntimeError, match=r"server wheel version 1\.0\.0 does not satisfy.*<1\.0\.0"):
        assembler.verify_python37_runtime_contract(
            [core], server, _runtime_lock()["distributions"][0]
        )


def test_python37_runtime_accepts_pep_equivalent_server_requirement(tmp_path):
    assembler = _load_assembler()
    core = tmp_path / "dcc_mcp_core-0.20.21-cp37-cp37m-win_amd64.whl"
    server = tmp_path / "dcc_mcp_server-0.20.21-py3-none-any.whl"
    requirements = (
        "dcc_mcp_server ( < 1.0.0 , >= 0.18.17 )",
        "typing-extensions==4.7.1 ; python_full_version < '3.8'",
    )
    _metadata_wheel(core, "dcc-mcp-core", requirements)
    _metadata_wheel(server, "dcc-mcp-server")

    assembler.verify_python37_runtime_contract(
        [core], server, _runtime_lock()["distributions"][0]
    )


@pytest.mark.parametrize(
    "server_requirement",
    [
        "dcc-mcp-server==0.20.21",
        "dcc-mcp-server>=0.18.17,==0.20.*",
        "dcc-mcp-server~=0.20",
        "dcc-mcp-server>=0.18.17,!=0.19.0,<1",
    ],
)
def test_python37_runtime_accepts_supported_pep_specifier_forms(tmp_path, server_requirement):
    assembler = _load_assembler()
    core = tmp_path / "dcc_mcp_core-0.20.21-cp37-cp37m-win_amd64.whl"
    server = tmp_path / "dcc_mcp_server-0.20.21-py3-none-any.whl"
    requirements = (
        server_requirement,
        "typing-extensions==4.7.1 ; python_full_version < '3.8'",
    )
    _metadata_wheel(core, "dcc-mcp-core", requirements)
    _metadata_wheel(server, "dcc-mcp-server")

    assembler.verify_python37_runtime_contract(
        [core], server, _runtime_lock()["distributions"][0]
    )


@pytest.mark.parametrize(
    "server_requirement",
    [
        "dcc-mcp-server",
        "dcc-mcp-server=>0.18.17",
        "dcc-mcp-server<1.*",
    ],
)
def test_python37_runtime_rejects_missing_or_invalid_server_specifier(tmp_path, server_requirement):
    assembler = _load_assembler()
    core = tmp_path / "dcc_mcp_core-0.20.21-cp37-cp37m-win_amd64.whl"
    server = tmp_path / "dcc_mcp_server-0.20.21-py3-none-any.whl"
    requirements = (
        server_requirement,
        "typing-extensions==4.7.1 ; python_full_version < '3.8'",
    )
    _metadata_wheel(core, "dcc-mcp-core", requirements)
    _metadata_wheel(server, "dcc-mcp-server")

    with pytest.raises(RuntimeError, match="dcc-mcp-server specifier"):
        assembler.verify_python37_runtime_contract(
            [core], server, _runtime_lock()["distributions"][0]
        )


@pytest.mark.parametrize("server_version", [None, "not-a-version"])
def test_python37_runtime_rejects_missing_or_invalid_server_wheel_version(tmp_path, server_version):
    assembler = _load_assembler()
    core = tmp_path / "dcc_mcp_core-0.20.21-cp37-cp37m-win_amd64.whl"
    server = tmp_path / "dcc_mcp_server-0.20.21-py3-none-any.whl"
    requirements = (
        "dcc-mcp-server>=0.18.17,<1.0.0",
        "typing-extensions==4.7.1 ; python_full_version < '3.8'",
    )
    _metadata_wheel(core, "dcc-mcp-core", requirements)
    _metadata_wheel(server, "dcc-mcp-server", version=server_version)

    with pytest.raises(RuntimeError, match="server wheel.*missing or invalid version"):
        assembler.verify_python37_runtime_contract(
            [core], server, _runtime_lock()["distributions"][0]
        )


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_workflow_cannot_upload_mzp_before_real_python37_registration(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    build_mzp = text.index("build-mzp:")
    upload = text.index("Upload MZP artifact", build_mzp)
    modern = text.index("Smoke-check MZP payload imports", build_mzp)
    python37_setup = text.index('python-version: "3.7"', modern)
    python37_check = text.index("Smoke-check MZP payload with Python 3.7", python37_setup)

    assert modern < python37_setup < python37_check < upload
