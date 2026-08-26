"""Install SOP v1 contract tests for the public 3ds Max lifecycle CLI."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


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
        "_probe_compatibility",
        lambda _ctx: {"python_version": "3.11.9", "core_version": "0.20.20", "host_version": "2025"},
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "_probe_target",
        lambda _python: {
            "python_version": "3.11.9",
            "adapter_version": cli.ADAPTER_VERSION,
            "core_version": "0.20.20",
        },
    )
    monkeypatch.setattr(cli, "_install_package", lambda _ctx, _source: True)
    monkeypatch.setattr(cli, "_uninstall_package", lambda _ctx: None)
    empty_package_state = cli._package_state_from_lines([])
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: empty_package_state)


def _install_for_verify(cli, layout, capsys, monkeypatch) -> None:
    _stub_target(monkeypatch, cli)
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": False, "status": "missing"})
    assert cli.main(_args(layout, "install", "--yes")) == 50
    _report(cli, capsys)


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


@pytest.mark.parametrize(
    ("readiness", "expected_reason"),
    [
        ({"success": False, "status": "x:PRIVATE_URI_VALUE_71aa/C:/private/token"}, "invalid_readiness_status"),
        ({"success": True, "status": "unexpected_ready"}, "invalid_readiness_status"),
    ],
)
def test_verify_rejects_hostile_or_unknown_readiness_status(
    tmp_path, capsys, monkeypatch, readiness, expected_reason
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: readiness)

    exit_code = cli.main(_args(layout, "verify"))
    captured = capsys.readouterr()

    assert exit_code == 40
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_URI_VALUE_71aa" not in captured.out
    assert "C:/private/token" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "readiness",
        "failure_reason": expected_reason,
    }


def test_verify_maps_readiness_exception_to_one_safe_report(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)

    def fail_readiness(_timeout):
        raise RuntimeError("PRIVATE_SECRET at x:private-uri/C:/private/path")

    monkeypatch.setattr(cli, "_wait_readiness", fail_readiness)
    exit_code = cli.main(_args(layout, "verify"))
    captured = capsys.readouterr()

    assert exit_code == 40
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_SECRET" not in captured.out
    assert "private-uri" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"]["failure_stage"] == "readiness"
    assert report["verify"]["failure_reason"] == "readiness_probe_failed"


def test_verify_rejects_hostile_readiness_status_object(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)

    class HostileStatus:
        def __eq__(self, _other):
            raise RuntimeError("PRIVATE_COMPARE_SECRET C:/private/path")

        def __str__(self):
            raise RuntimeError("PRIVATE_STRING_SECRET x:private-uri")

    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": True, "status": HostileStatus()})
    exit_code = cli.main(_args(layout, "verify"))
    captured = capsys.readouterr()

    assert exit_code == 40
    assert captured.err == ""
    assert "PRIVATE_" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"]["failure_reason"] == "invalid_readiness_status"


def test_verify_maps_bootstrap_log_exception_to_one_safe_report(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    receipt = json.loads(layout["receipt"].read_text(encoding="utf-8"))
    error_dir = Path(receipt["bootstrap_error_dir"])
    error_dir.mkdir(parents=True)
    real_glob = Path.glob

    def fail_error_log_scan(path, pattern):
        if path == error_dir:
            raise OSError("PRIVATE_SECRET at x:private-uri/C:/private/path")
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", fail_error_log_scan)
    exit_code = cli.main(_args(layout, "verify"))
    captured = capsys.readouterr()

    assert exit_code == 40
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_SECRET" not in captured.out
    assert "private-uri" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "startup",
        "failure_reason": "bootstrap_log_unavailable",
    }


@pytest.mark.parametrize(
    ("verb", "expected_exit", "expected_stage", "expected_reason"),
    [
        ("install", 30, "install", "operation_failed"),
        ("upgrade", 30, "install", "operation_failed"),
        ("uninstall", 30, "uninstall", "operation_failed"),
        ("status", 10, "preflight", "status_failed"),
        ("verify", 40, "verify", "verification_failed"),
    ],
)
def test_every_public_verb_maps_ordinary_operational_exceptions_to_one_safe_report(
    tmp_path, capsys, monkeypatch, verb, expected_exit, expected_stage, expected_reason
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)

    def fail_context(_args):
        raise RuntimeError("PRIVATE_SECRET at x:private-uri/C:/private/path")

    monkeypatch.setattr(cli, "_context", fail_context)
    exit_code = cli.main(_args(layout, verb))
    captured = capsys.readouterr()

    assert exit_code == expected_exit
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_SECRET" not in captured.out
    assert "private-uri" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": expected_stage,
        "failure_reason": expected_reason,
    }


def test_public_cli_does_not_capture_base_exceptions(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    monkeypatch.setattr(cli, "_context", lambda _args: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        cli.main(_args(layout, "status"))


@pytest.mark.parametrize(
    ("compatibility", "expected_reason"),
    [
        ({"python_version": "3.6.15", "core_version": "0.20.20", "host_version": "2025"}, "python_version_unsupported"),
        ({"python_version": "3.11.9", "core_version": "0.20.19", "host_version": "2025"}, "core_version_too_old"),
        ({"python_version": "3.11.9", "core_version": "1.0.0", "host_version": "2025"}, "core_version_unsupported"),
        ({"python_version": "3.11.9", "core_version": "0.20.20", "host_version": "2016"}, "host_version_unsupported"),
    ],
)
def test_install_rejects_incompatible_target_before_any_mutation(
    tmp_path, capsys, monkeypatch, compatibility, expected_reason
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    events = []
    monkeypatch.setattr(
        cli,
        "_probe_compatibility",
        lambda _ctx: events.append("compatibility") or compatibility,
        raising=False,
    )
    monkeypatch.setattr(cli, "_install_package", lambda *_args: events.append("pip"))
    monkeypatch.setattr(cli, "_replace_file", lambda *_args: events.append("file"))

    exit_code = cli.main(_args(layout, "install", "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "preflight",
        "failure_reason": expected_reason,
    }
    assert events == ["compatibility"]
    assert not layout["startup"].exists()
    assert not layout["receipt"].exists()


def test_uninstall_rejects_incompatible_target_before_package_or_file_mutation(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook_before = hook.read_bytes()
    receipt_before = layout["receipt"].read_bytes()
    events = []
    monkeypatch.setattr(
        cli,
        "_probe_compatibility",
        lambda _ctx: (
            events.append("compatibility")
            or {"python_version": "3.6.15", "core_version": "0.20.20", "host_version": "2025"}
        ),
    )
    monkeypatch.setattr(cli, "_uninstall_package", lambda _ctx: events.append("pip"))
    monkeypatch.setattr(cli, "_restore", lambda *_args: events.append("file"))

    exit_code = cli.main(_args(layout, "uninstall", "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["verify"]["failure_reason"] == "python_version_unsupported"
    assert events == ["compatibility"]
    assert hook.read_bytes() == hook_before
    assert layout["receipt"].read_bytes() == receipt_before


def test_package_rollback_restores_same_version_provenance_and_dependency_state(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "upgrade", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines(
        [
            "dcc-mcp-3dsmax @ file:///trusted/dcc_mcp_3dsmax-0.2.2.whl",
            "dcc-mcp-core==0.20.20",
        ]
    )
    mutated = cli._package_state_from_lines(
        [
            "dcc-mcp-3dsmax @ https://example.invalid/dcc_mcp_3dsmax-0.2.2.whl",
            "dcc-mcp-core==0.20.21",
            "new-dependency==1.0",
        ]
    )
    snapshots = iter([mutated, prior])
    commands = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: next(snapshots))
    monkeypatch.setattr(cli, "_run_pip_command", lambda _ctx, command: commands.append(command))

    cli._restore_package_state(ctx, prior)

    assert commands == [
        ["uninstall", "-y", "new-dependency"],
        [
            "install",
            "--no-deps",
            "--force-reinstall",
            "dcc-mcp-3dsmax @ file:///trusted/dcc_mcp_3dsmax-0.2.2.whl",
            "dcc-mcp-core==0.20.20",
        ],
    ]


def test_package_rollback_fails_closed_when_restore_subprocess_fails(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "upgrade", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    mutated = cli._package_state_from_lines(["dcc-mcp-3dsmax @ https://example.invalid/other.whl"])
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: mutated)

    def fail_restore(_ctx, _command):
        raise OSError("PRIVATE_ROLLBACK_SECRET C:/private/wheel.whl")

    monkeypatch.setattr(cli, "_run_pip_command", fail_restore)

    with pytest.raises(cli.LifecycleError) as captured:
        cli._restore_package_state(ctx, prior)

    assert captured.value.stage == "rollback"
    assert captured.value.reason == "package_rollback_incomplete"
    assert "PRIVATE_ROLLBACK_SECRET" not in captured.value.reason


@pytest.mark.parametrize("verb", ["install", "upgrade"])
def test_install_and_upgrade_report_incomplete_package_rollback(tmp_path, capsys, monkeypatch, verb) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    if verb == "upgrade":
        _install_for_verify(cli, layout, capsys, monkeypatch)
    else:
        _stub_target(monkeypatch, cli)

    def fail_commit(_source, _destination):
        raise OSError("commit failed")

    def fail_rollback(_ctx, _prior):
        raise cli.LifecycleError(30, "rollback", "package_rollback_incomplete")

    monkeypatch.setattr(cli, "_replace_file", fail_commit)
    monkeypatch.setattr(cli, "_restore_package_state", fail_rollback)

    exit_code = cli.main(_args(layout, verb, "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 30
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "rollback",
        "failure_reason": "package_rollback_incomplete",
    }


def test_uninstall_reports_incomplete_package_rollback(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    real_unlink = Path.unlink

    def fail_receipt_unlink(path, *args, **kwargs):
        if path == layout["receipt"]:
            raise OSError("commit failed")
        return real_unlink(path, *args, **kwargs)

    def fail_rollback(_ctx, _prior):
        raise cli.LifecycleError(30, "rollback", "package_rollback_incomplete")

    monkeypatch.setattr(Path, "unlink", fail_receipt_unlink)
    monkeypatch.setattr(cli, "_restore_package_state", fail_rollback)

    exit_code = cli.main(_args(layout, "uninstall", "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 30
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "rollback",
        "failure_reason": "package_rollback_incomplete",
    }


@pytest.mark.parametrize("verb", ["install", "upgrade"])
def test_install_and_upgrade_compatibility_precedes_package_and_file_mutation(
    tmp_path, capsys, monkeypatch, verb
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    if verb == "upgrade":
        _install_for_verify(cli, layout, capsys, monkeypatch)
    events = []
    state = cli._package_state_from_lines([])
    monkeypatch.setattr(
        cli,
        "_probe_compatibility",
        lambda _ctx: (
            events.append("compatibility")
            or {"python_version": "3.11.9", "core_version": "0.20.20", "host_version": "2025"}
        ),
    )
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: events.append("snapshot") or state)
    monkeypatch.setattr(cli, "_install_package", lambda _ctx, _source: events.append("pip") or True)
    monkeypatch.setattr(
        cli,
        "_probe_target",
        lambda _python: (
            events.append("target")
            or {"python_version": "3.11.9", "adapter_version": cli.ADAPTER_VERSION, "core_version": "0.20.20"}
        ),
    )
    real_replace = cli._replace_file
    monkeypatch.setattr(
        cli,
        "_replace_file",
        lambda source, destination: events.append("file") or real_replace(source, destination),
    )
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": False, "status": "missing"})

    assert cli.main(_args(layout, verb, "--yes")) == 50
    _report(cli, capsys)
    assert events[:4] == ["compatibility", "snapshot", "pip", "target"]
    assert events.index("compatibility") < events.index("pip") < events.index("file")


def test_uninstall_compatibility_precedes_package_and_file_mutation(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    events = []
    state = cli._package_state_from_lines([])
    monkeypatch.setattr(
        cli,
        "_probe_compatibility",
        lambda _ctx: (
            events.append("compatibility")
            or {"python_version": "3.11.9", "core_version": "0.20.20", "host_version": "2025"}
        ),
    )
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: events.append("snapshot") or state)
    monkeypatch.setattr(cli, "_uninstall_package", lambda _ctx: events.append("pip"))
    real_restore = cli._restore
    monkeypatch.setattr(
        cli,
        "_restore",
        lambda path, content: events.append("file") or real_restore(path, content),
    )

    assert cli.main(_args(layout, "uninstall", "--yes")) == 0
    _report(cli, capsys)
    assert events[:3] == ["compatibility", "snapshot", "pip"]
    assert events.index("compatibility") < events.index("pip") < events.index("file")
