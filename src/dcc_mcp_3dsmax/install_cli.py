"""Agent-first Install SOP v1 lifecycle for dcc-mcp-3dsmax.

The CLI owns only adapter installation state. Shared catalog routing and Core
orchestration remain in dcc-mcp-core.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .__version__ import __version__ as ADAPTER_VERSION

DCC_TYPE = "3dsmax"
COMMAND = "dcc-mcp-3dsmax"
STARTUP_SCRIPT_NAME = "dcc_mcp_3dsmax_startup.ms"
MIN_CORE_VERSION = "0.20.20"
try:
    from dcc_mcp_core.deployment import INSTALL_EXIT_CODES, INSTALL_SOP_SCHEMA_VERSION
except ImportError:
    # Import-light fallback lets the CLI return a stable preflight report when
    # an old Core is present; pyproject requires the published implementation.
    INSTALL_SOP_SCHEMA_VERSION = 1
    INSTALL_EXIT_CODES = {
        "ok": 0,
        "preflight": 10,
        "acquire": 20,
        "install": 30,
        "verify": 40,
        "requires_restart": 50,
    }
INSTALL_EXIT_OK = INSTALL_EXIT_CODES["ok"]
INSTALL_EXIT_PREFLIGHT = INSTALL_EXIT_CODES["preflight"]
INSTALL_EXIT_ACQUIRE = INSTALL_EXIT_CODES["acquire"]
INSTALL_EXIT_INSTALL = INSTALL_EXIT_CODES["install"]
INSTALL_EXIT_VERIFY = INSTALL_EXIT_CODES["verify"]
INSTALL_EXIT_REQUIRES_RESTART = INSTALL_EXIT_CODES["requires_restart"]
DEFAULT_RECEIPT_PATH = Path.home() / ".dcc-mcp" / "receipts" / "3dsmax.json"
READINESS_FAILURE_REASONS = {
    "ambiguous",
    "booting",
    "dead",
    "missing",
    "probe_bad_response",
    "probe_failed",
    "probe_http_error",
    "probe_missing_tool",
    "probe_missing_url",
    "probe_unreachable",
    "timeout",
    "unavailable",
}
PUBLIC_OPERATION_FAILURES = {
    "install": (INSTALL_EXIT_INSTALL, "install", "operation_failed"),
    "upgrade": (INSTALL_EXIT_INSTALL, "install", "operation_failed"),
    "uninstall": (INSTALL_EXIT_INSTALL, "uninstall", "operation_failed"),
    "status": (INSTALL_EXIT_PREFLIGHT, "preflight", "status_failed"),
    "verify": (INSTALL_EXIT_VERIFY, "verify", "verification_failed"),
}


class LifecycleError(RuntimeError):
    """Stable public failure without exposing exception text in JSON."""

    def __init__(self, exit_code: int, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.exit_code = exit_code
        self.stage = stage
        self.reason = reason


@dataclass
class InstallContext:
    host_path: Path
    python_path: Path
    startup_dir: Path
    hook_path: Path
    receipt_path: Path
    host_version: str
    core_version: str
    state: str
    state_stage: Optional[str]
    state_reason: Optional[str]


@dataclass(frozen=True)
class FileSnapshot:
    existed: bool
    content: bytes
    sha256: str
    identity: Optional[Tuple[int, int]]
    independent: bool


def _core_version() -> str:
    try:
        import dcc_mcp_core

        return str(getattr(dcc_mcp_core, "__version__", "unknown"))
    except ImportError:
        return "unavailable"


def _version_tuple(value: str) -> Tuple[int, ...]:
    values = [int(item) for item in re.findall(r"\d+", str(value))[:3]]
    return tuple(values + [0] * (3 - len(values)))


def _published_schema() -> Optional[Dict[str, Any]]:
    try:
        from dcc_mcp_core.deployment import load_install_sop_schema
    except ImportError:
        try:
            from dcc_mcp_core import load_install_sop_schema
        except ImportError:
            return None
    return load_install_sop_schema()


def validate_public_report(report: Dict[str, Any]) -> None:
    """Validate against Core's published Draft 2020-12 schema when available."""
    schema = _published_schema()
    if schema is not None:
        from jsonschema import Draft202012Validator

        Draft202012Validator(schema).validate(report)
        return
    required = {
        "schema_version",
        "status",
        "dcc_type",
        "adapter_version",
        "core_version",
        "steps",
        "next_steps",
        "receipt_path",
        "verify",
    }
    if not required.issubset(report) or report.get("schema_version") != INSTALL_SOP_SCHEMA_VERSION:
        raise ValueError("Install SOP v1 report is incomplete")


def loads_public_report(value: str) -> Dict[str, Any]:
    report = json.loads(value)
    if not isinstance(report, dict):
        raise ValueError("Install SOP v1 output must be one JSON object")
    validate_public_report(report)
    return report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _maxscript_string(value: str) -> str:
    return value.replace("\\", "/").replace('"', '\\"')


def render_startup_script(log_dir: Path) -> str:
    encoded_log_dir = base64.b64encode(str(log_dir.resolve()).encode("utf-8")).decode("ascii")
    python_code = (
        "import base64\n"
        "from dcc_mcp_core import capture_bootstrap_errors\n"
        "bootstrap_error_dir = base64.b64decode(%r).decode('utf-8')\n"
        "with capture_bootstrap_errors(\n"
        "    '3dsmax', adapter_version=%r, min_core_version=%r,\n"
        "    phase='startup', log_dir=bootstrap_error_dir):\n"
        "    import dcc_mcp_3dsmax\n"
        "    dcc_mcp_3dsmax.install_menu()\n"
        "    dcc_mcp_3dsmax.install_shutdown_callback()\n"
        "    dcc_mcp_3dsmax.main()\n"
        "    print('dcc-mcp-3dsmax runtime ready from startup hook')\n"
    ) % (encoded_log_dir, ADAPTER_VERSION, MIN_CORE_VERSION)
    lines = ['    py += "%s\\n"' % _maxscript_string(line) for line in python_code.splitlines()]
    return "\n".join(
        [
            "-- Managed by dcc-mcp-3dsmax Install SOP v1.",
            "(",
            '    local py = ""',
        ]
        + lines
        + ["    python.Execute py", ")", ""]
    )


def _find_host(explicit: Optional[str]) -> Path:
    if explicit:
        host = Path(explicit).expanduser().resolve()
        if host.is_file() and host.name.lower() == "3dsmax.exe":
            host = host.parent
        if (host / "3dsmax.exe").is_file():
            return host
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "host_not_found")
    candidates: List[Path] = []
    for name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(name)
        if root:
            for year in range(2028, 2019, -1):
                candidates.append(Path(root) / "Autodesk" / ("3ds Max %s" % year))
    for candidate in candidates:
        if (candidate / "3dsmax.exe").is_file():
            return candidate.resolve()
    raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "host_not_found")


def _find_python(host: Path, explicit: Optional[str]) -> Path:
    candidates = [Path(explicit).expanduser()] if explicit else [host / "Python" / "python.exe", host / "3dsmaxpy.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "python_not_found")


def _host_version(host: Path) -> str:
    match = re.search(r"3ds Max\s+(\d{4})", str(host), flags=re.IGNORECASE)
    return match.group(1) if match else "unknown"


def _startup_dir(host: Path, explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    year = _host_version(host)
    root = os.environ.get("LOCALAPPDATA")
    if not root or year == "unknown":
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "startup_dir_not_found")
    return (Path(root) / "Autodesk" / "3dsMax" / ("%s - 64bit" % year) / "ENU" / "scripts" / "startup").resolve()


def _read_receipt(path: Path, required: bool = False) -> Optional[Dict[str, Any]]:
    if not os.path.lexists(str(path)):
        if required:
            raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "receipt", "receipt_missing")
        return None
    try:
        content, identity = _read_independent_file(path)
    except Exception:
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "receipt", "receipt_ownership_invalid")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "receipt", "receipt_invalid")
    if not isinstance(value, dict) or value.get("receipt_version") != 1 or value.get("dcc_type") != DCC_TYPE:
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "receipt", "receipt_invalid")
    if not _identity_record_matches(value.get("receipt_identity"), identity):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "receipt", "receipt_ownership_invalid")
    return value


def _inspect_state(receipt_path: Path, hook_path: Path) -> Tuple[str, Optional[str], Optional[str]]:
    if not os.path.lexists(str(receipt_path)):
        if hook_path.exists():
            return "partial", "receipt", "receipt_missing"
        return "fresh", "preflight", "not_installed"
    try:
        receipt = _read_receipt(receipt_path, required=True)
    except LifecycleError as exc:
        return "partial", exc.stage, exc.reason
    assert receipt is not None
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
        return "partial", "receipt", "receipt_ownership_invalid"
    artifact = artifacts[0]
    if _lexical_path_key(artifact.get("path")) != _lexical_path_key(hook_path):
        return "partial", "receipt", "receipt_target_mismatch"
    try:
        hook_content, hook_identity = _read_independent_file(hook_path)
    except Exception:
        return "partial", "artifact", "startup_hook_missing_or_modified"
    if hashlib.sha256(hook_content).hexdigest() != artifact.get("sha256") or not _identity_record_matches(
        artifact.get("identity"), hook_identity
    ):
        return "partial", "artifact", "startup_hook_missing_or_modified"
    if receipt.get("adapter_version") != ADAPTER_VERSION:
        return "upgrade", None, None
    return "current", None, None


def _context(args: argparse.Namespace) -> InstallContext:
    host = _find_host(args.dcc_path)
    python = _find_python(host, args.python)
    startup = _startup_dir(host, args.startup_dir)
    receipt = _lexical_absolute_path(args.receipt_path or DEFAULT_RECEIPT_PATH)
    hook = startup / STARTUP_SCRIPT_NAME
    state, stage, reason = _inspect_state(receipt, hook)
    return InstallContext(
        host_path=host,
        python_path=python,
        startup_dir=startup,
        hook_path=hook,
        receipt_path=receipt,
        host_version=_host_version(host),
        core_version=_core_version(),
        state=state,
        state_stage=stage,
        state_reason=reason,
    )


def _next_command(ctx: InstallContext, verb: str, step_id: str, why: str) -> Dict[str, Any]:
    return {
        "id": step_id,
        "description": "Run the 3ds Max %s lifecycle step." % verb,
        "command": [
            COMMAND,
            verb,
            "--json",
            "--dcc-path",
            str(ctx.host_path),
            "--python",
            str(ctx.python_path),
            "--startup-dir",
            str(ctx.startup_dir),
            "--receipt-path",
            str(ctx.receipt_path),
        ],
        "why": why,
    }


def _base_report(ctx: InstallContext, status: str, command: str) -> Dict[str, Any]:
    return {
        "schema_version": INSTALL_SOP_SCHEMA_VERSION,
        "status": status,
        "dcc_type": DCC_TYPE,
        "command": command,
        "adapter_version": ADAPTER_VERSION,
        "core_version": ctx.core_version,
        "steps": [],
        "next_steps": [],
        "receipt_path": str(ctx.receipt_path) if ctx.receipt_path.is_file() else None,
        "verify": {"directly_usable": False, "failure_stage": None, "failure_reason": None},
        "host": {"path": str(ctx.host_path), "version": ctx.host_version},
        "python": {"path": str(ctx.python_path)},
        "install_state": ctx.state,
    }


def _failure_report(
    ctx: InstallContext, command: str, stage: str, reason: str, status: str = "failed"
) -> Dict[str, Any]:
    report = _base_report(ctx, status, command)
    report["steps"] = [{"id": stage, "status": "failed"}]
    report["verify"] = {"directly_usable": False, "failure_stage": stage, "failure_reason": reason}
    return report


def _plan(ctx: InstallContext, command: str) -> Dict[str, Any]:
    report = _base_report(ctx, "planned", command)
    report["steps"] = [
        {"id": "preflight", "status": "ok"},
        {"id": "acquire", "status": "planned"},
        {"id": "stage", "status": "planned"},
        {"id": "commit", "status": "planned"},
        {"id": "verify", "status": "planned"},
    ]
    report["next_steps"] = [_next_command(ctx, command, "execute_%s" % command, "Execute the validated plan.")]
    return report


def _probe_target(python: Path) -> Dict[str, Any]:
    code = (
        "import json,sys,dcc_mcp_core,dcc_mcp_3dsmax;"
        "print(json.dumps({'python_version':sys.version.split()[0],"
        "'adapter_version':dcc_mcp_3dsmax.__version__,"
        "'core_version':getattr(dcc_mcp_core,'__version__','unknown')}))"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        value = json.loads(completed.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        raise LifecycleError(INSTALL_EXIT_VERIFY, "import", "target_import_failed")
    if not isinstance(value, dict):
        raise LifecycleError(INSTALL_EXIT_VERIFY, "import", "target_import_failed")
    return value


def _probe_compatibility(ctx: InstallContext) -> Dict[str, Any]:
    code = (
        "import json,sys;"
        "core_version=None;"
        "exec(\"try:\\n import dcc_mcp_core\\n core_version=getattr(dcc_mcp_core,'__version__','unknown')"
        '\\nexcept ImportError:\\n pass");'
        "print(json.dumps({'python_version':sys.version.split()[0],'core_version':core_version}))"
    )
    try:
        completed = subprocess.run(
            [str(ctx.python_path), "-c", code],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        value = json.loads(completed.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "compatibility_probe_failed")
    if not isinstance(value, dict):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "compatibility_probe_failed")
    value["host_version"] = ctx.host_version
    return value


def _validate_compatibility(compatibility: Dict[str, Any]) -> None:
    if _version_tuple(str(compatibility.get("python_version", "0"))) < (3, 7, 0):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "python_version_unsupported")
    host_version = str(compatibility.get("host_version") or "unknown")
    if not host_version.isdigit() or int(host_version) < 2017:
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "host_version_unsupported")
    core_version = compatibility.get("core_version")
    if core_version in (None, "", "unavailable"):
        return
    parsed_core = _version_tuple(str(core_version))
    if parsed_core < _version_tuple(MIN_CORE_VERSION):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "core_version_too_old")
    if parsed_core >= (1, 0, 0):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "core_version_unsupported")


def _preflight_compatibility(ctx: InstallContext) -> Dict[str, Any]:
    compatibility = _probe_compatibility(ctx)
    _validate_compatibility(compatibility)
    return compatibility


def _install_package(ctx: InstallContext, source: str) -> bool:
    if source == "local":
        root = Path(__file__).resolve().parents[2]
        if not (root / "pyproject.toml").is_file():
            raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "local_source_unavailable")
        requirement = str(root)
    else:
        requirement = "dcc-mcp-3dsmax==%s" % ADAPTER_VERSION
    try:
        subprocess.run(
            [str(ctx.python_path), "-m", "pip", "install", "--upgrade", requirement],
            cwd=str(root) if source == "local" else None,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "package_install_failed")
    return True


def _uninstall_package(ctx: InstallContext) -> None:
    try:
        subprocess.run(
            [str(ctx.python_path), "-m", "pip", "uninstall", "-y", "dcc-mcp-3dsmax"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        raise LifecycleError(INSTALL_EXIT_INSTALL, "uninstall", "package_uninstall_failed")


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _frozen_requirement_name(requirement: str) -> Optional[str]:
    match = re.match(r"^([A-Za-z0-9_.-]+)(?:==|\s*@\s*)", requirement)
    if match:
        return _normalized_distribution_name(match.group(1))
    editable = re.match(r"^-e\s+.+[#&]egg=([A-Za-z0-9_.-]+)(?:&.*)?$", requirement)
    if editable:
        return _normalized_distribution_name(editable.group(1))
    return None


def _package_state_from_lines(lines: Sequence[str]) -> Dict[str, Any]:
    requirements: List[str] = []
    by_name: Dict[str, str] = {}
    fingerprints: Dict[str, str] = {}
    for raw_line in lines:
        requirement = str(raw_line).strip()
        if not requirement or requirement.startswith("#"):
            continue
        name = _frozen_requirement_name(requirement)
        if name is None or name in by_name:
            raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_state_unavailable")
        requirements.append(requirement)
        by_name[name] = requirement
        fingerprints[name] = hashlib.sha256(requirement.encode("utf-8")).hexdigest()
    requirements.sort()
    canonical = "\n".join(requirements).encode("utf-8")
    return {
        "requirements": tuple(requirements),
        "by_name": by_name,
        "fingerprints": fingerprints,
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _snapshot_package_state(ctx: InstallContext) -> Dict[str, Any]:
    code = (
        "import hashlib,json,os,sys\n"
        "try:\n from importlib import metadata\n"
        "except ImportError:\n"
        " try:\n  import importlib_metadata as metadata\n"
        " except ImportError:\n  metadata=None\n"
        "items=[]\n"
        "if metadata is not None:\n"
        " for dist in metadata.distributions():\n"
        "  name=dist.metadata.get('Name')\n"
        "  version=dist.version\n"
        "  if not name or not version:\n   raise RuntimeError('distribution identity unavailable')\n"
        "  direct_text=dist.read_text('direct_url.json') or ''\n"
        "  direct=json.loads(direct_text) if direct_text else {}\n"
        "  url=direct.get('url') if isinstance(direct,dict) else None\n"
        "  requirement=(str(name)+' @ '+str(url)) if url else (str(name)+'=='+str(version))\n"
        "  record=dist.read_text('RECORD') or ''\n"
        "  items.append({'name':str(name),'version':str(version),'requirement':requirement,"
        "'direct_url':direct,'metadata_location':os.path.abspath(str(getattr(dist,'_path',''))),"
        "'record_sha256':hashlib.sha256(record.encode('utf-8')).hexdigest()})\n"
        "else:\n"
        " from email.parser import Parser\n"
        " seen_paths=set()\n"
        " for root in sys.path:\n"
        "  if not root or not os.path.isdir(root):\n   continue\n"
        "  root=os.path.abspath(root)\n"
        "  if root in seen_paths:\n   continue\n"
        "  seen_paths.add(root)\n"
        "  for entry in os.listdir(root):\n"
        "   lower=entry.lower()\n"
        "   if not (lower.endswith('.dist-info') or lower.endswith('.egg-info')):\n    continue\n"
        "   location=os.path.join(root,entry)\n"
        "   if os.path.isdir(location):\n"
        "    metadata_path=os.path.join(location,'METADATA' if lower.endswith('.dist-info') else 'PKG-INFO')\n"
        "   else:\n    metadata_path=location\n"
        "   if not os.path.isfile(metadata_path):\n    raise RuntimeError('distribution metadata unavailable')\n"
        "   with open(metadata_path,'r',encoding='utf-8',errors='replace') as stream:\n"
        "    parsed=Parser().parsestr(stream.read())\n"
        "   name=parsed.get('Name')\n"
        "   version=parsed.get('Version')\n"
        "   if not name or not version:\n    raise RuntimeError('distribution identity unavailable')\n"
        "   direct_path=os.path.join(location,'direct_url.json')\n"
        "   if os.path.isfile(direct_path):\n"
        "    with open(direct_path,'r',encoding='utf-8') as stream:\n     direct=json.load(stream)\n"
        "   else:\n    direct={}\n"
        "   url=direct.get('url') if isinstance(direct,dict) else None\n"
        "   requirement=(str(name)+' @ '+str(url)) if url else (str(name)+'=='+str(version))\n"
        "   record_path=os.path.join(location,'RECORD')\n"
        "   if os.path.isfile(record_path):\n"
        "    with open(record_path,'r',encoding='utf-8',errors='replace') as stream:\n     record=stream.read()\n"
        "   else:\n    record=''\n"
        "   items.append({'name':str(name),'version':str(version),'requirement':requirement,"
        "'direct_url':direct,'metadata_location':os.path.abspath(location),"
        "'record_sha256':hashlib.sha256(record.encode('utf-8')).hexdigest()})\n"
        "print(json.dumps(items,sort_keys=True,separators=(',',':')))\n"
    )
    try:
        completed = subprocess.run(
            [str(ctx.python_path), "-c", code],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=60,
        )
        records = json.loads(completed.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_state_unavailable")
    if len(completed.stdout.encode("utf-8")) > 1024 * 1024 or not isinstance(records, list):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_state_unavailable")
    requirements: List[str] = []
    by_name: Dict[str, str] = {}
    fingerprints: Dict[str, str] = {}
    canonical_records: List[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_state_unavailable")
        name_value = record.get("name")
        requirement = record.get("requirement")
        if not isinstance(name_value, str) or not isinstance(requirement, str):
            raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_state_unavailable")
        name = _normalized_distribution_name(name_value)
        if not name or name in by_name:
            raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_state_unavailable")
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        requirements.append(requirement)
        by_name[name] = requirement
        fingerprints[name] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        canonical_records.append(canonical)
    requirements.sort()
    canonical_records.sort()
    return {
        "requirements": tuple(requirements),
        "by_name": by_name,
        "fingerprints": fingerprints,
        "sha256": hashlib.sha256("\n".join(canonical_records).encode("utf-8")).hexdigest(),
    }


def _run_pip_command(ctx: InstallContext, command: Sequence[str]) -> None:
    subprocess.run(
        [str(ctx.python_path), "-m", "pip"] + list(command),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )


def _restore_package_state(ctx: InstallContext, prior: Dict[str, Any]) -> None:
    try:
        current = _snapshot_package_state(ctx)
        prior_by_name = prior["by_name"]
        current_by_name = current["by_name"]
        prior_fingerprints = prior["fingerprints"]
        current_fingerprints = current["fingerprints"]
        remove = sorted(name for name in current_by_name if name not in prior_by_name)
        restore = sorted(
            requirement
            for name, requirement in prior_by_name.items()
            if current_fingerprints.get(name) != prior_fingerprints.get(name)
        )
        if remove:
            _run_pip_command(ctx, ["uninstall", "-y"] + remove)
        if restore:
            _run_pip_command(ctx, ["install", "--no-deps", "--force-reinstall"] + restore)
        if remove or restore:
            restored = _snapshot_package_state(ctx)
            if restored.get("sha256") != prior.get("sha256"):
                raise RuntimeError("package state mismatch")
    except Exception:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")


def _rollback_package(ctx: InstallContext, prior: Dict[str, Any]) -> None:
    _restore_package_state(ctx, prior)


def _rollback_transaction(
    ctx: InstallContext,
    prior_package_state: Dict[str, Any],
    hook_before: Any,
    receipt_before: Any,
    restore_files: bool,
    committed: Optional[Dict[Path, FileSnapshot]] = None,
) -> None:
    rollback_failed = False
    if restore_files:
        for path, content in (
            (ctx.hook_path, hook_before),
            (ctx.receipt_path, receipt_before),
        ):
            if committed is not None and path not in committed:
                continue
            try:
                snapshot = _coerce_file_snapshot(content)
                expected = committed[path] if committed is not None else _snapshot(path)
                rollback_journal: Dict[Path, FileSnapshot] = {}
                restored = _restore_if_snapshot(
                    path,
                    snapshot.content if snapshot.existed else None,
                    expected,
                    rollback_journal,
                )
                if not _snapshot_is_current(path, restored):
                    rollback_failed = True
            except Exception:
                rollback_failed = True
    try:
        _restore_package_state(ctx, prior_package_state)
    except Exception:
        rollback_failed = True
    if rollback_failed:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "transaction_rollback_incomplete")


def _rollback_committed(
    ctx: InstallContext,
    prior_package_state: Dict[str, Any],
    hook_before: Any,
    receipt_before: Any,
    committed: Dict[Path, FileSnapshot],
) -> None:
    _rollback_transaction(
        ctx,
        prior_package_state,
        hook_before,
        receipt_before,
        bool(committed),
        committed,
    )


def _replace_file(source: Path, destination: Path) -> None:
    """Publish *source* without ever overwriting an existing destination."""

    os.link(str(source), str(destination))


def _file_identity(file_stat: os.stat_result) -> Tuple[int, int]:
    return int(file_stat.st_dev), int(file_stat.st_ino)


def _identity_record(identity: Optional[Tuple[int, int]]) -> Optional[Dict[str, int]]:
    if identity is None:
        return None
    return {"device": identity[0], "inode": identity[1]}


def _is_identity_record(value: Any) -> bool:
    return isinstance(value, dict) and type(value.get("device")) is int and type(value.get("inode")) is int


def _identity_record_matches(value: Any, identity: Tuple[int, int]) -> bool:
    return _is_identity_record(value) and (value["device"], value["inode"]) == identity


def _lexical_absolute_path(value: Any) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def _lexical_path_key(value: Any) -> str:
    if value is None:
        return ""
    return os.path.normcase(str(_lexical_absolute_path(value)))


def _is_independent_regular_file(file_stat: os.stat_result) -> bool:
    reparse_point = bool(getattr(file_stat, "st_file_attributes", 0) & 0x400)
    return stat.S_ISREG(file_stat.st_mode) and file_stat.st_nlink == 1 and not reparse_point


def _read_independent_file(path: Path) -> Tuple[bytes, Tuple[int, int]]:
    if not os.path.lexists(str(path)):
        raise FileNotFoundError(str(path))
    before = os.lstat(str(path))
    if not _is_independent_regular_file(before):
        raise OSError("file is not independent")
    identity = _file_identity(before)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not _is_independent_regular_file(opened) or _file_identity(opened) != identity:
            raise OSError("file identity changed before read")
        content = stream.read()
        after_read = os.fstat(stream.fileno())
    after = os.lstat(str(path))
    if (
        not _is_independent_regular_file(after_read)
        or not _is_independent_regular_file(after)
        or _file_identity(after_read) != identity
        or _file_identity(after) != identity
    ):
        raise OSError("file identity changed during read")
    return content, identity


def _absent_file_snapshot() -> FileSnapshot:
    return FileSnapshot(False, b"", hashlib.sha256(b"").hexdigest(), None, True)


def _coerce_file_snapshot(value: Any) -> FileSnapshot:
    if isinstance(value, FileSnapshot):
        return value
    if value is None:
        return _absent_file_snapshot()
    if not isinstance(value, bytes):
        raise TypeError("unsupported file snapshot")
    return FileSnapshot(True, value, hashlib.sha256(value).hexdigest(), None, True)


def _snapshot(path: Path) -> FileSnapshot:
    if not os.path.lexists(str(path)):
        return _absent_file_snapshot()
    try:
        content, identity = _read_independent_file(path)
    except Exception:
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "file_identity_ambiguous")
    return FileSnapshot(True, content, hashlib.sha256(content).hexdigest(), identity, True)


def _snapshot_is_current(path: Path, expected: Any) -> bool:
    snapshot = _coerce_file_snapshot(expected)
    if not snapshot.existed:
        return not os.path.lexists(str(path))
    if not snapshot.independent or snapshot.identity is None:
        return False
    try:
        content, identity = _read_independent_file(path)
    except Exception:
        return False
    return (
        identity == snapshot.identity
        and content == snapshot.content
        and hashlib.sha256(content).hexdigest() == snapshot.sha256
    )


def _require_snapshot_current(path: Path, expected: Any) -> None:
    if not _snapshot_is_current(path, expected):
        raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")


def _snapshot_matches(
    path: Path,
    expected: Any,
    restored_identity: Optional[Tuple[int, int]],
) -> bool:
    snapshot = _coerce_file_snapshot(expected)
    if not snapshot.existed:
        return not os.path.lexists(str(path))
    if not snapshot.independent or restored_identity is None or not os.path.lexists(str(path)):
        return False
    try:
        actual, identity = _read_independent_file(path)
    except Exception:
        return False
    return (
        identity == restored_identity
        and actual == snapshot.content
        and hashlib.sha256(actual).hexdigest() == snapshot.sha256
    )


def _restore(path: Path, content: Optional[bytes]) -> Optional[Tuple[int, int]]:
    if content is None:
        if os.path.lexists(str(path)):
            raise FileExistsError(str(path))
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".%s.restore-%s" % (path.name, uuid.uuid4().hex))
    try:
        temp.write_bytes(content)
        temp_stat = os.lstat(str(temp))
        if not _is_independent_regular_file(temp_stat):
            raise OSError("restore staging file is not independent")
        restored_identity = _file_identity(temp_stat)
        _replace_file(temp, path)
        return restored_identity
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _restore_claim_without_clobber(claim: Path, path: Path) -> None:
    """Put a claimed identity back only while its original name is vacant."""

    os.link(str(claim), str(path))
    claim.unlink()


def _recover_claim(
    claim: Optional[Path],
    path: Path,
    expected: Any,
    committed: Dict[Path, FileSnapshot],
) -> None:
    if claim is None or os.path.lexists(str(path)):
        return
    _restore_claim_without_clobber(claim, path)
    if _snapshot_is_current(path, expected):
        committed.pop(path, None)


def _claim_file_if_snapshot(path: Path, expected: Any) -> Optional[Path]:
    """Atomically remove and return the exact expected identity from *path*.

    Renaming to a same-directory private name is the platform CAS claim.  A
    replacement that wins before the rename is detected by identity validation
    and restored without overwriting any newer path occupant.
    """

    snapshot = _coerce_file_snapshot(expected)
    if not snapshot.existed:
        _require_snapshot_current(path, snapshot)
        return None
    claim = path.with_name(".%s.claim-%s" % (path.name, uuid.uuid4().hex))
    try:
        os.replace(str(path), str(claim))
    except FileNotFoundError:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")
    if _snapshot_is_current(claim, snapshot):
        return claim
    try:
        _restore_claim_without_clobber(claim, path)
    except Exception:
        pass
    raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")


def _replace_file_if_snapshot(
    source: Path,
    destination: Path,
    expected: Any,
    committed: Dict[Path, FileSnapshot],
) -> FileSnapshot:
    staged = _snapshot(source)
    claim = _claim_file_if_snapshot(destination, expected)
    removed = _absent_file_snapshot()
    committed[destination] = removed
    try:
        # The platform publish is no-clobber and leaves one independent final
        # identity after removing the same-directory stage.
        _replace_file(source, destination)
        committed[destination] = staged
        source.unlink()
        if claim is not None:
            claim.unlink()
        _require_snapshot_current(destination, staged)
        return staged
    except FileExistsError:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")
    except Exception:
        try:
            _recover_claim(claim, destination, expected, committed)
        except Exception:
            pass
        raise


def _restore_if_snapshot(
    path: Path,
    content: Optional[bytes],
    expected: Any,
    committed: Dict[Path, FileSnapshot],
) -> FileSnapshot:
    if content is None:
        return _unlink_if_snapshot(path, expected, committed)
    claim = _claim_file_if_snapshot(path, expected)
    removed = _absent_file_snapshot()
    committed[path] = removed
    try:
        restored_identity = _restore(path, content)
        restored = FileSnapshot(
            True,
            content,
            hashlib.sha256(content).hexdigest(),
            restored_identity,
            True,
        )
        committed[path] = restored
        if claim is not None:
            claim.unlink()
        _require_snapshot_current(path, restored)
        return restored
    except FileExistsError:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")
    except Exception:
        try:
            _recover_claim(claim, path, expected, committed)
        except Exception:
            pass
        raise


def _unlink_if_snapshot(
    path: Path,
    expected: Any,
    committed: Dict[Path, FileSnapshot],
) -> FileSnapshot:
    claim = _claim_file_if_snapshot(path, expected)
    removed = _absent_file_snapshot()
    committed[path] = removed
    try:
        _restore(path, None)
        if claim is not None:
            claim.unlink()
        _require_snapshot_current(path, removed)
        return removed
    except Exception:
        try:
            _recover_claim(claim, path, expected, committed)
        except Exception:
            pass
        raise


def _previous_hook(prior_receipt: Optional[Dict[str, Any]], current: Any) -> Dict[str, Any]:
    if prior_receipt and isinstance(prior_receipt.get("previous_hook"), dict):
        return dict(prior_receipt["previous_hook"])
    snapshot = _coerce_file_snapshot(current)
    content = snapshot.content if snapshot.existed else b""
    return {
        "existed": snapshot.existed,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "identity": _identity_record(snapshot.identity),
    }


def _receipt(
    ctx: InstallContext,
    staged_hook: Path,
    prior_receipt: Optional[Dict[str, Any]],
    current: Any,
    target: Dict[str, Any],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    hook_content, hook_identity = _read_independent_file(staged_hook)
    return {
        "receipt_version": 1,
        "schema_version": INSTALL_SOP_SCHEMA_VERSION,
        "dcc_type": DCC_TYPE,
        "adapter_version": ADAPTER_VERSION,
        "core_version": str(target.get("core_version", ctx.core_version)),
        "host": {"path": str(ctx.host_path), "version": ctx.host_version},
        "python": {"path": str(ctx.python_path), "version": str(target.get("python_version", "unknown"))},
        "artifacts": [
            {
                "kind": "startup_hook",
                "path": str(ctx.hook_path),
                "sha256": hashlib.sha256(hook_content).hexdigest(),
                "identity": _identity_record(hook_identity),
            }
        ],
        "previous_hook": _previous_hook(prior_receipt, current),
        "bootstrap_error_dir": str(ctx.receipt_path.parent / "bootstrap-errors"),
        "installed_at": now.isoformat(),
        "installed_at_epoch": now.timestamp(),
        "transaction": {"strategy": "staged_replace", "rollback": "previous_state"},
    }


def _write_staged_receipt(path: Path, receipt: Dict[str, Any]) -> None:
    receipt.pop("receipt_identity", None)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    staged = os.lstat(str(path))
    if not _is_independent_regular_file(staged):
        raise OSError("receipt staging file is not independent")
    identity = _file_identity(staged)
    receipt["receipt_identity"] = _identity_record(identity)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _, verified_identity = _read_independent_file(path)
    if verified_identity != identity:
        raise OSError("receipt staging identity changed")


def _is_windows_lock(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}


def _install_transaction(
    ctx: InstallContext,
    source: str,
    ownership: Optional[Tuple[FileSnapshot, FileSnapshot]] = None,
) -> None:
    hook_before, receipt_before = ownership or _preflight_ownership(ctx)
    _require_snapshot_current(ctx.hook_path, hook_before)
    _require_snapshot_current(ctx.receipt_path, receipt_before)
    prior_receipt = _read_receipt(ctx.receipt_path)
    prior_package_state = _snapshot_package_state(ctx)
    token = uuid.uuid4().hex
    hook_stage = ctx.hook_path.with_name(".%s.stage-%s" % (ctx.hook_path.name, token))
    receipt_stage = ctx.receipt_path.with_name(".%s.stage-%s" % (ctx.receipt_path.name, token))
    package_attempted = False
    committed: Dict[Path, FileSnapshot] = {}
    try:
        package_attempted = True
        _install_package(ctx, source)
        target = _probe_target(ctx.python_path)
        if target.get("adapter_version") != ADAPTER_VERSION:
            raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "adapter_version_mismatch")
        if _version_tuple(str(target.get("core_version", "0"))) < _version_tuple(MIN_CORE_VERSION):
            raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "core_version_too_old")
        _require_snapshot_current(ctx.hook_path, hook_before)
        _require_snapshot_current(ctx.receipt_path, receipt_before)
        ctx.startup_dir.mkdir(parents=True, exist_ok=True)
        ctx.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        hook_stage.write_text(
            render_startup_script(ctx.receipt_path.parent / "bootstrap-errors"),
            encoding="utf-8",
        )
        _write_staged_receipt(receipt_stage, _receipt(ctx, hook_stage, prior_receipt, hook_before, target))
        _replace_file_if_snapshot(hook_stage, ctx.hook_path, hook_before, committed)
        _replace_file_if_snapshot(receipt_stage, ctx.receipt_path, receipt_before, committed)
    except LifecycleError:
        if package_attempted:
            _rollback_committed(ctx, prior_package_state, hook_before, receipt_before, committed)
        raise
    except OSError as exc:
        if package_attempted:
            _rollback_committed(ctx, prior_package_state, hook_before, receipt_before, committed)
        if _is_windows_lock(exc):
            raise LifecycleError(INSTALL_EXIT_REQUIRES_RESTART, "install", "windows_file_lock")
        raise LifecycleError(INSTALL_EXIT_INSTALL, "install", "commit_failed")
    except Exception:
        if package_attempted:
            _rollback_committed(ctx, prior_package_state, hook_before, receipt_before, committed)
        raise LifecycleError(INSTALL_EXIT_INSTALL, "install", "commit_failed")
    finally:
        for stage in (hook_stage, receipt_stage):
            try:
                stage.unlink()
            except FileNotFoundError:
                pass


def _wait_readiness(timeout: float) -> Dict[str, Any]:
    try:
        from dcc_mcp_core.deployment import wait_for_sidecar_ready
    except ImportError:
        from dcc_mcp_core import wait_for_sidecar_ready
    return wait_for_sidecar_ready(
        dcc_type=DCC_TYPE,
        timeout_secs=timeout,
        probe_tool="3dsmax_diagnostics__ping",
    )


def _readiness_verdict(timeout: float) -> Tuple[bool, str]:
    """Return only bounded, public readiness identities from Core's result."""
    try:
        readiness = _wait_readiness(timeout)
        if not isinstance(readiness, dict):
            return False, "invalid_readiness_status"
        status = readiness.get("status")
        success = readiness.get("success")
        if type(status) is not str:
            return False, "invalid_readiness_status"
        if success is True and status == "ready":
            return True, "ready"
        if success is False and status in READINESS_FAILURE_REASONS:
            return False, status
    except Exception:
        return False, "readiness_probe_failed"
    return False, "invalid_readiness_status"


def _bootstrap_error_state(receipt: Dict[str, Any]) -> Optional[str]:
    """Inspect bounded host-error receipts without exposing operator paths."""
    try:
        error_dir = Path(str(receipt.get("bootstrap_error_dir", "")))
        installed_at = float(receipt.get("installed_at_epoch", 0.0))
        if error_dir.is_dir():
            for error_log in error_dir.glob("*.host-errors.log"):
                if error_log.stat().st_mtime >= installed_at:
                    return "bootstrap_error"
    except Exception:
        return "bootstrap_log_unavailable"
    return None


def _verify(ctx: InstallContext, timeout: float) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    state, stage, reason = _inspect_state(ctx.receipt_path, ctx.hook_path)
    if state not in {"current", "upgrade"}:
        return {"directly_usable": False, "failure_stage": stage, "failure_reason": reason}, []
    receipt = _read_receipt(ctx.receipt_path, required=True)
    assert receipt is not None
    try:
        target = _probe_target(ctx.python_path)
    except LifecycleError as exc:
        return {"directly_usable": False, "failure_stage": exc.stage, "failure_reason": exc.reason}, []
    if target.get("adapter_version") != ADAPTER_VERSION:
        return {"directly_usable": False, "failure_stage": "import", "failure_reason": "adapter_version_mismatch"}, []
    if _version_tuple(str(target.get("core_version", "0"))) < _version_tuple(MIN_CORE_VERSION):
        return {"directly_usable": False, "failure_stage": "preflight", "failure_reason": "core_version_too_old"}, []
    bootstrap_reason = _bootstrap_error_state(receipt)
    if bootstrap_reason:
        return {"directly_usable": False, "failure_stage": "startup", "failure_reason": bootstrap_reason}, []
    ready, readiness_reason = _readiness_verdict(timeout)
    if not ready:
        return (
            {
                "directly_usable": False,
                "failure_stage": "readiness",
                "failure_reason": readiness_reason,
            },
            [_next_command(ctx, "verify", "restart_3dsmax", "Restart 3ds Max, then verify typed sidecar readiness.")],
        )
    return {"directly_usable": True, "failure_stage": None, "failure_reason": None}, []


def _preflight_ownership(ctx: InstallContext) -> Tuple[FileSnapshot, FileSnapshot]:
    return _snapshot(ctx.hook_path), _snapshot(ctx.receipt_path)


def _run_install(ctx: InstallContext, args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    ownership = _preflight_ownership(ctx)
    if ctx.state == "partial" and ctx.receipt_path.is_file():
        return INSTALL_EXIT_PREFLIGHT, _failure_report(
            ctx,
            args.command,
            ctx.state_stage or "receipt",
            ctx.state_reason or "receipt_ownership_invalid",
        )
    _preflight_compatibility(ctx)
    if args.dry_run:
        return INSTALL_EXIT_OK, _plan(ctx, args.command)
    if not args.yes:
        report = _failure_report(ctx, args.command, "preflight", "confirmation_required")
        report["next_steps"] = [
            _next_command(ctx, args.command, "confirm_%s" % args.command, "Explicit consent is required.")
        ]
        return INSTALL_EXIT_PREFLIGHT, report
    _install_transaction(ctx, args.source, ownership)
    verify, next_steps = _verify(ctx, args.timeout)
    if verify["directly_usable"]:
        status, exit_code = "ok", INSTALL_EXIT_OK
    else:
        status, exit_code = "requires_restart", INSTALL_EXIT_REQUIRES_RESTART
    report = _base_report(ctx, status, args.command)
    report["receipt_path"] = str(ctx.receipt_path)
    report["verify"] = verify
    report["next_steps"] = next_steps
    report["steps"] = [
        {"id": "preflight", "status": "ok"},
        {"id": "acquire", "status": "ok"},
        {"id": "stage", "status": "ok"},
        {"id": "commit", "status": "ok"},
        {"id": "verify", "status": "ok" if verify["directly_usable"] else "deferred"},
    ]
    return exit_code, report


def _run_status(ctx: InstallContext) -> Tuple[int, Dict[str, Any]]:
    if ctx.state == "fresh":
        report = _failure_report(ctx, "status", "preflight", "not_installed")
        report["next_steps"] = [_next_command(ctx, "install", "install", "No receipted installation exists.")]
        return INSTALL_EXIT_PREFLIGHT, report
    if ctx.state == "partial":
        report = _failure_report(
            ctx, "status", ctx.state_stage or "receipt", ctx.state_reason or "partial_install", "partial"
        )
        report["next_steps"] = [_next_command(ctx, "install", "repair", "Repair the partial installation.")]
        return INSTALL_EXIT_PREFLIGHT, report
    report = _base_report(ctx, "ok", "status")
    report["receipt_path"] = str(ctx.receipt_path)
    report["steps"] = [{"id": "receipt", "status": "ok"}, {"id": "startup_hook", "status": "ok"}]
    if ctx.state == "upgrade":
        report["next_steps"] = [_next_command(ctx, "upgrade", "upgrade", "The installed adapter version is stale.")]
    return INSTALL_EXIT_OK, report


def _run_verify(ctx: InstallContext, args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    verify, next_steps = _verify(ctx, args.timeout)
    report = _base_report(ctx, "ok" if verify["directly_usable"] else "failed", "verify")
    report["receipt_path"] = str(ctx.receipt_path) if ctx.receipt_path.is_file() else None
    report["verify"] = verify
    report["steps"] = [{"id": "verify", "status": "ok" if verify["directly_usable"] else "failed"}]
    report["next_steps"] = next_steps
    return (INSTALL_EXIT_OK if verify["directly_usable"] else INSTALL_EXIT_VERIFY), report


def _decode_previous_hook(receipt: Dict[str, Any]) -> Optional[bytes]:
    previous = receipt.get("previous_hook")
    if not isinstance(previous, dict) or not isinstance(previous.get("existed"), bool):
        raise LifecycleError(INSTALL_EXIT_INSTALL, "receipt", "receipt_ownership_invalid")
    try:
        content = base64.b64decode(str(previous.get("content_base64", "")).encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        raise LifecycleError(INSTALL_EXIT_INSTALL, "receipt", "receipt_ownership_invalid")
    if hashlib.sha256(content).hexdigest() != previous.get("sha256"):
        raise LifecycleError(INSTALL_EXIT_INSTALL, "receipt", "receipt_ownership_invalid")
    if previous["existed"]:
        if not _is_identity_record(previous.get("identity")):
            raise LifecycleError(INSTALL_EXIT_INSTALL, "receipt", "receipt_ownership_invalid")
    elif previous.get("identity") is not None:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "receipt", "receipt_ownership_invalid")
    return content if previous["existed"] else None


def _run_uninstall(ctx: InstallContext, args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    if not os.path.lexists(str(ctx.receipt_path)):
        ownership = _preflight_ownership(ctx)
        if os.path.lexists(str(ctx.hook_path)):
            return INSTALL_EXIT_PREFLIGHT, _failure_report(ctx, "uninstall", "receipt", "receipt_missing")
        report = _base_report(ctx, "ok", "uninstall")
        report["steps"] = [{"id": "uninstall", "status": "skipped"}]
        return INSTALL_EXIT_OK, report
    receipt = _read_receipt(ctx.receipt_path, required=True)
    assert receipt is not None
    ownership = _preflight_ownership(ctx)
    state, stage, reason = _inspect_state(ctx.receipt_path, ctx.hook_path)
    if state == "partial":
        return INSTALL_EXIT_INSTALL, _failure_report(
            ctx,
            "uninstall",
            stage or "receipt",
            reason or "receipt_ownership_invalid",
        )
    previous = _decode_previous_hook(receipt)
    _preflight_compatibility(ctx)
    if args.dry_run:
        return INSTALL_EXIT_OK, _plan(ctx, "uninstall")
    if not args.yes:
        return INSTALL_EXIT_PREFLIGHT, _failure_report(ctx, "uninstall", "preflight", "confirmation_required")
    prior_package_state = _snapshot_package_state(ctx)
    hook_before, receipt_before = ownership
    _require_snapshot_current(ctx.hook_path, hook_before)
    _require_snapshot_current(ctx.receipt_path, receipt_before)
    package_attempted = False
    committed: Dict[Path, FileSnapshot] = {}
    try:
        package_attempted = True
        _uninstall_package(ctx)
        _require_snapshot_current(ctx.hook_path, hook_before)
        _require_snapshot_current(ctx.receipt_path, receipt_before)
        _restore_if_snapshot(ctx.hook_path, previous, hook_before, committed)
        _unlink_if_snapshot(ctx.receipt_path, receipt_before, committed)
    except LifecycleError:
        if package_attempted:
            _rollback_committed(ctx, prior_package_state, hook_before, receipt_before, committed)
        raise
    except OSError as exc:
        if package_attempted:
            _rollback_committed(ctx, prior_package_state, hook_before, receipt_before, committed)
        if _is_windows_lock(exc):
            raise LifecycleError(INSTALL_EXIT_REQUIRES_RESTART, "uninstall", "windows_file_lock")
        raise LifecycleError(INSTALL_EXIT_INSTALL, "uninstall", "commit_failed")
    except Exception:
        if package_attempted:
            _rollback_committed(ctx, prior_package_state, hook_before, receipt_before, committed)
        raise LifecycleError(INSTALL_EXIT_INSTALL, "uninstall", "commit_failed")
    report = _base_report(ctx, "ok", "uninstall")
    report["receipt_path"] = None
    report["steps"] = [{"id": "receipt", "status": "consumed"}, {"id": "uninstall", "status": "ok"}]
    return INSTALL_EXIT_OK, report


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit exactly one Install SOP v1 JSON object.")
    parser.add_argument("--yes", action="store_true", help="Confirm mutation without an interactive prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Plan without changing package, hook, or receipt state.")
    parser.add_argument("--dcc-path", help="3ds Max installation directory or 3dsmax.exe path.")
    parser.add_argument("--python", help="Target 3ds Max Python interpreter path.")
    parser.add_argument("--startup-dir", help="3ds Max userStartupScripts directory.")
    parser.add_argument("--receipt-path", help="Override the owned Install SOP receipt path.")
    parser.add_argument("--source", choices=["pypi", "local"], default="pypi", help="Adapter package source.")
    parser.add_argument("--timeout", type=float, default=3.0, help="Bounded sidecar readiness timeout in seconds.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "status", "verify", "uninstall", "upgrade"):
        _add_common(subparsers.add_parser(command))
    return parser


def _emit(report: Dict[str, Any], json_output: bool) -> None:
    validate_public_report(report)
    if json_output:
        sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        sys.stdout.write("%s: %s\n" % (report.get("command"), report.get("status")))


def _fallback_context(args: argparse.Namespace) -> InstallContext:
    host = Path(args.dcc_path or ".").expanduser().resolve()
    python = Path(args.python or sys.executable).expanduser().resolve()
    startup = Path(args.startup_dir or ".").expanduser().resolve()
    receipt = Path(args.receipt_path or DEFAULT_RECEIPT_PATH).expanduser().resolve()
    return InstallContext(
        host,
        python,
        startup,
        startup / STARTUP_SCRIPT_NAME,
        receipt,
        "unknown",
        _core_version(),
        "partial",
        "preflight",
        "invalid_context",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        ctx = _context(args)
        if args.command in {"install", "upgrade"}:
            exit_code, report = _run_install(ctx, args)
        elif args.command == "status":
            exit_code, report = _run_status(ctx)
        elif args.command == "verify":
            exit_code, report = _run_verify(ctx, args)
        else:
            exit_code, report = _run_uninstall(ctx, args)
    except LifecycleError as exc:
        ctx = locals().get("ctx") or _fallback_context(args)
        status = "requires_restart" if exc.exit_code == INSTALL_EXIT_REQUIRES_RESTART else "failed"
        report = _failure_report(ctx, args.command, exc.stage, exc.reason, status)
        if exc.exit_code == INSTALL_EXIT_REQUIRES_RESTART:
            report["next_steps"] = [
                _next_command(ctx, "verify", "restart_3dsmax", "Restart 3ds Max before retrying verification.")
            ]
        exit_code = exc.exit_code
    except Exception:
        ctx = locals().get("ctx") or _fallback_context(args)
        exit_code, stage, reason = PUBLIC_OPERATION_FAILURES[args.command]
        report = _failure_report(ctx, args.command, stage, reason)
    _emit(report, args.json)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
