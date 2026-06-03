"""Tests for the agent-facing 3ds Max setup skill script."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "dcc-mcp-3dsmax-setup"
    / "scripts"
    / "setup_dcc_mcp_3dsmax.py"
)


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("setup_dcc_mcp_3dsmax", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_startup_script_starts_runtime_from_checkout(tmp_path):
    setup = _load_setup_module()
    repo = tmp_path / "repo"
    repo.mkdir()

    text = setup.build_startup_script(source="local", repo_root=repo)

    assert "sys.path.insert(0, path)" in text
    assert "DCC_MCP_3DSMAX_ROOT" in text
    assert "dcc_mcp_3dsmax.install_menu()" in text
    assert "dcc_mcp_3dsmax.install_shutdown_callback()" in text
    assert "dcc_mcp_3dsmax.main()" in text
    assert "DCC_MCP_GATEWAY_PORT" not in text


def test_write_startup_hook_installs_and_generates_copy(tmp_path):
    setup = _load_setup_module()
    startup_dir = tmp_path / "startup"
    out_dir = tmp_path / "out"

    installed = setup.write_startup_hook(
        startup_dir=startup_dir,
        out_dir=out_dir,
        source="pypi",
        repo_root=tmp_path,
        skip_startup_hook=False,
    )

    generated = out_dir / setup.STARTUP_SCRIPT_NAME
    assert installed == startup_dir / setup.STARTUP_SCRIPT_NAME
    assert generated.is_file()
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == generated.read_text(encoding="utf-8")


def test_write_startup_hook_can_generate_without_installing(tmp_path):
    setup = _load_setup_module()

    installed = setup.write_startup_hook(
        startup_dir=tmp_path / "startup",
        out_dir=tmp_path / "out",
        source="pypi",
        repo_root=tmp_path,
        skip_startup_hook=True,
    )

    assert installed is None
    assert (tmp_path / "out" / setup.STARTUP_SCRIPT_NAME).is_file()
    assert not (tmp_path / "startup").exists()


def test_fallback_startup_dir_uses_max_year_and_local_appdata(tmp_path, monkeypatch):
    setup = _load_setup_module()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    maxpy = Path("C:/Program Files/Autodesk/3ds Max 2025/3dsmaxpy.exe")

    assert setup.fallback_startup_dir(maxpy) == (
        tmp_path / "local" / "Autodesk" / "3dsMax" / "2025 - 64bit" / "ENU" / "scripts" / "startup"
    )


def test_query_startup_dir_uses_last_nonempty_output_line(monkeypatch):
    setup = _load_setup_module()

    monkeypatch.setattr(setup, "capture", lambda _cmd: "startup noise\n\nC:/Users/me/3dsmax/startup\n")

    assert setup.query_3dsmax_startup_dir(Path("3dsmaxpy.exe")) == Path("C:/Users/me/3dsmax/startup")
