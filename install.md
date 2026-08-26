# Install dcc-mcp-3dsmax

This is the authoritative install runbook for the 3ds Max adapter. The
agent-first CLI emits the shared DCC-MCP Install SOP v1 report and owns only
the adapter package, startup hook, and receipt. It does not change the shared
Core catalog.

## Requirements

- **Platform:** Windows only. macOS and Linux are not supported because
  Autodesk 3ds Max is Windows-only.
- Autodesk 3ds Max 2017 or later with Python 3 and `pymxs`.
- The target `3dsmaxpy.exe` or embedded `Python\python.exe`.
- `dcc-mcp-core>=0.20.20` and `dcc-mcp-server>=0.20.20`. The lifecycle CLI
  installs these through the adapter dependency set and verifies the Core
  version in the target interpreter before reporting success.
- Permission to write the user's 3ds Max `userStartupScripts` directory.

Installing files is not proof that 3ds Max tools are callable. A directly
usable result requires a running host and a typed sidecar readiness probe.

## Supported versions

| 3ds Max | Embedded Python | Support |
| --- | --- | --- |
| 2022 | 3.7 | Supported through the Python 3.7 compatibility payload |
| 2023-2026 | 3.9+ | Supported |
| 2017-2021 | Python 3 with `pymxs` | Best effort; verify on the target host |

The repository tests lifecycle behavior without launching 3ds Max. A real
host is still required for final readiness and scene-tool validation.

## Agent quick path

Install the public package in the environment used to run the lifecycle CLI:

```powershell
python -m pip install --upgrade dcc-mcp-3dsmax
```

Then run the standard lifecycle command with explicit host and interpreter
paths. `--json` writes exactly one Install SOP v1 object to stdout.

```powershell
dcc-mcp-3dsmax install --json --yes `
  --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" `
  --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
```

Use `--dry-run` first when the target is unfamiliar. It executes the same
read-only host, interpreter, installed-Core, and receipt-ownership preflight as
the mutation path, then emits executable `next_steps[]` without installing a
package, hook, or receipt.

```powershell
dcc-mcp-3dsmax install --json --dry-run `
  --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" `
  --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
```

The standard exit codes are:

| Code | Meaning |
| ---: | --- |
| 0 | Completed or planned successfully |
| 10 | Preflight or ownership failure |
| 20 | Package acquisition failure |
| 30 | Transactional install or uninstall failure |
| 40 | Verification did not reach usable |
| 50 | Restart is required before retrying verification |

The CLI accepts `install`, `status`, `verify`, `uninstall`, and `upgrade`, plus
the common `--json`, `--yes`, `--dry-run`, `--dcc-path`, and `--python` flags.
Use `--startup-dir` only for a non-standard profile and `--receipt-path` only
for an explicitly managed receipt location.

## Manual path

The release MZP remains the 3ds Max drag-and-drop path. Download the immutable
`dcc-mcp-3dsmax-<version>-win64.mzp` release asset and drag it into the 3ds Max
viewport. The installer stages a versioned payload, changes `current.txt` only
after staging succeeds, installs the startup hook, and defers locked cleanup
until restart.

The MZP and lifecycle CLI are separate ownership domains. Do not use a CLI
receipt to remove an MZP payload. For repository development only, the legacy
setup helper remains available:

```powershell
python skills/dcc-mcp-3dsmax-setup/scripts/setup_dcc_mcp_3dsmax.py --source local
```

## Verify

After installation, close and reopen 3ds Max so the startup hook can run, then
verify from a shell:

```powershell
dcc-mcp-3dsmax status --json --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
dcc-mcp-3dsmax verify --json --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
```

Verification checks receipt ownership, independent regular-file identity, and
digests; symlink, reparse-point, and hardlink aliases fail closed even when the
bytes match. It imports the exact adapter and Core versions in the selected
interpreter, checks bootstrap-error records, and waits for the typed
`3dsmax_diagnostics__ping` sidecar probe. Only
`verify.directly_usable=true` proves the runtime is callable. A copied hook or
a live process alone is insufficient. Invalid or failed readiness results are
reported with bounded reason identities; probe exception text and values are
never copied into the public report.

No automated test in this repository claims to have opened a real 3ds Max
instance. Run the generated smoke prompt only after the operator starts the
licensed host.

## Upgrade

Plan and apply upgrades through the same receipt-owned transaction:

```powershell
dcc-mcp-3dsmax upgrade --json --dry-run --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
dcc-mcp-3dsmax upgrade --json --yes --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
```

The selected host, target Python, and any already-installed Core are checked
before pip, hook, or receipt mutation. The hook and receipt are staged before
commit. Before pip runs, the CLI records the exact frozen package provenance
and dependency state. A receipt owned by a different startup hook fails closed
and cannot supply `previous_hook` state for the selected target. If commit
fails, it restores the previous files and reads
back their exact presence, bytes, digests, and independent file identity. A
symlink, reparse point, hardlink alias, or same-bytes identity swap fails
closed. The CLI always attempts to reconcile the complete package snapshot and
verify its digest. Any file or package mismatch returns
`transaction_rollback_incomplete`; it is never reported as a successful
recovery. A Windows file lock returns exit 50 with a
restart-and-verify next step instead of deleting the live installation.

## Uninstall

Preview and then consume the owned receipt:

```powershell
dcc-mcp-3dsmax uninstall --json --dry-run --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
dcc-mcp-3dsmax uninstall --json --yes --dcc-path "C:\Program Files\Autodesk\3ds Max 2025" --python "C:\Program Files\Autodesk\3ds Max 2025\Python\python.exe"
```

Uninstall verifies the recorded path and digest, restores any startup hook
that existed before the first receipted install, removes the target package,
and consumes the receipt. It is idempotent when both the receipt and owned hook
are absent. It refuses to delete an unreceipted or modified hook.

Use the MZP dialog's Uninstall action for MZP-owned payloads. Its existing
out-of-tree uninstall marker completes locked cleanup on the next 3ds Max
startup.

## Troubleshooting

### `host_not_found`, `python_not_found`, or `startup_dir_not_found`

Pass the exact `--dcc-path`, `--python`, and, for a custom profile,
`--startup-dir`. The selected paths are recorded in the plan and receipt.

### `core_version_too_old`

Upgrade `dcc-mcp-3dsmax` in the selected target interpreter. Do not claim the
host is usable until verification sees Core 0.20.20 or newer.

### `package_rollback_incomplete` or `transaction_rollback_incomplete`

Stop further lifecycle mutations and preserve the one-object JSON report. The
previous package provenance, dependency set, hook, or receipt could not be
verified after recovery. Transaction rollback still attempts both file
restoration and package reconciliation before reporting an incomplete result;
matching bytes alone do not prove that a restored file has independent
ownership.
Repair the selected interpreter from a trusted exact artifact or environment
lock, then run `status --json` and `verify --json`.

### `receipt_missing`, `receipt_invalid`, or `startup_hook_missing_or_modified`

Do not delete files manually. Run `status --json`, preserve the report, and
use a receipted repair. Uninstall intentionally fails closed when ownership is
ambiguous.

### `windows_file_lock`

Close every 3ds Max process using the payload, restart 3ds Max if the report
requests it, and run the emitted verify command. Do not delete loaded native
files or the receipt by hand.

### `bootstrap_error`, `bootstrap_log_unavailable`, or readiness failure

Inspect the bounded bootstrap-error directory named in the receipt and the
gateway/sidecar diagnostics. Confirm the host is open and the startup hook ran.
The public MCP URL remains `http://127.0.0.1:9765/mcp`; the per-instance port
is intentionally ephemeral.
