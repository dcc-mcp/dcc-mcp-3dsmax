"""Install SOP v1 contract tests for the public 3ds Max lifecycle CLI."""

from __future__ import annotations

import importlib
import json
from pathlib import Path


def _install_cli():
    return importlib.import_module("dcc_mcp_3dsmax.install_cli")


def _layout(tmp_path: Path):
    host_root = tmp_path / "Autodesk" / "3ds Max 2025"
    host_root.mkdir(parents=True)
    (host_root / "3dsmax.exe").write_bytes(b"")
    target_python = host_root / "Python" / "python.exe"
    target_python.parent.mkdir()
    target_python.write_bytes(b"")
    return {
        "host": host_root,
        "python": target_python,
        "startup": tmp_path / "startup",
        "receipt": tmp_path / "receipt.json",
    }


def _args(layout, verb, *extra):
    return [
        verb,
        "--json",
        "--dcc-path",
        str(layout["host"]),
        "--python",
        str(layout["python"]),
        "--startup-dir",
        str(layout["startup"]),
        "--receipt-path",
        str(layout["receipt"]),
        *extra,
    ]


def _report(cli, capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    return cli.loads_public_report(captured.out)


def _stub_target(monkeypatch, cli):
    monkeypatch.setattr(
        cli,
        "_probe_target",
        lambda _python: {
            "python_version": "3.11.9",
            "adapter_version": cli.ADAPTER_VERSION,
            "core_version": "0.20.20",
        },
    )
    monkeypatch.setattr(cli, "_install_package", lambda _ctx, _source: None)
    monkeypatch.setattr(cli, "_uninstall_package", lambda _ctx: None)


def test_install_cli_exposes_the_standard_lifecycle_verbs() -> None:
    cli = _install_cli()

    parser = cli.build_parser()
    verbs = set(parser._subparsers._group_actions[0].choices)

    assert verbs == {"install", "status", "verify", "uninstall", "upgrade"}


def test_json_status_is_one_schema_valid_report(tmp_path, capsys) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)

    exit_code = cli.main(_args(layout, "status"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["schema_version"] == 1
    assert report["dcc_type"] == "3dsmax"
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "preflight",
        "failure_reason": "not_installed",
    }
    assert report["receipt_path"] is None
    assert report["steps"]
    assert report["next_steps"][0]["command"][1] == "install"
    cli.validate_public_report(report)


def test_dry_run_does_not_write_a_hook_receipt_or_install_package(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    package_calls = []
    monkeypatch.setattr(cli, "_install_package", lambda *_args: package_calls.append(_args))

    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code == 0
    assert report["status"] == "planned"
    assert report["verify"]["directly_usable"] is False
    assert package_calls == []
    assert not layout["startup"].exists()
    assert not layout["receipt"].exists()


def test_install_receipt_verify_and_uninstall_round_trip(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _stub_target(monkeypatch, cli)
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": False, "status": "missing"})

    install_exit = cli.main(_args(layout, "install", "--yes"))
    installed = _report(cli, capsys)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME

    assert install_exit == 50
    assert installed["status"] == "requires_restart"
    assert installed["receipt_path"] == str(layout["receipt"].resolve())
    assert hook.is_file()
    receipt = json.loads(layout["receipt"].read_text(encoding="utf-8"))
    assert receipt["receipt_version"] == 1
    assert receipt["dcc_type"] == "3dsmax"
    assert receipt["host"]["path"] == str(layout["host"].resolve())
    assert receipt["python"]["path"] == str(layout["python"].resolve())
    assert receipt["python"]["version"] == "3.11.9"
    assert receipt["core_version"] == "0.20.20"
    assert receipt["artifacts"][0]["sha256"] == cli._sha256(hook)

    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": True, "status": "ready"})
    verify_exit = cli.main(_args(layout, "verify"))
    verified = _report(cli, capsys)
    assert verify_exit == 0
    assert verified["status"] == "ok"
    assert verified["verify"]["directly_usable"] is True

    uninstall_exit = cli.main(_args(layout, "uninstall", "--yes"))
    uninstalled = _report(cli, capsys)
    assert uninstall_exit == 0
    assert uninstalled["status"] == "ok"
    assert not hook.exists()
    assert not layout["receipt"].exists()

    assert cli.main(_args(layout, "uninstall", "--yes")) == 0
    assert _report(cli, capsys)["status"] == "ok"


def test_upgrade_rolls_back_hook_and_receipt_when_commit_fails(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _stub_target(monkeypatch, cli)
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": False, "status": "missing"})
    assert cli.main(_args(layout, "install", "--yes")) == 50
    _report(cli, capsys)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    old_hook = hook.read_bytes()
    old_receipt = layout["receipt"].read_bytes()

    real_replace = cli._replace_file
    calls = []

    def fail_receipt(source, destination):
        calls.append(Path(destination))
        if Path(destination) == layout["receipt"].resolve():
            raise OSError("injected receipt commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(cli, "_replace_file", fail_receipt)
    assert cli.main(_args(layout, "upgrade", "--yes")) == 30
    failed = _report(cli, capsys)

    assert failed["status"] == "failed"
    assert failed["verify"]["failure_stage"] == "install"
    assert hook.read_bytes() == old_hook
    assert layout["receipt"].read_bytes() == old_receipt
    assert calls


def test_windows_lock_defers_without_overwriting_previous_state(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _stub_target(monkeypatch, cli)
    layout["startup"].mkdir()
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook.write_text("previous hook", encoding="utf-8")

    def locked(_source, _destination):
        error = PermissionError("file in use")
        error.winerror = 32
        raise error

    monkeypatch.setattr(cli, "_replace_file", locked)
    assert cli.main(_args(layout, "install", "--yes")) == 50
    report = _report(cli, capsys)

    assert report["status"] == "requires_restart"
    assert report["verify"]["failure_reason"] == "windows_file_lock"
    assert report["next_steps"][0]["id"] == "restart_3dsmax"
    assert hook.read_text(encoding="utf-8") == "previous hook"
    assert not layout["receipt"].exists()


def test_uninstall_refuses_an_unreceipted_owned_hook(tmp_path, capsys) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    layout["startup"].mkdir()
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook.write_text(cli.render_startup_script(tmp_path / "bootstrap-errors"), encoding="utf-8")

    assert cli.main(_args(layout, "uninstall", "--yes")) == 10
    report = _report(cli, capsys)
    assert report["status"] == "failed"
    assert report["verify"]["failure_reason"] == "receipt_missing"
    assert hook.exists()


def test_startup_hook_captures_bootstrap_failures_and_reraises() -> None:
    cli = _install_cli()

    text = cli.render_startup_script(Path("C:/safe/bootstrap-errors"))

    assert "capture_bootstrap_errors" in text
    assert "with capture_bootstrap_errors(" in text
    assert "dcc_mcp_3dsmax.main()" in text
    assert "except" not in text
