"""Install SOP v1 contract tests for the public 3ds Max lifecycle CLI."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import venv
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
    monkeypatch.setattr(cli, "_install_package", lambda _ctx, _source, _mutex: {})
    monkeypatch.setattr(
        cli,
        "_uninstall_package",
        lambda _ctx, _mutex, before: {"before": before, "after": before},
    )
    empty_package_state = cli._package_state_from_lines([])
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: empty_package_state)


def _stub_owned_pip(monkeypatch, cli, callback):
    monkeypatch.setattr(
        cli,
        "_run_target_owned_pip_command",
        lambda ctx, command, _token_path, _token: callback(ctx, command),
    )


def _isolated_distribution(cli, tmp_path: Path):
    environment = tmp_path / "target-environment"
    venv.EnvBuilder(with_pip=False).create(str(environment))
    python_path = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site_packages = environment / "Lib/site-packages" if os.name == "nt" else None
    if site_packages is None:
        completed = subprocess.run(
            [str(python_path), "-c", "import site;print(site.getsitepackages()[0])"],
            check=True,
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        site_packages = Path(completed.stdout.strip())
    package_dir = site_packages / "review_contender"
    package_dir.mkdir(parents=True)
    payload = package_dir / "__init__.py"
    payload.write_text("VALUE = 1\n", encoding="utf-8")
    dist_info = site_packages / "review_contender-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: review-contender\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        "review_contender/__init__.py,,\n"
        "review_contender-1.0.dist-info/METADATA,,\n"
        "review_contender-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    return SimpleNamespace(python_path=python_path), dist_info, payload


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

    def swap_owned_identity(*args):
        package_calls.append("apply")
        replacement = tmp_path / ("concurrent-%s-%s" % (verb, drift_target))
        replacement.write_bytes(concurrent_content)
        os.replace(str(replacement), str(target))
        concurrent_identity.append(cli._file_identity(os.lstat(str(target))))
        before = args[-1] if isinstance(args[-1], dict) else None
        return {"before": before, "after": before} if before is not None else {}

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
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    cli._preflight_ownership(cli._context(args))

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
        "failure_reason": "transaction_recovery_required",
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
    monkeypatch.setattr(
        cli,
        "_uninstall_package",
        lambda _ctx, _mutex, before: package_calls.append("uninstall") or {"before": before, "after": before},
    )

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
    monkeypatch.setattr(
        cli,
        "_uninstall_package",
        lambda _ctx, _mutex, before: package_calls.append("package") or {"before": before, "after": before},
    )
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
    monkeypatch.setattr(
        cli,
        "_uninstall_package",
        lambda _ctx, _mutex, before: events.append("pip") or {"before": before, "after": before},
    )
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

    _stub_owned_pip(monkeypatch, cli, fail_restore)

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
    snapshots = iter([current, current, current, current, reconciled])
    commands = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: next(snapshots))
    _stub_owned_pip(monkeypatch, cli, lambda _ctx, command: commands.append(command))

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
    _stub_owned_pip(monkeypatch, cli, lambda _ctx, command: commands.append(command))

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
    _stub_owned_pip(monkeypatch, cli, lambda _ctx, command: commands.append(command))

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

    def restore_then_overwrite(path, _desired):
        if path == ctx.hook_path:
            path.write_bytes(b"foreign-after-restore")

    monkeypatch.setattr(cli, "_file_transaction_after_publish", restore_then_overwrite)
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

    def restore_then_alias(path, desired):
        if path == ctx.hook_path:
            foreign.write_bytes(desired.content)
            path.unlink()
            os.link(str(foreign), str(path))

    monkeypatch.setattr(cli, "_file_transaction_after_publish", restore_then_alias)
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

    def restore_then_replace(path, desired):
        if path == ctx.hook_path:
            foreign.write_bytes(desired.content)
            os.replace(str(foreign), str(path))

    monkeypatch.setattr(cli, "_file_transaction_after_publish", restore_then_replace)
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

    def restore_then_overwrite(path, _desired):
        restore_calls.append(path)
        if path == ctx.hook_path:
            path.write_bytes(b"foreign-after-restore")

    def fail_package_restore(_ctx, _prior):
        package_calls.append("package")
        raise cli.LifecycleError(30, "rollback", "package_rollback_incomplete")

    monkeypatch.setattr(cli, "_file_transaction_after_publish", restore_then_overwrite)
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

    def restore_then_overwrite(path, _desired):
        nonlocal hook_restore_calls
        if path == hook_path:
            hook_restore_calls += 1
            rollback_call = hook_restore_calls == 1
            if rollback_call:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"foreign-after-restore")

    monkeypatch.setattr(cli, "_file_transaction_after_publish", restore_then_overwrite)

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

    def restore_then_alias(path, desired):
        nonlocal hook_restore_calls
        if path == hook_path:
            hook_restore_calls += 1
            rollback_call = hook_restore_calls == 1
            if rollback_call:
                foreign.write_bytes(desired.content)
                if identity_swap == "hardlink":
                    path.unlink()
                    os.link(str(foreign), str(path))
                else:
                    os.replace(str(foreign), str(path))

    monkeypatch.setattr(cli, "_file_transaction_after_publish", restore_then_alias)
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
    monkeypatch.setattr(cli, "_install_package", lambda _ctx, _source, _mutex: events.append("pip") or {})
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
    monkeypatch.setattr(
        cli,
        "_uninstall_package",
        lambda _ctx, _mutex, before: events.append("pip") or {"before": before, "after": before},
    )
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


def test_reconcile_recovers_publish_interrupted_before_stage_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    destination = tmp_path / "startup.py"
    stage = tmp_path / ".startup.py.stage"
    destination.write_bytes(b"previous")
    stage.write_bytes(b"transaction")
    expected = cli._snapshot(destination)
    committed: dict[str, object] = {}
    real_replace = cli._replace_file

    def publish_then_interrupt(source: Path, target: Path) -> None:
        real_replace(source, target)
        raise KeyboardInterrupt("publish completed before stage cleanup")

    monkeypatch.setattr(cli, "_replace_file", publish_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        cli._replace_file_if_snapshot(stage, destination, expected, committed)

    assert os.path.samefile(stage, destination)
    assert os.lstat(destination).st_nlink == 2
    journal_path = next(tmp_path.glob(".startup.py.transaction-*.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["stage_path"] == str(stage)
    assert journal["stage"]["identity"] is not None
    assert journal["commit_path"]
    monkeypatch.setattr(cli, "_replace_file", real_replace)
    cli._reconcile_file_transaction(destination)

    assert destination.read_bytes() == b"previous"
    assert cli._snapshot(destination).identity == expected.identity
    assert not os.path.lexists(stage)


def test_reconcile_recovers_restore_published_with_unresolved_desired_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    destination = tmp_path / "startup.py"
    destination.write_bytes(b"transaction")
    expected = cli._snapshot(destination)
    committed: dict[str, object] = {}
    real_commit = cli._write_committed_marker

    def restore_then_interrupt(commit_path: Path, target: Path, desired):
        real_commit(commit_path, target, desired)
        raise KeyboardInterrupt("restore published before journal cleanup")

    monkeypatch.setattr(cli, "_write_committed_marker", restore_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        cli._restore_if_snapshot(destination, b"previous", expected, committed)

    assert destination.read_bytes() == b"previous"
    journal_path = next(tmp_path.glob(".startup.py.transaction-*.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["desired"]["identity"] is not None
    assert journal["desired"]["independent"] is True
    assert Path(journal["commit_path"]).is_file()
    monkeypatch.setattr(cli, "_write_committed_marker", real_commit)
    cli._reconcile_file_transaction(destination)

    assert destination.read_bytes() == b"previous"
    assert cli._snapshot(destination).identity != expected.identity


def test_reconcile_accepts_committed_absence_before_journal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    destination = tmp_path / "startup.py"
    destination.write_bytes(b"transaction")
    expected = cli._snapshot(destination)
    committed: dict[str, object] = {}
    real_remove = cli._remove_owned_file

    def interrupt_journal_cleanup(path: Path, snapshot) -> None:
        if ".transaction-" in path.name:
            raise KeyboardInterrupt("absence committed before journal cleanup")
        real_remove(path, snapshot)

    monkeypatch.setattr(cli, "_remove_owned_file", interrupt_journal_cleanup)
    with pytest.raises(KeyboardInterrupt):
        cli._unlink_if_snapshot(destination, expected, committed)

    assert not os.path.lexists(destination)
    journal_path = next(tmp_path.glob(".startup.py.transaction-*.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert Path(journal["commit_path"]).is_file()
    monkeypatch.setattr(cli, "_remove_owned_file", real_remove)
    cli._reconcile_file_transaction(destination)
    assert not os.path.lexists(destination)


def test_package_rollback_rechecks_ownership_in_final_pre_pip_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines([])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    concurrent = cli._package_state_from_lines(["dcc-mcp-3dsmax @ file:///concurrent/dcc_mcp_3dsmax-0.2.2.whl"])
    absent = cli._package_state_from_lines([])
    state = {"package": owned}
    commands: list[list[str]] = []

    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])

    def replace_in_final_window(_ctx, _mutex) -> None:
        state["package"] = concurrent

    monkeypatch.setattr(cli, "_package_rollback_before_pip", replace_in_final_window, raising=False)

    def run_pip(_ctx, command: list[str]) -> None:
        commands.append(command)
        state["package"] = absent

    _stub_owned_pip(monkeypatch, cli, run_pip)

    with pytest.raises(cli.LifecycleError):
        cli._restore_package_state(ctx, prior, owned)

    assert commands == []
    assert state["package"] == concurrent


def test_package_ownership_mutex_excludes_another_process(tmp_path: Path) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    probe = (
        "import os,sys\n"
        "stream=open(sys.argv[1],'a+b')\n"
        "stream.seek(0)\n"
        "try:\n"
        " if os.name=='nt':\n"
        "  import msvcrt\n"
        "  msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)\n"
        " else:\n"
        "  import fcntl\n"
        "  fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "except (OSError,IOError):\n"
        " sys.exit(23)\n"
    )

    with cli._package_ownership(ctx):
        blocked = subprocess.run([sys.executable, "-c", probe, str(cli._package_mutex_path(ctx))])
    acquired = subprocess.run([sys.executable, "-c", probe, str(cli._package_mutex_path(ctx))])

    assert blocked.returncode == 23
    assert acquired.returncode == 0


def test_reconcile_recovers_real_exit_during_journal_publication(tmp_path: Path) -> None:
    script = r"""
import os, sys
from pathlib import Path
from dcc_mcp_3dsmax import install_cli as cli
root = Path(sys.argv[1])
destination = root / "startup.py"
stage = root / ".startup.py.stage"
destination.write_bytes(b"previous")
stage.write_bytes(b"transaction")
expected = cli._snapshot(destination)
desired = cli._snapshot(stage)
real_unlink = cli._durable_unlink
def crash(path):
    if ".journal-stage-" in path.name:
        os._exit(91)
    return real_unlink(path)
cli._durable_unlink = crash
cli._write_file_transaction(destination, expected, desired, stage)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env)

    assert completed.returncode == 91
    journal = next(tmp_path.glob(".startup.py.transaction-*.json"))
    journal_stage = next(tmp_path.glob(".startup.py.journal-stage-*"))
    assert os.path.samefile(journal, journal_stage)
    assert os.lstat(journal).st_nlink == 2

    cli = _install_cli()
    cli._reconcile_file_transaction(tmp_path / "startup.py")
    assert (tmp_path / "startup.py").read_bytes() == b"previous"
    assert not list(tmp_path.glob(".startup.py.*"))


def test_reconcile_recovers_real_exit_during_commit_marker_publication(tmp_path: Path) -> None:
    script = r"""
import os, sys
from pathlib import Path
from dcc_mcp_3dsmax import install_cli as cli
root = Path(sys.argv[1])
destination = root / "startup.py"
stage = root / ".startup.py.stage"
destination.write_bytes(b"previous")
stage.write_bytes(b"transaction")
expected = cli._snapshot(destination)
real_unlink = cli._durable_unlink
def crash(path):
    if ".committed-" in path.name and path.name.endswith(".stage"):
        os._exit(92)
    return real_unlink(path)
cli._durable_unlink = crash
cli._replace_file_if_snapshot(stage, destination, expected, {})
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env)

    assert completed.returncode == 92
    marker = next(tmp_path.glob(".startup.py.committed-*.json"))
    marker_stage = next(tmp_path.glob(".startup.py.committed-*.json.stage"))
    assert os.path.samefile(marker, marker_stage)
    assert os.lstat(marker).st_nlink == 2

    cli = _install_cli()
    cli._reconcile_file_transaction(tmp_path / "startup.py")
    destination = tmp_path / "startup.py"
    assert destination.read_bytes() == b"transaction"
    assert os.lstat(destination).st_nlink == 1
    assert not list(tmp_path.glob(".startup.py.*"))


def test_dry_run_reports_recovery_required_without_mutating_transaction_state(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.hook_path.write_bytes(b"previous")
    stage = ctx.hook_path.with_name(".%s.stage-red" % ctx.hook_path.name)
    stage.write_bytes(b"transaction")
    expected = cli._snapshot(ctx.hook_path)
    real_replace = cli._replace_file

    def publish_then_interrupt(source: Path, destination: Path) -> None:
        real_replace(source, destination)
        raise KeyboardInterrupt("crash before commit marker")

    monkeypatch.setattr(cli, "_replace_file", publish_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        cli._replace_file_if_snapshot(stage, ctx.hook_path, expected, {})
    monkeypatch.setattr(cli, "_replace_file", real_replace)
    _stub_target(monkeypatch, cli)

    def tree_state():
        return {
            path.name: (path.read_bytes(), os.lstat(path).st_ino, os.lstat(path).st_nlink)
            for path in sorted(ctx.hook_path.parent.iterdir())
            if path.is_file()
        }

    before = tree_state()
    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)

    assert exit_code != 0
    assert report["status"] == "failed"
    assert report["verify"]["failure_stage"] == "recovery"
    assert tree_state() == before


def test_package_rollback_rechecks_owner_after_final_snapshot_before_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines([])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    concurrent = cli._package_state_from_lines(["dcc-mcp-3dsmax @ file:///concurrent/dcc_mcp_3dsmax-0.2.2.whl"])
    state = {"package": owned}
    commands = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])

    def replace_after_final_snapshot(_ctx, _mutex) -> None:
        state["package"] = concurrent

    monkeypatch.setattr(cli, "_package_rollback_commit_hook", replace_after_final_snapshot, raising=False)

    def run_pip(_ctx, command):
        commands.append(command)
        state["package"] = prior

    _stub_owned_pip(monkeypatch, cli, run_pip)
    with pytest.raises(cli.LifecycleError):
        cli._restore_package_state(ctx, prior, owned)

    assert commands == []
    assert state["package"] == concurrent


def test_install_rollback_preserves_external_package_upgrade_not_in_mutation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines(["unrelated==1.0"])
    installed = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "unrelated==2.0"])
    external_only = cli._package_state_from_lines(["unrelated==2.0"])
    state = {"package": prior}
    commands = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])

    def install_with_external_upgrade(_ctx, _source, _mutex):
        state["package"] = installed
        return {
            "dcc-mcp-3dsmax": {
                "requirement": "dcc-mcp-3dsmax==0.2.2",
                "fingerprint": installed["fingerprints"]["dcc-mcp-3dsmax"],
                "provenance": "test-report",
            }
        }

    monkeypatch.setattr(cli, "_install_package", install_with_external_upgrade)
    monkeypatch.setattr(
        cli,
        "_probe_target",
        lambda _path: (_ for _ in ()).throw(cli.LifecycleError(20, "acquire", "target_probe_failed")),
    )

    def run_pip(_ctx, command):
        commands.append(command)
        if command[:2] == ["uninstall", "-y"]:
            state["package"] = external_only
        else:
            state["package"] = prior

    _stub_owned_pip(monkeypatch, cli, run_pip)
    ownership = (cli._snapshot(ctx.hook_path), cli._snapshot(ctx.receipt_path))

    with pytest.raises(cli.LifecycleError) as captured:
        cli._install_transaction(ctx, "pypi", ownership)

    assert captured.value.reason == "target_probe_failed"
    assert commands == [["uninstall", "-y", "dcc-mcp-3dsmax"]]
    assert state["package"] == external_only


def test_reconcile_recovers_real_exit_before_journal_publication_without_dry_run_mutation(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True, exist_ok=True)
    script = r"""
import os, sys
from pathlib import Path
from dcc_mcp_3dsmax import install_cli as cli
destination = Path(sys.argv[1])
stage = destination.with_name(".%s.stage-pre-link" % destination.name)
destination.write_bytes(b"previous")
stage.write_bytes(b"transaction")
expected = cli._snapshot(destination)
desired = cli._snapshot(stage)
real_link = os.link
def crash(source, target, *args, **kwargs):
    if ".transaction-" in Path(target).name:
        os._exit(93)
    return real_link(source, target, *args, **kwargs)
os.link = crash
cli._write_file_transaction(destination, expected, desired, stage)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run([sys.executable, "-c", script, str(ctx.hook_path)], env=env)

    assert completed.returncode == 93
    assert not list(ctx.hook_path.parent.glob(".%s.transaction-*.json" % ctx.hook_path.name))
    assert len(list(ctx.hook_path.parent.glob(".%s.journal-stage-*" % ctx.hook_path.name))) == 1
    assert ctx.hook_path.read_bytes() == b"previous"

    def tree_state():
        return {
            path.name: (path.read_bytes(), os.lstat(path).st_ino, os.lstat(path).st_nlink)
            for path in sorted(ctx.hook_path.parent.iterdir())
            if path.is_file()
        }

    _stub_target(monkeypatch, cli)
    before = tree_state()
    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)
    assert exit_code != 0
    assert report["verify"]["failure_reason"] == "transaction_recovery_required"
    assert tree_state() == before

    cli._reconcile_file_transaction(ctx.hook_path)
    assert ctx.hook_path.read_bytes() == b"previous"
    assert not list(ctx.hook_path.parent.glob(".%s.*" % ctx.hook_path.name))

    after_recovery = tree_state()
    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)
    assert exit_code == 0
    assert report["status"] == "planned"
    assert tree_state() == after_recovery


def test_reconcile_recovers_real_exit_before_commit_marker_publication_without_dry_run_mutation(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    ctx.hook_path.parent.mkdir(parents=True, exist_ok=True)
    script = r"""
import os, sys
from pathlib import Path
from dcc_mcp_3dsmax import install_cli as cli
destination = Path(sys.argv[1])
stage = destination.with_name(".%s.stage-pre-marker" % destination.name)
destination.write_bytes(b"previous")
stage.write_bytes(b"transaction")
expected = cli._snapshot(destination)
real_link = os.link
def crash(source, target, *args, **kwargs):
    if ".committed-" in Path(target).name:
        os._exit(94)
    return real_link(source, target, *args, **kwargs)
os.link = crash
cli._replace_file_if_snapshot(stage, destination, expected, {})
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run([sys.executable, "-c", script, str(ctx.hook_path)], env=env)

    assert completed.returncode == 94
    assert ctx.hook_path.read_bytes() == b"transaction"
    assert not list(ctx.hook_path.parent.glob(".%s.committed-*.json" % ctx.hook_path.name))
    assert len(list(ctx.hook_path.parent.glob(".%s.committed-*.json.stage" % ctx.hook_path.name))) == 1

    def tree_state():
        return {
            path.name: (path.read_bytes(), os.lstat(path).st_ino, os.lstat(path).st_nlink)
            for path in sorted(ctx.hook_path.parent.iterdir())
            if path.is_file()
        }

    _stub_target(monkeypatch, cli)
    before = tree_state()
    exit_code = cli.main(_args(layout, "install", "--dry-run"))
    report = _report(cli, capsys)
    assert exit_code != 0
    assert report["verify"]["failure_reason"] == "transaction_recovery_required"
    assert tree_state() == before

    cli._reconcile_file_transaction(ctx.hook_path)
    assert ctx.hook_path.read_bytes() == b"transaction"
    assert os.lstat(ctx.hook_path).st_nlink == 1
    assert not list(ctx.hook_path.parent.glob(".%s.*" % ctx.hook_path.name))


def test_package_rollback_rejects_same_name_contender_at_pip_mutation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    args = cli.build_parser().parse_args(_args(layout, "install", "--yes"))
    ctx = cli._context(args)
    prior = cli._package_state_from_lines([])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    concurrent = cli._package_state_from_lines(["dcc-mcp-3dsmax @ file:///concurrent/dcc_mcp_3dsmax-0.2.2.whl"])
    state = {"package": owned}
    pip_calls = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])

    def contender_wins(_ctx, _mutex, _token) -> None:
        state["package"] = concurrent

    monkeypatch.setattr(cli, "_package_pip_commit_hook", contender_wins, raising=False)

    _stub_owned_pip(monkeypatch, cli, lambda _ctx, command: pip_calls.append(command))

    with pytest.raises(cli.LifecycleError) as captured:
        cli._restore_package_state(ctx, prior, owned)

    assert captured.value.reason == "package_rollback_incomplete"
    assert pip_calls == []
    assert state["package"] == concurrent


def test_package_rollback_rejects_contender_after_token_read_before_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    prior = cli._package_state_from_lines([])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    contender = cli._package_state_from_lines(["dcc-mcp-3dsmax @ file:///concurrent/dcc_mcp_3dsmax-0.2.2.whl"])
    state = {"package": owned}
    pip_calls = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])
    real_read = cli._read_package_commit

    def read_token_then_contender_wins(path):
        record = real_read(path)
        state["package"] = contender
        return record

    monkeypatch.setattr(cli, "_read_package_commit", read_token_then_contender_wins)

    def run_pip(_ctx, command):
        pip_calls.append(command)
        state["package"] = prior

    _stub_owned_pip(monkeypatch, cli, run_pip)

    with pytest.raises(cli.LifecycleError):
        cli._restore_package_state(ctx, prior, owned)

    assert pip_calls == []
    assert state["package"] == contender


def test_target_worker_revalidates_exact_package_owner_before_pip(tmp_path: Path) -> None:
    cli = _install_cli()
    ctx = SimpleNamespace(python_path=Path(sys.executable))
    with cli._package_ownership(ctx) as mutex:
        current = cli._snapshot_package_state(ctx)["fingerprints"]
        stale = dict(current)
        stale["dcc-mcp-review-contender"] = "different-owner"
        token, token_snapshot = cli._write_package_commit_token(ctx, mutex, stale, stale)
        token_path = cli._package_commit_path(mutex)
        try:
            with pytest.raises(RuntimeError, match="target package mutation failed"):
                cli._run_target_owned_pip_command(
                    ctx,
                    ["list", "--disable-pip-version-check", "--format", "freeze"],
                    token_path,
                    token,
                )
        finally:
            cli._remove_owned_file(token_path, token_snapshot)


def test_target_worker_keeps_owner_token_through_actual_pip_boundary(tmp_path: Path) -> None:
    cli = _install_cli()
    ctx = SimpleNamespace(python_path=Path(sys.executable))
    with cli._package_ownership(ctx) as mutex:
        current = cli._snapshot_package_state(ctx)["fingerprints"]
        token, token_snapshot = cli._write_package_commit_token(ctx, mutex, current, current)
        token_path = cli._package_commit_path(mutex)
        try:
            cli._run_target_owned_pip_command(
                ctx,
                ["list", "--disable-pip-version-check", "--format", "freeze"],
                token_path,
                token,
            )
            cli._require_snapshot_current(token_path, token_snapshot)
        finally:
            cli._remove_owned_file(token_path, token_snapshot)


def test_install_worker_captures_first_full_fingerprint_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _install_cli()
    ctx = SimpleNamespace(python_path=Path(sys.executable))
    evidence = {
        "dcc-mcp-3dsmax": {
            "requirement": "dcc-mcp-3dsmax==0.2.2",
            "provenance": '{"archive_info":{},"url":"https://example.invalid/adapter.whl"}',
            "fingerprint": "worker-captured-distribution-fingerprint",
        }
    }

    def run_worker(command, **_kwargs):
        if "--report" in command:
            report_path = Path(command[command.index("--report") + 1])
            report_path.write_text(
                json.dumps(
                    {
                        "install": [
                            {
                                "metadata": {"name": "dcc-mcp-3dsmax", "version": "0.2.2"},
                                "download_info": {
                                    "url": "https://example.invalid/adapter.whl",
                                    "archive_info": {},
                                },
                                "is_direct": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="DCC_MCP_INSTALL_EVIDENCE=" + json.dumps({"evidence": evidence}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", run_worker)
    monkeypatch.setattr(cli, "_require_package_mutex_identity", lambda _ctx, _mutex: None)
    monkeypatch.setattr(
        cli,
        "_snapshot_package_state",
        lambda _ctx: (_ for _ in ()).throw(AssertionError("parent first-capture gap")),
    )

    with cli._package_ownership(ctx) as mutex:
        assert cli._install_package(ctx, "pypi", mutex) == evidence


def test_install_rejects_target_interpreter_replacement_after_mutex_acquire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    ownership = (cli._snapshot(ctx.hook_path), cli._snapshot(ctx.receipt_path))
    empty = cli._package_state_from_lines([])
    pip_worker_calls = []

    with cli._package_ownership(ctx) as mutex:
        replacement = layout["python"].with_name("replacement-python.exe")
        replacement.write_bytes(b"independent-interpreter")
        os.replace(str(replacement), str(layout["python"]))

        def run_worker(command, **_kwargs):
            if len(command) > 2 and "DCC_MCP_INSTALL_EVIDENCE" in command[2]:
                pip_worker_calls.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='DCC_MCP_INSTALL_EVIDENCE={"evidence":{}}\n',
                stderr="",
            )

        monkeypatch.setattr(cli.subprocess, "run", run_worker)
        monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: empty)
        monkeypatch.setattr(
            cli,
            "_probe_target",
            lambda _path: {
                "python_version": "3.11.9",
                "adapter_version": cli.ADAPTER_VERSION,
                "core_version": cli.MIN_CORE_VERSION,
            },
        )

        with pytest.raises(cli.LifecycleError) as captured:
            cli._install_transaction_locked(ctx, "pypi", ownership, mutex)

    assert captured.value.reason == "transaction_rollback_incomplete"
    assert pip_worker_calls == []


def test_install_worker_receives_mutex_identity_token_through_pip_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    observed = {}

    with cli._package_ownership(ctx) as mutex:

        def run_worker(command, **_kwargs):
            code = command[2] if len(command) > 2 else ""
            if "DCC_MCP_INSTALL_EVIDENCE" not in code:
                return subprocess.CompletedProcess(command, 0, stdout="not-json\n", stderr="")
            observed["command"] = command
            token_path = Path(command[3])
            record = cli._read_package_operation(token_path)
            observed["record"] = record
            observed["snapshot"] = cli._snapshot(token_path)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='DCC_MCP_INSTALL_EVIDENCE={"evidence":{}}\n',
                stderr="",
            )

        monkeypatch.setattr(cli.subprocess, "run", run_worker)
        assert cli._install_package(ctx, "pypi", mutex) == {}

    assert len(observed["command"]) == 6
    assert observed["record"]["token"] == observed["command"][4]
    assert observed["record"]["package_identity"] == mutex.package_identity
    assert observed["snapshot"].independent is True


def test_uninstall_worker_receives_mutex_identity_token_through_pip_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "uninstall", "--yes")))
    observed = {}

    with cli._package_ownership(ctx) as mutex:

        def run_worker(command, **_kwargs):
            code = command[2] if len(command) > 2 and command[1] == "-c" else ""
            if "DCC_MCP_UNINSTALL_EVIDENCE" not in code:
                return subprocess.CompletedProcess(command, 0, stdout="not-json\n", stderr="")
            observed["command"] = command
            token_path = Path(command[3])
            observed["record"] = cli._read_package_operation(token_path)
            observed["snapshot"] = cli._snapshot(token_path)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='DCC_MCP_UNINSTALL_EVIDENCE={"before":{},"after":{}}\n',
                stderr="",
            )

        monkeypatch.setattr(cli.subprocess, "run", run_worker)
        monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: cli._package_state_from_lines([]))
        assert cli._uninstall_package(ctx, mutex, {}) == {"before": {}, "after": {}}

    assert len(observed["command"]) == 5
    assert observed["record"]["token"] == observed["command"][4]
    assert observed["record"]["package_identity"] == mutex.package_identity
    assert observed["record"]["before"] == {}
    assert observed["snapshot"].independent is True


def test_uninstall_rejects_target_interpreter_replacement_after_mutex_acquire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "uninstall", "--yes")))
    pip_worker_calls = []

    with cli._package_ownership(ctx) as mutex:
        replacement = layout["python"].with_name("replacement-python.exe")
        replacement.write_bytes(b"independent-interpreter")
        os.replace(str(replacement), str(layout["python"]))

        def run_worker(command, **_kwargs):
            if len(command) > 2 and "DCC_MCP_UNINSTALL_EVIDENCE" in command[2]:
                pip_worker_calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="not-json\n", stderr="")

        monkeypatch.setattr(cli.subprocess, "run", run_worker)
        with pytest.raises(cli.LifecycleError) as captured:
            cli._uninstall_package(ctx, mutex, {})

    assert captured.value.reason == "package_install_failed"
    assert pip_worker_calls == []


def test_package_fingerprint_changes_when_dist_info_is_physically_replaced(
    tmp_path: Path,
) -> None:
    cli = _install_cli()
    ctx, dist_info, _payload = _isolated_distribution(cli, tmp_path)

    before = cli._snapshot_package_state(ctx)["fingerprints"]["review-contender"]
    replacement = dist_info.parent / "replacement.dist-info"
    retired = tmp_path / "retired.dist-info"
    shutil.copytree(str(dist_info), str(replacement))
    dist_info.rename(retired)
    replacement.rename(dist_info)
    after = cli._snapshot_package_state(ctx)["fingerprints"]["review-contender"]

    assert after != before


def test_package_fingerprint_changes_when_record_payload_is_physically_replaced(
    tmp_path: Path,
) -> None:
    cli = _install_cli()
    ctx, _dist_info, payload = _isolated_distribution(cli, tmp_path)

    before = cli._snapshot_package_state(ctx)["fingerprints"]["review-contender"]
    replacement = payload.with_name("replacement.py")
    replacement.write_bytes(payload.read_bytes())
    payload.rename(tmp_path / "retired-payload.py")
    replacement.rename(payload)
    after = cli._snapshot_package_state(ctx)["fingerprints"]["review-contender"]

    assert after != before


def test_package_rollback_preserves_physically_replaced_same_content_contender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    ctx, _dist_info, payload = _isolated_distribution(cli, tmp_path)
    prior = cli._package_state_from_lines([])
    prior["_ownership_required"] = True
    transaction_owned = cli._snapshot_package_state(ctx)
    replacement = payload.with_name("replacement.py")
    replacement.write_bytes(payload.read_bytes())
    payload.rename(tmp_path / "transaction-payload.py")
    replacement.rename(payload)
    contender = cli._snapshot_package_state(ctx)
    pip_calls = []
    monkeypatch.setattr(
        cli,
        "_run_target_owned_pip_command",
        lambda _ctx, command, _path, _token: pip_calls.append(command),
    )

    cli._restore_package_state(ctx, prior, transaction_owned)

    assert pip_calls == []
    assert payload.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert cli._snapshot_package_state(ctx)["fingerprints"] == contender["fingerprints"]


def test_install_does_not_claim_same_name_contender_after_package_install_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    prior = cli._package_state_from_lines([])
    installed = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    contender = {
        "requirements": installed["requirements"],
        "by_name": dict(installed["by_name"]),
        "fingerprints": dict(installed["fingerprints"]),
        "sha256": installed["sha256"],
    }
    contender["fingerprints"]["dcc-mcp-3dsmax"] = "concurrent-dist-record-fingerprint"
    contender["sha256"] = "concurrent-state-fingerprint"
    state = {"package": prior}
    pip_calls = []
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])

    def install_then_contender_wins(_ctx, _source, _mutex):
        state["package"] = contender
        return {
            "dcc-mcp-3dsmax": {
                "requirement": installed["by_name"]["dcc-mcp-3dsmax"],
                "fingerprint": installed["fingerprints"]["dcc-mcp-3dsmax"],
                "provenance": '{"archive_info":{},"url":"https://example.invalid/adapter.whl"}',
            }
        }

    monkeypatch.setattr(cli, "_install_package", install_then_contender_wins)
    monkeypatch.setattr(
        cli,
        "_probe_target",
        lambda _path: (_ for _ in ()).throw(
            cli.LifecycleError(cli.INSTALL_EXIT_ACQUIRE, "acquire", "target_probe_failed")
        ),
    )

    def run_pip(_ctx, command):
        pip_calls.append(command)
        state["package"] = prior

    _stub_owned_pip(monkeypatch, cli, run_pip)
    ownership = (cli._snapshot(ctx.hook_path), cli._snapshot(ctx.receipt_path))

    with pytest.raises(cli.LifecycleError) as captured:
        cli._install_transaction(ctx, "pypi", ownership)

    assert captured.value.reason in {"package_rollback_incomplete", "target_probe_failed"}
    assert pip_calls == []
    assert state["package"] == contender


def test_uninstall_does_not_claim_same_name_contender_after_package_worker_returns(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    _install_for_verify(cli, layout, capsys, monkeypatch)
    installed = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2"])
    removed = cli._package_state_from_lines([])
    contender = {
        "requirements": installed["requirements"],
        "by_name": dict(installed["by_name"]),
        "fingerprints": dict(installed["fingerprints"]),
        "sha256": installed["sha256"],
    }
    contender["fingerprints"]["dcc-mcp-3dsmax"] = "concurrent-physical-distribution-fingerprint"
    contender["sha256"] = "concurrent-state-fingerprint"
    state = {"package": installed}
    pip_calls = []
    hook_path = layout["startup"] / cli.STARTUP_SCRIPT_NAME
    hook_before = hook_path.read_bytes()
    receipt_before = layout["receipt"].read_bytes()
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])

    def uninstall_then_contender_wins(_ctx, _mutex, expected_before):
        assert expected_before == installed["fingerprints"]
        state["package"] = contender
        return {"before": installed["fingerprints"], "after": removed["fingerprints"]}

    monkeypatch.setattr(cli, "_uninstall_package", uninstall_then_contender_wins)
    monkeypatch.setattr(
        cli,
        "_restore_if_snapshot",
        lambda *_args: (_ for _ in ()).throw(OSError("force rollback after package mutation")),
    )
    monkeypatch.setattr(
        cli,
        "_run_target_owned_pip_command",
        lambda _ctx, command, _path, _token: pip_calls.append(command),
    )

    exit_code = cli.main(_args(layout, "uninstall", "--yes"))
    report = _report(cli, capsys)

    assert exit_code != cli.INSTALL_EXIT_OK
    assert report["verify"]["failure_reason"] == "transaction_rollback_incomplete"
    assert pip_calls == []
    assert state["package"] == contender
    assert hook_path.read_bytes() == hook_before
    assert layout["receipt"].read_bytes() == receipt_before


def test_package_mutex_key_uses_physical_interpreter_identity_for_hardlink_alias(tmp_path: Path) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    alias = layout["python"].with_name("python-alias.exe")
    os.link(str(layout["python"]), str(alias))
    first = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    alias_layout = dict(layout)
    alias_layout["python"] = alias
    second = cli._context(cli.build_parser().parse_args(_args(alias_layout, "install", "--yes")))

    assert cli._package_mutex_path(first) == cli._package_mutex_path(second)


def test_package_mutex_distinguishes_independent_interpreter_identity(tmp_path: Path) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    independent = layout["python"].with_name("independent-python.exe")
    independent.write_bytes(layout["python"].read_bytes())
    first = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    independent_layout = dict(layout)
    independent_layout["python"] = independent
    second = cli._context(cli.build_parser().parse_args(_args(independent_layout, "install", "--yes")))

    assert cli._package_mutex_path(first) != cli._package_mutex_path(second)


@pytest.mark.skipif(os.name != "nt", reason="Windows hardlink/mutex contract")
@pytest.mark.parametrize("alias_kind", ["hardlink", "junction", "case"])
def test_package_mutex_excludes_physical_interpreter_alias_in_second_process(tmp_path: Path, alias_kind: str) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    if alias_kind == "hardlink":
        alias_dir = tmp_path / "alias-environment"
        alias_dir.mkdir()
        alias = alias_dir / "python.exe"
        os.link(str(layout["python"]), str(alias))
    elif alias_kind == "junction":
        alias_dir = tmp_path / "junction-environment"
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias_dir), str(layout["python"].parent)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode == 0
        alias = alias_dir / "python.exe"
    else:
        alias = Path(str(layout["python"]).swapcase())
        assert alias.exists()
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    script = r"""
import sys
from pathlib import Path
from types import SimpleNamespace
from dcc_mcp_3dsmax import install_cli as cli
ctx = SimpleNamespace(python_path=Path(sys.argv[1]))
try:
    mutex = cli._acquire_package_mutex(ctx, timeout=0.1)
except cli.LifecycleError as exc:
    raise SystemExit(23 if exc.reason == "package_ownership_locked" else 24)
else:
    mutex.release()
    raise SystemExit(0)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    with cli._package_ownership(ctx):
        blocked = subprocess.run([sys.executable, "-c", script, str(alias)], env=env)

    assert blocked.returncode == 23


def test_multi_command_package_rollback_crash_keeps_durable_outer_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    prior = cli._package_state_from_lines(["shared-dependency==1.0"])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "shared-dependency==2.0"])
    after_remove = cli._package_state_from_lines(["shared-dependency==2.0"])
    state = {"package": owned}
    monkeypatch.setattr(cli, "_snapshot_package_state", lambda _ctx: state["package"])

    def run_pip(_ctx, command):
        if command[:2] == ["uninstall", "-y"]:
            state["package"] = after_remove
        else:
            state["package"] = prior

    _stub_owned_pip(monkeypatch, cli, run_pip)
    real_owned_pip = cli._run_owned_pip_command
    calls = {"count": 0}

    def crash_after_first_committed_command(*args, **kwargs):
        real_owned_pip(*args, **kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            raise KeyboardInterrupt("process exits after the first rollback command")

    monkeypatch.setattr(cli, "_run_owned_pip_command", crash_after_first_committed_command)

    with pytest.raises(KeyboardInterrupt):
        cli._restore_package_state(ctx, prior, owned)

    rejected = False
    restarted_mutex = None
    try:
        restarted_mutex = cli._acquire_package_mutex(ctx)
    except cli.LifecycleError:
        rejected = True
    finally:
        if restarted_mutex is not None:
            restarted_mutex.release()

    assert rejected or state["package"] == prior


def test_package_mutex_excludes_hardlink_alias_in_second_process(tmp_path: Path) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    alias = layout["python"].with_name("python-process-alias.exe")
    os.link(str(layout["python"]), str(alias))
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    script = r"""
import sys
from pathlib import Path
from types import SimpleNamespace
from dcc_mcp_3dsmax import install_cli as cli
ctx = SimpleNamespace(python_path=Path(sys.argv[1]))
try:
    mutex = cli._acquire_package_mutex(ctx, timeout=0.1)
except cli.LifecycleError as exc:
    raise SystemExit(23 if exc.reason == "package_ownership_locked" else 24)
else:
    mutex.release()
    raise SystemExit(0)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    with cli._package_ownership(ctx):
        blocked = subprocess.run([sys.executable, "-c", script, str(alias)], env=env)

    assert blocked.returncode == 23


def test_real_exit_between_package_rollback_commands_automatically_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    state_path = tmp_path / "package-state.txt"
    state_path.write_text("owned", encoding="ascii")
    script = r"""
import os, sys
from pathlib import Path
from types import SimpleNamespace
from dcc_mcp_3dsmax import install_cli as cli
python_path = Path(sys.argv[1])
state_path = Path(sys.argv[2])
ctx = SimpleNamespace(python_path=python_path)
prior = cli._package_state_from_lines(["shared-dependency==1.0"])
owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "shared-dependency==2.0"])
after_remove = cli._package_state_from_lines(["shared-dependency==2.0"])
states = {"owned": owned, "after_remove": after_remove, "prior": prior}
cli._snapshot_package_state = lambda _ctx: states[state_path.read_text(encoding="ascii")]
def run_pip(_ctx, command):
    state_path.write_text("after_remove" if command[:2] == ["uninstall", "-y"] else "prior", encoding="ascii")
cli._run_target_owned_pip_command = lambda ctx, command, _path, _token: run_pip(ctx, command)
real_owned = cli._run_owned_pip_command
def crash_after_first(*args, **kwargs):
    real_owned(*args, **kwargs)
    os._exit(95)
cli._run_owned_pip_command = crash_after_first
cli._restore_package_state(ctx, prior, owned)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run([sys.executable, "-c", script, str(ctx.python_path), str(state_path)], env=env)
    assert completed.returncode == 95
    assert state_path.read_text(encoding="ascii") == "after_remove"

    prior = cli._package_state_from_lines(["shared-dependency==1.0"])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "shared-dependency==2.0"])
    after_remove = cli._package_state_from_lines(["shared-dependency==2.0"])
    states = {"owned": owned, "after_remove": after_remove, "prior": prior}
    monkeypatch.setattr(
        cli,
        "_snapshot_package_state",
        lambda _ctx: states[state_path.read_text(encoding="ascii")],
    )

    def resume_pip(_ctx, command):
        assert command[:3] == ["install", "--no-deps", "--force-reinstall"]
        state_path.write_text("prior", encoding="ascii")

    _stub_owned_pip(monkeypatch, cli, resume_pip)
    recovered = cli._acquire_package_mutex(ctx)
    recovered.release()
    assert state_path.read_text(encoding="ascii") == "prior"


@pytest.mark.parametrize(
    ("mode", "exit_code", "crashed_state"),
    [("before-command", 94, "owned"), ("after-progress", 96, "after_remove")],
)
def test_real_exit_at_each_package_rollback_step_boundary_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    exit_code: int,
    crashed_state: str,
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    state_path = tmp_path / "package-boundary-state.txt"
    state_path.write_text("owned", encoding="ascii")
    script = r"""
import os, sys
from pathlib import Path
from types import SimpleNamespace
from dcc_mcp_3dsmax import install_cli as cli
mode = sys.argv[1]
ctx = SimpleNamespace(python_path=Path(sys.argv[2]))
state_path = Path(sys.argv[3])
prior = cli._package_state_from_lines(["shared-dependency==1.0"])
owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "shared-dependency==2.0"])
after_remove = cli._package_state_from_lines(["shared-dependency==2.0"])
states = {"owned": owned, "after_remove": after_remove, "prior": prior}
cli._snapshot_package_state = lambda _ctx: states[state_path.read_text(encoding="ascii")]
def mutate(_ctx, command, _path, _token):
    state_path.write_text("after_remove" if command[:2] == ["uninstall", "-y"] else "prior", encoding="ascii")
cli._run_target_owned_pip_command = mutate
if mode == "before-command":
    cli._run_owned_pip_command = lambda *_args, **_kwargs: os._exit(94)
else:
    real_progress = cli._write_package_rollback_progress
    def crash_after_progress(*args, **kwargs):
        real_progress(*args, **kwargs)
        os._exit(96)
    cli._write_package_rollback_progress = crash_after_progress
cli._restore_package_state(ctx, prior, owned)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script, mode, str(ctx.python_path), str(state_path)],
        env=env,
        check=False,
    )
    assert completed.returncode == exit_code
    assert state_path.read_text(encoding="ascii") == crashed_state

    prior = cli._package_state_from_lines(["shared-dependency==1.0"])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "shared-dependency==2.0"])
    after_remove = cli._package_state_from_lines(["shared-dependency==2.0"])
    states = {"owned": owned, "after_remove": after_remove, "prior": prior}
    monkeypatch.setattr(
        cli,
        "_snapshot_package_state",
        lambda _ctx: states[state_path.read_text(encoding="ascii")],
    )

    def resume_pip(_ctx, command):
        state_path.write_text("after_remove" if command[:2] == ["uninstall", "-y"] else "prior", encoding="ascii")

    _stub_owned_pip(monkeypatch, cli, resume_pip)
    recovered = cli._acquire_package_mutex(ctx)
    recovered.release()
    assert state_path.read_text(encoding="ascii") == "prior"


@pytest.mark.parametrize(
    ("crash_write", "exit_code", "crashed_state"),
    [(2, 97, "owned"), (3, 98, "after_remove")],
)
def test_real_exit_before_rollback_record_publication_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_write: int,
    exit_code: int,
    crashed_state: str,
) -> None:
    cli = _install_cli()
    layout = _layout(tmp_path)
    ctx = cli._context(cli.build_parser().parse_args(_args(layout, "install", "--yes")))
    state_path = tmp_path / "package-publication-state.txt"
    state_path.write_text("owned", encoding="ascii")
    script = r"""
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from dcc_mcp_3dsmax import install_cli as cli
crash_write = int(sys.argv[1])
ctx = SimpleNamespace(python_path=Path(sys.argv[2]))
state_path = Path(sys.argv[3])
prior = cli._package_state_from_lines(["shared-dependency==1.0"])
owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "shared-dependency==2.0"])
after_remove = cli._package_state_from_lines(["shared-dependency==2.0"])
states = {"owned": owned, "after_remove": after_remove, "prior": prior}
cli._snapshot_package_state = lambda _ctx: states[state_path.read_text(encoding="ascii")]
def mutate(_ctx, command, _path, _token):
    state_path.write_text("after_remove" if command[:2] == ["uninstall", "-y"] else "prior", encoding="ascii")
cli._run_target_owned_pip_command = mutate
real_write = cli._write_private_json
calls = {"count": 0}
def crash_before_publication(path, record):
    calls["count"] += 1
    if calls["count"] != crash_write:
        return real_write(path, record)
    stage = path.with_name(path.name + ".stage-crash")
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with stage.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os._exit(95 + crash_write)
cli._write_private_json = crash_before_publication
cli._restore_package_state(ctx, prior, owned)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script, str(crash_write), str(ctx.python_path), str(state_path)],
        env=env,
        check=False,
    )
    assert completed.returncode == exit_code
    assert state_path.read_text(encoding="ascii") == crashed_state

    prior = cli._package_state_from_lines(["shared-dependency==1.0"])
    owned = cli._package_state_from_lines(["dcc-mcp-3dsmax==0.2.2", "shared-dependency==2.0"])
    after_remove = cli._package_state_from_lines(["shared-dependency==2.0"])
    states = {"owned": owned, "after_remove": after_remove, "prior": prior}
    monkeypatch.setattr(
        cli,
        "_snapshot_package_state",
        lambda _ctx: states[state_path.read_text(encoding="ascii")],
    )

    def resume_pip(_ctx, command):
        state_path.write_text("after_remove" if command[:2] == ["uninstall", "-y"] else "prior", encoding="ascii")

    _stub_owned_pip(monkeypatch, cli, resume_pip)
    recovered = cli._acquire_package_mutex(ctx)
    recovered.release()
    assert state_path.read_text(encoding="ascii") == "prior"
