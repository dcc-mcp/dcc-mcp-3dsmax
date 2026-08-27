"""Install SOP v1 contract tests for the public 3ds Max lifecycle CLI."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest


def _install_cli():
    return importlib.import_module("dcc_mcp_3dsmax.install_cli")


def _layout(tmp_path: Path, year: str = "2025"):
    host_root = tmp_path / "Autodesk" / ("3ds Max %s" % year)
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
    monkeypatch.setattr(
        cli,
        "_probe_compatibility",
        lambda _ctx: {"python_version": "3.11.9", "core_version": "0.20.20", "host_version": "2025"},
    )
    monkeypatch.setattr(cli, "_install_package", lambda *_args: package_calls.append(_args))

    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code == 0
    assert report["status"] == "planned"
    assert report["verify"]["directly_usable"] is False
    assert package_calls == []
    assert not layout["startup"].exists()
    assert not layout["receipt"].exists()


@pytest.mark.parametrize(
    ("compatibility", "expected_reason"),
    [
        (
            {"python_version": "3.6.15", "core_version": "0.20.20", "host_version": "2025"},
            "python_version_unsupported",
        ),
        (
            {"python_version": "3.11.9", "core_version": "0.20.19", "host_version": "2025"},
            "core_version_too_old",
        ),
    ],
)
def test_dry_run_reports_read_only_compatibility_failures_without_mutation(
    tmp_path, capsys, monkeypatch, compatibility, expected_reason
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    mutations = []
    monkeypatch.setattr(cli, "_probe_compatibility", lambda _ctx: compatibility)
    monkeypatch.setattr(cli, "_install_package", lambda *_args: mutations.append("package"))
    monkeypatch.setattr(cli, "_replace_file", lambda *_args: mutations.append("file"))

    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "preflight",
        "failure_reason": expected_reason,
    }
    assert mutations == []
    assert not layout["startup"].exists()
    assert not layout["receipt"].exists()


def test_dry_run_rejects_an_unexecutable_interpreter_without_mutation(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    mutations = []
    monkeypatch.setattr(cli, "_install_package", lambda *_args: mutations.append("package"))
    monkeypatch.setattr(cli, "_replace_file", lambda *_args: mutations.append("file"))

    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "preflight",
        "failure_reason": "compatibility_probe_failed",
    }
    assert mutations == []
    assert not layout["startup"].exists()
    assert not layout["receipt"].exists()


@pytest.mark.parametrize("verb", ["install", "upgrade"])
@pytest.mark.parametrize("alias_kind", ["hardlink", "symlink"])
def test_dry_run_rejects_an_aliased_preexisting_hook_without_mutation(
    tmp_path, capsys, monkeypatch, verb, alias_kind
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook.parent.mkdir(parents=True)
    foreign = tmp_path / "foreign-hook.ms"
    foreign.write_bytes(b"preexisting hook")
    if alias_kind == "hardlink":
        os.link(str(foreign), str(hook))
    else:
        os.symlink(str(foreign), str(hook))
    mutations = []
    monkeypatch.setattr(
        cli,
        "_probe_compatibility",
        lambda _ctx: {"python_version": "3.11.9", "core_version": "0.20.20", "host_version": "2025"},
    )
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: mutations.append("package_snapshot"))
    monkeypatch.setattr(cli, "_install_package", lambda *_args: mutations.append("package_install"))
    monkeypatch.setattr(cli, "_replace_file", lambda *_args: mutations.append("file_replace"))
    hook_content = hook.read_bytes()
    hook_identity = cli._file_identity(os.lstat(str(hook)))
    foreign_identity = cli._file_identity(os.lstat(str(foreign)))

    exit_code = cli.main(_args(layout, verb, "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "preflight",
        "failure_reason": "file_identity_ambiguous",
    }
    assert mutations == []
    assert not layout["receipt"].exists()
    assert hook.read_bytes() == hook_content
    assert cli._file_identity(os.lstat(str(hook))) == hook_identity
    assert cli._file_identity(os.lstat(str(foreign))) == foreign_identity


@pytest.mark.parametrize("verb", ["install", "upgrade", "uninstall"])
@pytest.mark.parametrize("drift_target", ["hook", "receipt"])
def test_public_mutations_preserve_concurrent_identity_on_snapshot_commit_drift(
    tmp_path, capsys, monkeypatch, verb, drift_target
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    if verb == "install":
        _stub_target(monkeypatch, cli)
        hook.parent.mkdir(parents=True)
        hook.write_bytes(b"preexisting hook")
    else:
        _install_for_verify(cli, layout, capsys, monkeypatch)
    receipt = layout["receipt"]
    hook_before = hook.read_bytes()
    hook_identity = cli._file_identity(os.lstat(str(hook)))
    receipt_before = receipt.read_bytes() if receipt.exists() else None
    receipt_identity = cli._file_identity(os.lstat(str(receipt))) if receipt.exists() else None
    target = hook if drift_target == "hook" else receipt
    concurrent_content = target.read_bytes() if target.exists() else b"concurrent receipt"
    concurrent_identity = []
    package_calls = []

    def swap_owned_identity(*_args):
        package_calls.append("apply")
        replacement = tmp_path / ("concurrent-%s-%s" % (verb, drift_target))
        replacement.write_bytes(concurrent_content)
        os.replace(str(replacement), str(target))
        concurrent_identity.append(cli._file_identity(os.lstat(str(target))))
        return True

    if verb in {"install", "upgrade"}:
        monkeypatch.setattr(cli, "_install_package", swap_owned_identity)
    else:
        monkeypatch.setattr(cli, "_uninstall_package", swap_owned_identity)
    monkeypatch.setattr(
        cli,
        "_restore_package_state",
        lambda _ctx, _prior: package_calls.append("restore"),
    )
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": True, "status": "ready"})

    exit_code = cli.main(_args(layout, verb, "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 30
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "commit",
        "failure_reason": "file_identity_changed",
    }
    assert package_calls == ["apply", "restore"]
    assert target.read_bytes() == concurrent_content
    assert cli._file_identity(os.lstat(str(target))) == concurrent_identity[0]
    if drift_target == "receipt":
        assert hook.read_bytes() == hook_before
        assert cli._file_identity(os.lstat(str(hook))) == hook_identity
    else:
        if receipt_before is None:
            assert not os.path.lexists(str(receipt))
        else:
            assert receipt.read_bytes() == receipt_before
            assert cli._file_identity(os.lstat(str(receipt))) == receipt_identity


@pytest.mark.parametrize("verb", ["install", "upgrade"])
@pytest.mark.parametrize("drift_target", ["hook", "receipt"])
def test_install_commit_does_not_clobber_identity_created_after_atomic_claim(
    tmp_path, capsys, monkeypatch, verb, drift_target
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    if verb == "upgrade":
        _install_for_verify(cli, layout, capsys, monkeypatch)
    else:
        _stub_target(monkeypatch, cli)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    receipt = layout["receipt"]
    target = hook if drift_target == "hook" else receipt
    concurrent_content = ("concurrent-%s-%s" % (verb, drift_target)).encode("ascii")
    concurrent_identity = []
    original_claim = getattr(cli, "_claim_file_if_snapshot", None)

    def claim_then_drift(path, expected, claim_path=None):
        claimed = original_claim(path, expected, claim_path) if original_claim is not None else None
        if path == target:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(concurrent_content)
            concurrent_identity.append(cli._file_identity(os.lstat(str(path))))
        return claimed

    monkeypatch.setattr(cli, "_claim_file_if_snapshot", claim_then_drift, raising=False)
    monkeypatch.setattr(cli, "_restore_package_state", lambda *_args: None)
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": True, "status": "ready"})

    exit_code = cli.main(_args(layout, verb, "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 30
    assert report["status"] == "failed"
    assert report["verify"]["failure_stage"] in {"commit", "rollback"}
    assert target.read_bytes() == concurrent_content
    assert cli._file_identity(os.lstat(str(target))) == concurrent_identity[0]


@pytest.mark.parametrize("mutation", ["restore", "unlink"])
def test_uninstall_commit_does_not_clobber_identity_created_after_atomic_claim(
    tmp_path, capsys, monkeypatch, mutation
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    receipt = layout["receipt"]
    target = hook if mutation == "restore" else receipt
    concurrent_content = ("concurrent-uninstall-%s" % mutation).encode("ascii")
    concurrent_identity = []
    original_claim = getattr(cli, "_claim_file_if_snapshot", None)

    def claim_then_drift(path, expected, claim_path=None):
        claimed = original_claim(path, expected, claim_path) if original_claim is not None else None
        if path == target:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(concurrent_content)
            concurrent_identity.append(cli._file_identity(os.lstat(str(path))))
        return claimed

    monkeypatch.setattr(cli, "_claim_file_if_snapshot", claim_then_drift, raising=False)
    monkeypatch.setattr(cli, "_restore_package_state", lambda *_args: None)

    exit_code = cli.main(_args(layout, "uninstall", "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 30
    assert report["status"] == "failed"
    assert report["verify"]["failure_stage"] in {"commit", "rollback"}
    assert target.read_bytes() == concurrent_content
    assert cli._file_identity(os.lstat(str(target))) == concurrent_identity[0]


def test_replace_records_may_have_committed_before_fallible_postcheck(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    source = tmp_path / "transaction"
    destination = tmp_path / "owned"
    source.write_bytes(b"transaction")
    destination.write_bytes(b"owned")
    expected = cli._snapshot(destination)
    committed = {}
    original_current = cli._snapshot_is_current

    def current_after_commit_fails(path, snapshot):
        expected_snapshot = cli._coerce_file_snapshot(snapshot)
        if path == destination and expected_snapshot.content == b"transaction":
            return False
        return original_current(path, snapshot)

    monkeypatch.setattr(cli, "_snapshot_is_current", current_after_commit_fails)

    with pytest.raises(cli.LifecycleError):
        cli._replace_file_if_snapshot(source, destination, expected, committed)

    assert destination in committed
    assert committed[destination].content == b"transaction"


def test_preflight_recovers_owned_claim_after_keyboard_interrupt(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _stub_target(monkeypatch, cli)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook.parent.mkdir(parents=True)
    hook.write_bytes(b"original hook")
    original_identity = cli._file_identity(os.lstat(str(hook)))
    real_replace = cli._replace_file

    def interrupt_after_claim(_source, _destination):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_replace_file", interrupt_after_claim)
    with pytest.raises(KeyboardInterrupt):
        cli.main(_args(layout, "install", "--yes"))

    journals = list(tmp_path.rglob(".*.transaction-*.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text(encoding="utf-8"))
    assert journal["destination"] == str(hook)
    assert Path(journal["claim_path"]).is_file()
    assert not os.path.lexists(str(hook))

    monkeypatch.setattr(cli, "_replace_file", real_replace)
    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code == 0
    assert report["status"] == "planned"
    assert hook.read_bytes() == b"original hook"
    assert cli._file_identity(os.lstat(str(hook))) == original_identity
    assert list(tmp_path.rglob(".*.transaction-*.json")) == []
    assert list(tmp_path.rglob(".*.claim-*")) == []


def test_preflight_preserves_contender_and_durable_claim_after_interrupt(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _stub_target(monkeypatch, cli)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook.parent.mkdir(parents=True)
    hook.write_bytes(b"original hook")
    real_replace = cli._replace_file

    monkeypatch.setattr(cli, "_replace_file", lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        cli.main(_args(layout, "install", "--yes"))

    hook.write_bytes(b"concurrent contender")
    contender_identity = cli._file_identity(os.lstat(str(hook)))
    monkeypatch.setattr(cli, "_replace_file", real_replace)

    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code == 30
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "recovery",
        "failure_reason": "transaction_recovery_conflict",
    }
    assert hook.read_bytes() == b"concurrent contender"
    assert cli._file_identity(os.lstat(str(hook))) == contender_identity
    journals = list(tmp_path.rglob(".*.transaction-*.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text(encoding="utf-8"))
    assert Path(journal["claim_path"]).read_bytes() == b"original hook"


def test_uninstall_rejects_a_dangling_hook_symlink_without_mutation(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook.parent.mkdir(parents=True)
    missing_target = tmp_path / "missing-hook-target.ms"
    os.symlink(str(missing_target), str(hook))
    package_calls = []
    monkeypatch.setattr(cli, "_uninstall_package", lambda _ctx: package_calls.append("uninstall"))

    exit_code = cli.main(_args(layout, "uninstall", "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "preflight",
        "failure_reason": "file_identity_ambiguous",
    }
    assert package_calls == []
    assert not layout["receipt"].exists()
    assert os.path.lexists(str(hook))
    assert hook.is_symlink()


def test_install_refuses_to_reuse_a_receipt_owned_by_another_target(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    first = _layout(tmp_path / "first", "2024")
    second = _layout(tmp_path / "second", "2025")
    second["receipt"] = first["receipt"]
    _stub_target(monkeypatch, cli)
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": False, "status": "missing"})
    assert cli.main(_args(first, "install", "--yes")) == 50
    _report(cli, capsys)
    first_hook = first["startup"] / cli.STARTUP_SCRIPT_NAME
    first_receipt_before = first["receipt"].read_bytes()
    first_hook_before = first_hook.read_bytes()
    second["startup"].mkdir(parents=True)
    second_hook = second["startup"] / cli.STARTUP_SCRIPT_NAME
    second_hook.write_bytes(b"preexisting 2025 hook")
    package_calls = []
    monkeypatch.setattr(cli, "_install_package", lambda *_args: package_calls.append("package"))

    exit_code = cli.main(_args(second, "install", "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 10
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "receipt",
        "failure_reason": "receipt_target_mismatch",
    }
    assert package_calls == []
    assert first["receipt"].read_bytes() == first_receipt_before
    assert first_hook.read_bytes() == first_hook_before
    assert second_hook.read_bytes() == b"preexisting 2025 hook"

    uninstall_exit = cli.main(_args(second, "uninstall", "--yes"))
    uninstall_report = _report(cli, capsys)

    assert uninstall_exit == 30
    assert uninstall_report["verify"] == {
        "directly_usable": False,
        "failure_stage": "receipt",
        "failure_reason": "receipt_target_mismatch",
    }
    assert package_calls == []
    assert first["receipt"].read_bytes() == first_receipt_before
    assert first_hook.read_bytes() == first_hook_before
    assert second_hook.read_bytes() == b"preexisting 2025 hook"


@pytest.mark.parametrize("verb", ["status", "verify"])
@pytest.mark.parametrize("alias_kind", ["hardlink", "symlink"])
def test_status_and_verify_reject_a_same_bytes_aliased_hook(tmp_path, capsys, monkeypatch, verb, alias_kind) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    foreign = tmp_path / "foreign-hook.ms"
    if alias_kind == "hardlink":
        os.link(str(hook), str(foreign))
    else:
        content = hook.read_bytes()
        hook.unlink()
        foreign.write_bytes(content)
        os.symlink(str(foreign), str(hook))
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": True, "status": "ready"})

    exit_code = cli.main(_args(layout, verb))
    report = _report(cli, capsys)

    assert exit_code == (10 if verb == "status" else 40)
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "artifact",
        "failure_reason": "startup_hook_missing_or_modified",
    }
    assert os.path.samefile(str(hook), str(foreign))


@pytest.mark.parametrize("verb", ["status", "verify", "uninstall"])
@pytest.mark.parametrize(
    ("substitution", "failure_stage", "failure_reason", "expected_exit"),
    [
        ("hook_replace", "artifact", "startup_hook_missing_or_modified", {"status": 10, "verify": 40, "uninstall": 30}),
        ("receipt_hardlink", "receipt", "receipt_ownership_invalid", {"status": 10, "verify": 40, "uninstall": 10}),
        ("receipt_symlink", "receipt", "receipt_ownership_invalid", {"status": 10, "verify": 40, "uninstall": 10}),
        ("receipt_replace", "receipt", "receipt_ownership_invalid", {"status": 10, "verify": 40, "uninstall": 10}),
    ],
)
def test_public_ownership_verbs_reject_same_bytes_identity_substitution_without_mutation(
    tmp_path,
    capsys,
    monkeypatch,
    verb,
    substitution,
    failure_stage,
    failure_reason,
    expected_exit,
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    preexisting_hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    preexisting_hook.parent.mkdir(parents=True)
    preexisting_hook.write_bytes(b"preexisting hook")
    _install_for_verify(cli, layout, capsys, monkeypatch)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    receipt = layout["receipt"]
    foreign = tmp_path / ("foreign-%s" % substitution)
    target = hook if substitution == "hook_replace" else receipt
    if substitution == "receipt_hardlink":
        os.link(str(receipt), str(foreign))
    elif substitution == "receipt_symlink":
        content = receipt.read_bytes()
        receipt.unlink()
        foreign.write_bytes(content)
        os.symlink(str(foreign), str(receipt))
    else:
        foreign.write_bytes(target.read_bytes())
        os.replace(str(foreign), str(target))
    hook_identity = cli._file_identity(os.lstat(str(hook)))
    receipt_identity = cli._file_identity(os.lstat(str(receipt)))
    hook_content = hook.read_bytes()
    receipt_content = receipt.read_bytes()
    package_calls = []
    monkeypatch.setattr(cli, "_uninstall_package", lambda _ctx: package_calls.append("package"))
    monkeypatch.setattr(cli, "_wait_readiness", lambda _timeout: {"success": True, "status": "ready"})

    extra = ("--yes",) if verb == "uninstall" else ()
    exit_code = cli.main(_args(layout, verb, *extra))
    report = _report(cli, capsys)

    assert exit_code == expected_exit[verb]
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
    }
    assert package_calls == []
    assert hook.read_bytes() == hook_content
    assert receipt.read_bytes() == receipt_content
    assert cli._file_identity(os.lstat(str(hook))) == hook_identity
    assert cli._file_identity(os.lstat(str(receipt))) == receipt_identity


def test_receipt_persists_receipt_hook_and_previous_hook_identities(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    hook = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook.parent.mkdir(parents=True)
    hook.write_bytes(b"preexisting hook")
    previous_identity = cli._file_identity(os.lstat(str(hook)))

    _install_for_verify(cli, layout, capsys, monkeypatch)

    receipt = json.loads(layout["receipt"].read_text(encoding="utf-8"))
    assert receipt["receipt_identity"] == cli._identity_record(cli._file_identity(os.lstat(str(layout["receipt"]))))
    assert receipt["artifacts"][0]["identity"] == cli._identity_record(cli._file_identity(os.lstat(str(hook))))
    assert receipt["previous_hook"]["identity"] == cli._identity_record(previous_identity)


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


def test_verify_rejects_hostile_readiness_string_subclass(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)

    class HostileStatus(str):
        def __eq__(self, _other):
            raise RuntimeError("PRIVATE_COMPARE_SECRET C:/private/path")

        def __hash__(self):
            raise RuntimeError("PRIVATE_HASH_SECRET x:private-uri")

        def __str__(self):
            raise RuntimeError("PRIVATE_STRING_SECRET x:private-uri")

    monkeypatch.setattr(
        cli,
        "_wait_readiness",
        lambda _timeout: {"success": True, "status": HostileStatus("ready")},
    )

    exit_code = cli.main(_args(layout, "verify"))
    captured = capsys.readouterr()

    assert exit_code == 40
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "readiness",
        "failure_reason": "invalid_readiness_status",
    }


@pytest.mark.parametrize("verb", ["install", "upgrade", "verify"])
@pytest.mark.parametrize("success", [True, False])
def test_public_verbs_reject_hostile_readiness_string_subclasses(tmp_path, capsys, monkeypatch, verb, success) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    if verb in {"upgrade", "verify"}:
        _install_for_verify(cli, layout, capsys, monkeypatch)
    else:
        _stub_target(monkeypatch, cli)

    class HostileStatus(str):
        def __eq__(self, _other):
            raise RuntimeError("PRIVATE_COMPARE_SECRET C:/private/path")

        def __hash__(self):
            raise RuntimeError("PRIVATE_HASH_SECRET x:private-uri")

        def __str__(self):
            raise RuntimeError("PRIVATE_STRING_SECRET x:private-uri")

    monkeypatch.setattr(
        cli,
        "_wait_readiness",
        lambda _timeout: {"success": success, "status": HostileStatus("ready")},
    )

    extra = ("--yes",) if verb in {"install", "upgrade"} else ()
    exit_code = cli.main(_args(layout, verb, *extra))
    captured = capsys.readouterr()

    assert exit_code == (50 if verb in {"install", "upgrade"} else 40)
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_" not in captured.out
    assert "private-uri" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "readiness",
        "failure_reason": "invalid_readiness_status",
    }
    if verb in {"install", "upgrade"}:
        assert report["status"] == "requires_restart"
        assert report["steps"][3] == {"id": "commit", "status": "ok"}


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


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_readiness_does_not_capture_base_exceptions(tmp_path, capsys, monkeypatch, exception_type) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)

    def interrupt_readiness(_timeout):
        raise exception_type()

    monkeypatch.setattr(cli, "_wait_readiness", interrupt_readiness)

    with pytest.raises(exception_type):
        cli.main(_args(layout, "verify"))


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


def test_package_rollback_removes_only_exact_transaction_owned_distributions(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines([])
    transaction_owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    current = cli._package_state_from_lines(
        ["dcc-mcp-3dsmax==0.2.2", "concurrent-owner @ file:///concurrent/owner.whl"]
    )
    reconciled = cli._package_state_from_lines(["concurrent-owner @ file:///concurrent/owner.whl"])
    snapshots = iter([current, current, reconciled])
    commands = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: next(snapshots))
    monkeypatch.setattr(cli, "_run_pip_command", lambda _ctx, command: commands.append(command))

    cli._restore_package_state(ctx, prior, transaction_owned)

    assert commands == [["uninstall", "-y", "dcc-mcp-3dsmax"]]


def test_package_rollback_preserves_concurrent_replacement_of_owned_distribution(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines([])
    transaction_owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    concurrent = cli._package_state_from_lines(["dcc-mcp-3dsmax @ file:///concurrent/dcc_mcp_3dsmax-0.2.2.whl"])
    commands = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: concurrent)
    monkeypatch.setattr(cli, "_run_pip_command", lambda _ctx, command: commands.append(command))

    cli._restore_package_state(ctx, prior, transaction_owned)

    assert commands == []


def test_package_rollback_revalidates_exact_owner_immediately_before_pip_mutation(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines([])
    transaction_owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    concurrent = cli._package_state_from_lines(["dcc-mcp-3dsmax @ file:///concurrent/dcc_mcp_3dsmax-0.2.2.whl"])
    snapshots = iter([transaction_owned, concurrent])
    commands = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: next(snapshots))
    monkeypatch.setattr(cli, "_run_pip_command", lambda _ctx, command: commands.append(command))

    with pytest.raises(cli.LifecycleError) as captured:
        cli._restore_package_state(ctx, prior, transaction_owned)

    assert captured.value.stage == "rollback"
    assert captured.value.reason == "package_rollback_incomplete"
    assert commands == []


def test_transaction_rollback_detects_post_restore_overwrite_and_still_reconciles_packages(
    tmp_path, monkeypatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "upgrade", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True)
    ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.hook_path.write_bytes(b"mutated hook")
    ctx.receipt_path.write_bytes(b"mutated receipt")
    prior_package_state = cli._package_state_from_lines([])
    package_calls = []
    real_restore = cli._restore

    def restore_then_overwrite(path, content):
        restored_identity = real_restore(path, content)
        if path == ctx.hook_path:
            path.write_bytes(b"foreign-after-restore")
        return restored_identity

    monkeypatch.setattr(cli, "_restore", restore_then_overwrite)
    monkeypatch.setattr(
        cli,
        "_restore_package_state",
        lambda _ctx, _prior: package_calls.append("package"),
    )

    with pytest.raises(cli.LifecycleError) as captured:
        cli._rollback_transaction(
            ctx,
            prior_package_state,
            b"original hook",
            b"original receipt",
            True,
        )

    assert captured.value.stage == "rollback"
    assert captured.value.reason == "transaction_rollback_incomplete"
    assert package_calls == ["package"]
    assert ctx.hook_path.read_bytes() == b"foreign-after-restore"
    assert ctx.receipt_path.read_bytes() == b"original receipt"


def test_transaction_rollback_rejects_same_bytes_hardlink_identity_swap(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "upgrade", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True)
    ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.hook_path.write_bytes(b"mutated hook")
    ctx.receipt_path.write_bytes(b"mutated receipt")
    foreign = tmp_path / "PRIVATE_FOREIGN_SECRET.ms"
    prior_package_state = cli._package_state_from_lines([])
    package_calls = []
    real_restore = cli._restore

    def restore_then_alias(path, content):
        restored_identity = real_restore(path, content)
        if path == ctx.hook_path:
            foreign.write_bytes(content)
            path.unlink()
            os.link(str(foreign), str(path))
        return restored_identity

    monkeypatch.setattr(cli, "_restore", restore_then_alias)
    monkeypatch.setattr(
        cli,
        "_restore_package_state",
        lambda _ctx, _prior: package_calls.append("package"),
    )

    with pytest.raises(cli.LifecycleError) as captured:
        cli._rollback_transaction(
            ctx,
            prior_package_state,
            b"original hook",
            b"original receipt",
            True,
        )

    assert captured.value.stage == "rollback"
    assert captured.value.reason == "transaction_rollback_incomplete"
    assert os.path.samefile(str(ctx.hook_path), str(foreign))
    assert package_calls == ["package"]


def test_transaction_rollback_rejects_same_bytes_independent_identity_swap(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "upgrade", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True)
    ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.hook_path.write_bytes(b"mutated hook")
    ctx.receipt_path.write_bytes(b"mutated receipt")
    foreign = tmp_path / "PRIVATE_FOREIGN_SECRET.ms"
    prior_package_state = cli._package_state_from_lines([])
    package_calls = []
    real_restore = cli._restore

    def restore_then_replace(path, content):
        restored_identity = real_restore(path, content)
        if path == ctx.hook_path:
            foreign.write_bytes(content)
            os.replace(str(foreign), str(path))
        return restored_identity

    monkeypatch.setattr(cli, "_restore", restore_then_replace)
    monkeypatch.setattr(
        cli,
        "_restore_package_state",
        lambda _ctx, _prior: package_calls.append("package"),
    )

    with pytest.raises(cli.LifecycleError) as captured:
        cli._rollback_transaction(
            ctx,
            prior_package_state,
            b"original hook",
            b"original receipt",
            True,
        )

    assert captured.value.stage == "rollback"
    assert captured.value.reason == "transaction_rollback_incomplete"
    assert ctx.hook_path.read_bytes() == b"original hook"
    assert not foreign.exists()
    assert package_calls == ["package"]


def test_file_identity_contract_rejects_symlink_reparse_and_hardlink_metadata() -> None:
    cli = _install_cli()
    regular = SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_file_attributes=0)
    symlink = SimpleNamespace(st_mode=stat.S_IFLNK, st_nlink=1, st_file_attributes=0)
    reparse = SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_file_attributes=0x400)
    hardlink = SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=2, st_file_attributes=0)

    assert cli._is_independent_regular_file(regular) is True
    assert cli._is_independent_regular_file(symlink) is False
    assert cli._is_independent_regular_file(reparse) is False
    assert cli._is_independent_regular_file(hardlink) is False


def test_snapshot_rejects_preexisting_hardlink_alias_before_mutation(tmp_path) -> None:
    cli = _install_cli()
    foreign = tmp_path / "foreign.ms"
    alias = tmp_path / "alias.ms"
    foreign.write_bytes(b"same bytes")
    os.link(str(foreign), str(alias))

    with pytest.raises(cli.LifecycleError) as captured:
        cli._snapshot(alias)

    assert captured.value.exit_code == 10
    assert captured.value.stage == "preflight"
    assert captured.value.reason == "file_identity_ambiguous"


def test_snapshot_captures_stable_independent_identity_and_transaction_restores_it(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "upgrade", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True)
    ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.hook_path.write_bytes(b"original hook")
    ctx.receipt_path.write_bytes(b"original receipt")
    hook_snapshot = cli._snapshot(ctx.hook_path)
    receipt_snapshot = cli._snapshot(ctx.receipt_path)
    ctx.hook_path.write_bytes(b"mutated hook")
    ctx.receipt_path.write_bytes(b"mutated receipt")
    package_calls = []
    monkeypatch.setattr(
        cli,
        "_restore_package_state",
        lambda _ctx, _prior: package_calls.append("package"),
    )

    cli._rollback_transaction(
        ctx,
        cli._package_state_from_lines([]),
        hook_snapshot,
        receipt_snapshot,
        True,
    )

    assert hook_snapshot.existed is True
    assert hook_snapshot.independent is True
    assert hook_snapshot.identity is not None
    assert hook_snapshot.sha256 == hashlib.sha256(b"original hook").hexdigest()
    assert ctx.hook_path.read_bytes() == b"original hook"
    assert ctx.receipt_path.read_bytes() == b"original receipt"
    assert os.stat(str(ctx.hook_path)).st_nlink == 1
    assert os.stat(str(ctx.receipt_path)).st_nlink == 1
    assert package_calls == ["package"]


def test_transaction_rollback_combines_file_and_package_failures(tmp_path, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "upgrade", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True)
    ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.hook_path.write_bytes(b"mutated hook")
    ctx.receipt_path.write_bytes(b"mutated receipt")
    prior_package_state = cli._package_state_from_lines([])
    restore_calls = []
    package_calls = []
    real_restore = cli._restore

    def restore_then_overwrite(path, content):
        restore_calls.append(path)
        restored_identity = real_restore(path, content)
        if path == ctx.hook_path:
            path.write_bytes(b"foreign-after-restore")
        return restored_identity

    def fail_package_restore(_ctx, _prior):
        package_calls.append("package")
        raise cli.LifecycleError(30, "rollback", "package_rollback_incomplete")

    monkeypatch.setattr(cli, "_restore", restore_then_overwrite)
    monkeypatch.setattr(cli, "_restore_package_state", fail_package_restore)

    with pytest.raises(cli.LifecycleError) as captured:
        cli._rollback_transaction(
            ctx,
            prior_package_state,
            b"original hook",
            b"original receipt",
            True,
        )

    assert captured.value.stage == "rollback"
    assert captured.value.reason == "transaction_rollback_incomplete"
    assert restore_calls == [ctx.hook_path, ctx.receipt_path]
    assert package_calls == ["package"]
    assert ctx.hook_path.read_bytes() == b"foreign-after-restore"
    assert ctx.receipt_path.read_bytes() == b"original receipt"


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
        "failure_reason": "transaction_rollback_incomplete",
    }


def test_uninstall_reports_incomplete_package_rollback(tmp_path, capsys, monkeypatch) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    real_restore = cli._restore

    def fail_receipt_remove(path, content):
        if path == layout["receipt"] and content is None:
            raise OSError("commit failed")
        return real_restore(path, content)

    def fail_rollback(_ctx, _prior):
        raise cli.LifecycleError(30, "rollback", "package_rollback_incomplete")

    monkeypatch.setattr(cli, "_restore", fail_receipt_remove)
    monkeypatch.setattr(cli, "_restore_package_state", fail_rollback)

    exit_code = cli.main(_args(layout, "uninstall", "--yes"))
    report = _report(cli, capsys)

    assert exit_code == 30
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "rollback",
        "failure_reason": "transaction_rollback_incomplete",
    }


@pytest.mark.parametrize("verb", ["install", "upgrade", "uninstall"])
@pytest.mark.parametrize("package_failure", [False, True])
def test_public_mutations_report_post_restore_overwrite_as_incomplete_rollback(
    tmp_path, capsys, monkeypatch, verb, package_failure
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    if verb in {"upgrade", "uninstall"}:
        _install_for_verify(cli, layout, capsys, monkeypatch)
    else:
        _stub_target(monkeypatch, cli)

    hook_path = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    package_calls = []
    hook_restore_calls = 0
    real_restore = cli._restore

    def restore_then_overwrite(path, content):
        nonlocal hook_restore_calls
        restored_identity = real_restore(path, content)
        if path == hook_path:
            hook_restore_calls += 1
            rollback_call = hook_restore_calls == (2 if verb == "uninstall" else 1)
            if rollback_call:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"foreign-after-restore")
        return restored_identity

    monkeypatch.setattr(cli, "_restore", restore_then_overwrite)

    def restore_package(_ctx, _prior):
        package_calls.append("package")
        if package_failure:
            raise cli.LifecycleError(30, "rollback", "package_rollback_incomplete")

    monkeypatch.setattr(cli, "_restore_package_state", restore_package)
    if verb in {"install", "upgrade"}:
        real_replace = cli._replace_file

        def fail_receipt_commit(source, destination):
            if destination == layout["receipt"]:
                raise OSError("PRIVATE_COMMIT_SECRET x:private-uri/C:/private/path")
            return real_replace(source, destination)

        monkeypatch.setattr(cli, "_replace_file", fail_receipt_commit)
    else:
        commit_restore = cli._restore

        def fail_receipt_remove(path, content):
            if path == layout["receipt"] and content is None:
                raise OSError("PRIVATE_COMMIT_SECRET x:private-uri/C:/private/path")
            return commit_restore(path, content)

        monkeypatch.setattr(cli, "_restore", fail_receipt_remove)

    exit_code = cli.main(_args(layout, verb, "--yes"))
    captured = capsys.readouterr()

    assert exit_code == 30
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_" not in captured.out
    assert "private-uri" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["status"] == "failed"
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "rollback",
        "failure_reason": "transaction_rollback_incomplete",
    }
    assert package_calls == ["package"]
    assert hook_path.read_bytes() == b"foreign-after-restore"


@pytest.mark.parametrize("verb", ["install", "upgrade", "uninstall"])
@pytest.mark.parametrize("identity_swap", ["hardlink", "replace"])
def test_public_mutations_reject_same_bytes_rollback_identity_swap(
    tmp_path, capsys, monkeypatch, verb, identity_swap
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    hook_path = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    if verb in {"upgrade", "uninstall"}:
        _install_for_verify(cli, layout, capsys, monkeypatch)
    else:
        _stub_target(monkeypatch, cli)
        hook_path.parent.mkdir(parents=True)
        hook_path.write_bytes(b"original install hook")

    foreign = tmp_path / ("PRIVATE_%s_FOREIGN_SECRET.ms" % verb)
    package_calls = []
    hook_restore_calls = 0
    real_restore = cli._restore

    def restore_then_alias(path, content):
        nonlocal hook_restore_calls
        restored_identity = real_restore(path, content)
        if path == hook_path:
            hook_restore_calls += 1
            rollback_call = hook_restore_calls == (2 if verb == "uninstall" else 1)
            if rollback_call:
                assert content is not None
                foreign.write_bytes(content)
                if identity_swap == "hardlink":
                    path.unlink()
                    os.link(str(foreign), str(path))
                else:
                    os.replace(str(foreign), str(path))
        return restored_identity

    monkeypatch.setattr(cli, "_restore", restore_then_alias)
    monkeypatch.setattr(
        cli,
        "_restore_package_state",
        lambda _ctx, _prior: package_calls.append("package"),
    )
    if verb in {"install", "upgrade"}:
        real_replace = cli._replace_file

        def fail_receipt_commit(source, destination):
            if destination == layout["receipt"]:
                raise OSError("PRIVATE_COMMIT_SECRET x:private-uri/C:/private/path")
            return real_replace(source, destination)

        monkeypatch.setattr(cli, "_replace_file", fail_receipt_commit)
    else:
        commit_restore = cli._restore

        def fail_receipt_remove(path, content):
            if path == layout["receipt"] and content is None:
                raise OSError("PRIVATE_COMMIT_SECRET x:private-uri/C:/private/path")
            return commit_restore(path, content)

        monkeypatch.setattr(cli, "_restore", fail_receipt_remove)

    exit_code = cli.main(_args(layout, verb, "--yes"))
    captured = capsys.readouterr()

    assert exit_code == 30
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "PRIVATE_" not in captured.out
    assert "private-uri" not in captured.out
    report = cli.loads_public_report(captured.out)
    assert report["verify"] == {
        "directly_usable": False,
        "failure_stage": "rollback",
        "failure_reason": "transaction_rollback_incomplete",
    }
    if identity_swap == "hardlink":
        assert os.path.samefile(str(hook_path), str(foreign))
    else:
        assert hook_path.is_file()
        assert not foreign.exists()
    assert package_calls == ["package"]


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
    assert events[:5] == ["compatibility", "snapshot", "pip", "snapshot", "target"]
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
