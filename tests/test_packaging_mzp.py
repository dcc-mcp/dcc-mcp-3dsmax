"""Tests for the MZP package assembler."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "packaging" / "templates"


def _load_assembler():
    module_path = Path(__file__).resolve().parents[1] / "packaging" / "assemble_mzp.py"
    spec = importlib.util.spec_from_file_location("assemble_mzp", module_path)
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
    assert "def _cleanup_obsolete_payloads(active_key):" in text
    assert "_cleanup_obsolete_payloads(key)" in text
    assert "button installBtn \"Install\"" in text
    assert "button uninstallBtn \"Uninstall\"" in text
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
    assert "sys.path.insert(0, str(pkg))" in text
    assert "dcc_mcp_3dsmax.install_menu()" in text
    assert "dcc_mcp_3dsmax.install_shutdown_callback()" in text
    assert "dcc_mcp_3dsmax.main()" in text
    assert "DCC_MCP_3DSMAX_PORT" not in text
    assert "DCC_MCP_GATEWAY_PORT" not in text
    assert "def _cleanup_obsolete_payloads(active_root):" in text
    assert "_cleanup_obsolete_payloads(install_payload)" in text
    assert "from dcc_mcp_core.install_lifecycle import safe_remove_tree" in text


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
