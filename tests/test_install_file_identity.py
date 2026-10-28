"""File ownership must survive a lifecycle interpreter change, not a file swap."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dcc_mcp_3dsmax import install_cli as cli

WORKER = r"""
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[3])
from dcc_mcp_3dsmax import install_cli as cli
root = Path(sys.argv[2])
hook, receipt, previous = (root / name for name in ('hook.ms', 'receipt.json', 'previous.ms'))
if sys.argv[1] == 'write':
    previous.write_bytes(b'previous hook')
    stage = root / 'hook.stage'
    stage.write_bytes(b'candidate hook')
    ctx = SimpleNamespace(host_path=root, host_version='2024', python_path=Path(sys.executable),
        core_version=cli.MIN_CORE_VERSION, hook_path=hook, receipt_path=receipt)
    target = {'python_version': sys.version.split()[0], 'core_version': cli.MIN_CORE_VERSION}
    value = cli._receipt(ctx, stage, None, cli._snapshot(previous), target)
    receipt_stage = root / 'receipt.stage'
    cli._write_staged_receipt(receipt_stage, value)
    committed = {}
    cli._replace_file_if_snapshot(stage, hook, cli._snapshot(hook), committed)
    cli._replace_file_if_snapshot(receipt_stage, receipt, cli._snapshot(receipt), committed)
    snapshots = {p.name: cli._snapshot_record(cli._snapshot(p)) for p in (hook, receipt, previous)}
    (root / 'snapshots.json').write_text(json.dumps(snapshots), encoding='utf-8')
elif sys.argv[1] == 'prepare':
    hook.write_bytes(b'previous hook')
    stage = root / 'candidate.stage'
    stage.write_bytes(b'candidate hook')
    expected, desired = cli._snapshot(hook), cli._snapshot(stage)
    journal, _, claim, _, _, _ = cli._write_file_transaction(hook, expected, desired, stage)
    cli._claim_file_if_snapshot(hook, expected, claim)
elif sys.argv[1] == 'recover':
    cli._reconcile_file_transaction(hook)
    assert hook.read_bytes() == b'previous hook'
    assert not list(root.glob('.hook.ms.*'))
else:
    assert cli._inspect_state(receipt, hook) == ('current', None, None)
    value = cli._read_receipt(receipt, required=True)
    assert cli._identity_record_matches(value['previous_hook']['identity'], cli._file_identity(previous))
    snapshots = json.loads((root / 'snapshots.json').read_text(encoding='utf-8'))
    for name, snapshot in snapshots.items():
        assert cli._snapshot_is_current(root / name, cli._snapshot_from_record(snapshot))
print(json.dumps({'python': sys.version.split()[0], 'operation': sys.argv[1], 'success': True}))
"""


def _python(label):
    if label == "current":
        return sys.executable
    other = os.environ.get("DCC_MCP_TEST_OTHER_PYTHON")
    if not other:
        pytest.skip("Set DCC_MCP_TEST_OTHER_PYTHON for real cross-interpreter coverage")
    assert Path(other).is_file()
    return other


def _worker(python, operation, root):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [python, "-B", "-c", WORKER, operation, str(root), str(Path(cli.__file__).resolve().parent.parent)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["success"] is True
    return result


@pytest.mark.parametrize("writer,reader", [("current", "current"), ("current", "other"), ("other", "current")])
def test_receipt_hook_previous_hook_and_snapshots_survive_interpreter_change(tmp_path, writer, reader):
    writer_python, reader_python = _python(writer), _python(reader)
    before = _worker(writer_python, "write", tmp_path)
    payloads = {p: p.read_bytes() for p in tmp_path.iterdir()}
    after = _worker(reader_python, "read", tmp_path)
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == payloads
    if writer != reader:
        assert before["python"] != after["python"]


@pytest.mark.parametrize("writer,reader", [("current", "other"), ("other", "current")])
def test_interrupted_file_transaction_recovers_in_other_interpreter(tmp_path, writer, reader):
    writer_python, reader_python = _python(writer), _python(reader)
    _worker(writer_python, "prepare", tmp_path)
    _worker(reader_python, "recover", tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows versioned native identity contract")
@pytest.mark.parametrize("target", ["receipt", "hook", "snapshot"])
def test_legacy_stat_identity_is_rejected_without_rewriting_files(tmp_path, target):
    _worker(sys.executable, "write", tmp_path)
    receipt = tmp_path / "receipt.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    if target == "snapshot":
        snapshot = cli._snapshot_record(cli._snapshot(tmp_path / "hook.ms"))
        snapshot["identity"].pop("scheme")
        with pytest.raises(ValueError, match="invalid transaction snapshot"):
            cli._snapshot_from_record(snapshot)
        return
    identity = value["receipt_identity"] if target == "receipt" else value["artifacts"][0]["identity"]
    identity.pop("scheme")
    receipt.write_text(json.dumps(value), encoding="utf-8")
    before = receipt.read_bytes()
    state, stage, reason = cli._inspect_state(receipt, tmp_path / "hook.ms")
    assert state == "partial"
    assert (stage, reason) == (
        ("receipt", "receipt_ownership_invalid")
        if target == "receipt"
        else ("artifact", "startup_hook_missing_or_modified")
    )
    assert receipt.read_bytes() == before


@pytest.mark.parametrize("boundary", ["before_open", "after_read"])
def test_independent_read_rejects_same_bytes_replacement_at_read_boundaries(tmp_path, monkeypatch, boundary):
    path, replacement = tmp_path / "owned.ms", tmp_path / "replacement.ms"
    path.write_bytes(b"same bytes")
    replacement.write_bytes(path.read_bytes())
    if boundary == "before_open":
        real_open = Path.open

        def replace_before_open(target, *args, **kwargs):
            if target == path and replacement.exists():
                os.replace(replacement, path)
            return real_open(target, *args, **kwargs)

        monkeypatch.setattr(Path, "open", replace_before_open)
    else:
        real_identity = cli._file_identity
        fd_queries = []

        def changed_path_after_read(target):
            identity = real_identity(target)
            if isinstance(target, int):
                fd_queries.append(target)
            elif target == path and len(fd_queries) >= 2:
                return identity[0], identity[1] + 1
            return identity

        monkeypatch.setattr(cli, "_file_identity", changed_path_after_read)
    with pytest.raises(OSError, match="file identity changed"):
        cli._read_independent_file(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows native identity API")
def test_native_identity_query_failure_does_not_fall_back_to_stat(tmp_path, monkeypatch):
    import ctypes

    path = tmp_path / "owned.ms"
    path.write_bytes(b"owned")
    monkeypatch.setattr(ctypes.windll.kernel32, "GetFileInformationByHandleEx", lambda *args: 0)
    with pytest.raises(OSError):
        cli._read_independent_file(path)
