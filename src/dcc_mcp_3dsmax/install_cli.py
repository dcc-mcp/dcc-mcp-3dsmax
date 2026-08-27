"""Agent-first Install SOP v1 lifecycle for dcc-mcp-3dsmax.

The CLI owns only adapter installation state. Shared catalog routing and Core
orchestration remain in dcc-mcp-core.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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


class PackageMutex:
    """Cross-process ownership lock for one embedded Python environment."""

    def __init__(self, path: Path, stream: Any) -> None:
        self.path = path
        self.stream = stream

    def release(self) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


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


def _install_package(ctx: InstallContext, source: str) -> Dict[str, Dict[str, str]]:
    if source == "local":
        root = Path(__file__).resolve().parents[2]
        if not (root / "pyproject.toml").is_file():
            raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "local_source_unavailable")
        requirement = str(root)
    else:
        requirement = "dcc-mcp-3dsmax==%s" % ADAPTER_VERSION
    worker = r"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata

from pip._internal.cli.main import main as pip_main


def normalized_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def installed_records():
    values = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        version = dist.version
        if not name or not version:
            raise RuntimeError("distribution identity unavailable")
        normalized = normalized_name(str(name))
        if normalized in values:
            raise RuntimeError("duplicate distribution identity")
        direct_text = dist.read_text("direct_url.json") or ""
        direct = json.loads(direct_text) if direct_text else {}
        url = direct.get("url") if isinstance(direct, dict) else None
        requirement_value = (str(name) + " @ " + str(url)) if url else (str(name) + "==" + str(version))
        record_text = dist.read_text("RECORD") or ""
        record = {
            "name": str(name),
            "version": str(version),
            "requirement": requirement_value,
            "direct_url": direct,
            "metadata_location": os.path.abspath(str(getattr(dist, "_path", ""))),
            "record_sha256": hashlib.sha256(record_text.encode("utf-8")).hexdigest(),
        }
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        values[normalized] = {
            "requirement": requirement_value,
            "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
    return values


report_dir = tempfile.mkdtemp(prefix="dcc-mcp-pip-report-")
report_path = os.path.join(report_dir, "install.json")
try:
    return_code = pip_main(["install", "--upgrade", "--report", report_path, sys.argv[1]])
    if return_code:
        raise RuntimeError("pip install failed")
    with open(report_path, "r", encoding="utf-8") as stream:
        report = json.load(stream)
    installs = report.get("install")
    if not isinstance(installs, list):
        raise RuntimeError("pip report missing install records")
    evidence = {}
    for item in installs:
        metadata_item = item.get("metadata") if isinstance(item, dict) else None
        name = metadata_item.get("name") if isinstance(metadata_item, dict) else None
        version = metadata_item.get("version") if isinstance(metadata_item, dict) else None
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise RuntimeError("pip report missing distribution identity")
        normalized = normalized_name(name)
        if normalized in evidence:
            raise RuntimeError("pip report contains duplicate distribution")
        download_info = item.get("download_info") if isinstance(item, dict) else None
        if not isinstance(download_info, dict):
            raise RuntimeError("pip report missing distribution provenance")
        direct_url = download_info.get("url")
        if item.get("is_direct") is True:
            if not isinstance(direct_url, str) or not direct_url:
                raise RuntimeError("pip report missing direct distribution provenance")
            requirement_value = "%s @ %s" % (name, direct_url)
        else:
            requirement_value = "%s==%s" % (name, version)
        evidence[normalized] = {
            "requirement": requirement_value,
            "provenance": json.dumps(download_info, sort_keys=True, separators=(",", ":")),
        }
    installed = installed_records()
    for normalized, item in evidence.items():
        installed_item = installed.get(normalized)
        if not isinstance(installed_item, dict) or installed_item.get("requirement") != item["requirement"]:
            raise RuntimeError("installed distribution does not match pip report")
        fingerprint = installed_item.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise RuntimeError("installed distribution fingerprint unavailable")
        item["fingerprint"] = fingerprint
    print("DCC_MCP_INSTALL_EVIDENCE=" + json.dumps({"evidence": evidence}, sort_keys=True, separators=(",", ":")))
finally:
    shutil.rmtree(report_dir, ignore_errors=True)
"""
    try:
        completed = subprocess.run(
            [str(ctx.python_path), "-c", worker, requirement],
            cwd=str(root) if source == "local" else None,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=300,
        )
        if len(completed.stdout.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("pip worker output too large")
        marker = "DCC_MCP_INSTALL_EVIDENCE="
        lines = [line for line in completed.stdout.splitlines() if line.startswith(marker)]
        if len(lines) != 1:
            raise ValueError("pip worker evidence unavailable")
        payload = json.loads(lines[0][len(marker) :])
        evidence = payload.get("evidence") if isinstance(payload, dict) else None
        if not isinstance(evidence, dict):
            raise ValueError("pip worker evidence unavailable")
        for name, item in evidence.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(item, dict)
                or not isinstance(item.get("requirement"), str)
                or not isinstance(item.get("provenance"), str)
                or not isinstance(item.get("fingerprint"), str)
            ):
                raise ValueError("pip worker evidence unavailable")
        return evidence
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "package_install_failed")


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


def _run_target_owned_pip_command(
    ctx: InstallContext,
    command: Sequence[str],
    token_path: Path,
    token: str,
) -> None:
    """Revalidate and mutate inside one target-interpreter process."""

    worker = r"""
import hashlib
import json
import os
import sys

try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata


def fingerprints():
    values = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        version = dist.version
        if not name or not version:
            raise RuntimeError("distribution identity unavailable")
        normalized = str(name).lower().replace("_", "-").replace(".", "-")
        if normalized in values:
            raise RuntimeError("duplicate distribution identity")
        direct_text = dist.read_text("direct_url.json") or ""
        direct = json.loads(direct_text) if direct_text else {}
        url = direct.get("url") if isinstance(direct, dict) else None
        requirement = (str(name) + " @ " + str(url)) if url else (str(name) + "==" + str(version))
        record_text = dist.read_text("RECORD") or ""
        record = {
            "name": str(name),
            "version": str(version),
            "requirement": requirement,
            "direct_url": direct,
            "metadata_location": os.path.abspath(str(getattr(dist, "_path", ""))),
            "record_sha256": hashlib.sha256(record_text.encode("utf-8")).hexdigest(),
        }
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        values[normalized] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return values


token_path = sys.argv[1]
before_token = os.lstat(token_path)
if not os.path.isfile(token_path) or before_token.st_nlink != 1 or getattr(before_token, "st_file_attributes", 0) & 0x400:
    raise SystemExit(90)
with open(token_path, "r", encoding="utf-8") as stream:
    opened_token = os.fstat(stream.fileno())
    ownership = json.load(stream)
after_token = os.lstat(token_path)
token_identity = (int(before_token.st_dev), int(before_token.st_ino))
if token_identity != (int(opened_token.st_dev), int(opened_token.st_ino)) or token_identity != (
    int(after_token.st_dev), int(after_token.st_ino)
):
    raise SystemExit(90)
if ownership.get("token") != sys.argv[2]:
    raise SystemExit(91)
interpreter = os.stat(sys.executable)
environment = os.stat(sys.prefix)
worker_identity = {
    "interpreter": [int(interpreter.st_dev), int(interpreter.st_ino)],
    "environment": [int(environment.st_dev), int(environment.st_ino)],
}
if ownership.get("package_identity") not in (worker_identity, {"interpreter": worker_identity["interpreter"]}):
    raise SystemExit(91)
if fingerprints() != ownership.get("before"):
    raise SystemExit(92)
from pip._internal.cli.main import main as pip_main
result = pip_main(json.loads(sys.argv[3]))
if result:
    raise SystemExit(int(result))
after = fingerprints()
if after != ownership.get("after"):
    raise SystemExit(93)
print(json.dumps(after, sort_keys=True, separators=(",", ":")))
"""
    try:
        completed = subprocess.run(
            [str(ctx.python_path), "-c", worker, str(token_path), token, json.dumps(list(command))],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=300,
        )
        observed = json.loads(completed.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, json.JSONDecodeError):
        raise RuntimeError("target package mutation failed")
    if not isinstance(observed, dict):
        raise RuntimeError("target package mutation result unavailable")


def _package_ownership_identity(ctx: InstallContext) -> Dict[str, List[int]]:
    try:
        resolved = ctx.python_path.resolve(strict=True)
        interpreter_stat = os.stat(str(resolved))
        interpreter_reparse = bool(getattr(interpreter_stat, "st_file_attributes", 0) & 0x400)
        if not stat.S_ISREG(interpreter_stat.st_mode) or interpreter_reparse:
            raise OSError("package ownership identity is unsafe")
        identity = {"interpreter": list(_file_identity(interpreter_stat))}
        try:
            completed = subprocess.run(
                [
                    str(ctx.python_path),
                    "-c",
                    "import json,os,sys;s=os.stat(sys.prefix);print(json.dumps([int(s.st_dev),int(s.st_ino)]))",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
            )
            environment_identity = json.loads(completed.stdout.strip().splitlines()[-1])
            if (
                not isinstance(environment_identity, list)
                or len(environment_identity) != 2
                or any(type(value) is not int for value in environment_identity)
            ):
                raise ValueError("invalid target environment identity")
            identity["environment"] = environment_identity
        except (OSError, subprocess.SubprocessError, ValueError, IndexError, json.JSONDecodeError):
            # Discovery tests use a non-executable placeholder. Production
            # compatibility preflight rejects such a target before mutation.
            pass
        return identity
    except (OSError, RuntimeError, ValueError):
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_ownership_identity_unavailable")


def _package_mutex_path(ctx: InstallContext) -> Path:
    identity = _package_ownership_identity(ctx)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / ("dcc-mcp-3dsmax-package-%s.lock" % key)


def _acquire_package_mutex(ctx: InstallContext, timeout: float = 10.0) -> PackageMutex:
    path = _package_mutex_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
        os.fsync(stream.fileno())
    deadline = time.monotonic() + timeout
    while True:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            mutex = PackageMutex(path, stream)
            try:
                _reconcile_package_commit_token(ctx, mutex)
                _reconcile_package_rollback_plan(ctx, mutex)
            except Exception:
                mutex.release()
                raise
            return mutex
        except (OSError, IOError):
            if time.monotonic() >= deadline:
                stream.close()
                raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_ownership_locked")
            time.sleep(0.05)


@contextlib.contextmanager
def _package_ownership(ctx: InstallContext, existing: Optional[PackageMutex] = None):
    if existing is not None:
        yield existing
        return
    mutex = _acquire_package_mutex(ctx)
    try:
        yield mutex
    finally:
        mutex.release()


def _package_rollback_before_pip(_ctx: InstallContext, _mutex: PackageMutex) -> None:
    """Deterministic test seam at the final ownership-to-pip boundary."""


def _package_rollback_commit_hook(_ctx: InstallContext, _mutex: PackageMutex) -> None:
    """Deterministic test seam immediately before the commit ownership check."""


def _package_pip_commit_hook(_ctx: InstallContext, _mutex: PackageMutex, _token: str) -> None:
    """Deterministic seam inside the durable pip mutation boundary."""


def _package_commit_path(mutex: PackageMutex) -> Path:
    return mutex.path.with_name(mutex.path.name + ".commit.json")


def _read_package_commit(path: Path) -> Dict[str, Any]:
    content, _identity = _read_independent_file(path)
    if len(content) > 1024 * 1024:
        raise RuntimeError("package commit token is too large")
    record = json.loads(content.decode("utf-8"))
    if (
        not isinstance(record, dict)
        or record.get("version") != 1
        or not isinstance(record.get("token"), str)
        or not isinstance(record.get("package_identity"), dict)
        or not isinstance(record.get("before"), dict)
        or not isinstance(record.get("after"), dict)
    ):
        raise RuntimeError("invalid package commit token")
    for fingerprints in (record["before"], record["after"]):
        if any(not isinstance(name, str) or not isinstance(value, str) for name, value in fingerprints.items()):
            raise RuntimeError("invalid package commit fingerprints")
    return record


def _reconcile_package_commit_token(ctx: InstallContext, mutex: PackageMutex) -> None:
    path = _package_commit_path(mutex)
    stages = sorted(path.parent.glob(path.name + ".stage-*"))
    if len(stages) > 1:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")
    if stages and os.path.lexists(str(path)):
        try:
            _collapse_private_publication(path, stages[0])
            stages = []
        except Exception:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")
    candidate = stages[0] if stages else path
    if not os.path.lexists(str(candidate)):
        return
    try:
        record = _read_package_commit(candidate)
        if record["package_identity"] != _package_ownership_identity(ctx):
            raise RuntimeError("package commit target mismatch")
        current = _snapshot_package_state(ctx).get("fingerprints")
        if current not in (record["before"], record["after"]):
            raise RuntimeError("package commit ownership changed")
        _durable_unlink(candidate)
    except Exception:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")


def _write_package_commit_token(
    ctx: InstallContext,
    mutex: PackageMutex,
    before: Dict[str, str],
    after: Dict[str, str],
) -> Tuple[str, FileSnapshot]:
    path = _package_commit_path(mutex)
    if os.path.lexists(str(path)) or list(path.parent.glob(path.name + ".stage-*")):
        raise RuntimeError("package commit token already exists")
    token = uuid.uuid4().hex
    stage = path.with_name(path.name + ".stage-" + token)
    payload = (
        json.dumps(
            {
                "version": 1,
                "token": token,
                "package_identity": _package_ownership_identity(ctx),
                "before": before,
                "after": after,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    with stage.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(str(stage), str(path))
    _fsync_directory(path.parent)
    _durable_unlink(stage)
    return token, _snapshot(path)


def _run_owned_pip_command(
    ctx: InstallContext,
    command: Sequence[str],
    before: Dict[str, str],
    after: Dict[str, str],
    mutex: PackageMutex,
) -> None:
    token, token_snapshot = _write_package_commit_token(ctx, mutex, before, after)
    path = _package_commit_path(mutex)
    _package_pip_commit_hook(ctx, mutex, token)
    _require_snapshot_current(path, token_snapshot)
    record = _read_package_commit(path)
    if record.get("token") != token or record.get("before") != before or record.get("after") != after:
        raise RuntimeError("package commit token changed")
    _require_snapshot_current(path, token_snapshot)
    current = _snapshot_package_state(ctx)
    if current.get("fingerprints") != before:
        raise RuntimeError("package transaction ownership changed")
    _run_target_owned_pip_command(ctx, command, path, token)
    committed = _snapshot_package_state(ctx)
    if committed.get("fingerprints") != after:
        raise RuntimeError("package state mismatch")
    _require_snapshot_current(path, token_snapshot)
    _remove_owned_file(path, token_snapshot)


def _package_rollback_plan_path(mutex: PackageMutex) -> Path:
    return mutex.path.with_name(mutex.path.name + ".rollback.json")


def _package_rollback_progress_path(mutex: PackageMutex, token: str, index: int) -> Path:
    return mutex.path.with_name("%s.rollback-%s-step-%d.json" % (mutex.path.name, token, index))


def _package_rollback_intent_path(mutex: PackageMutex, token: str, index: int) -> Path:
    return mutex.path.with_name("%s.rollback-%s-step-%d.intent.json" % (mutex.path.name, token, index))


def _write_private_json(path: Path, record: Dict[str, Any]) -> FileSnapshot:
    if os.path.lexists(str(path)):
        raise RuntimeError("private transaction record already exists")
    token = uuid.uuid4().hex
    stage = path.with_name(path.name + ".stage-" + token)
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with stage.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(str(stage), str(path))
    _fsync_directory(path.parent)
    _durable_unlink(stage)
    return _snapshot(path)


def _read_package_rollback_plan(path: Path) -> Tuple[Dict[str, Any], FileSnapshot]:
    snapshot = _snapshot(path)
    content, identity = _read_independent_file(path)
    if identity != snapshot.identity or len(content) > 1024 * 1024:
        raise RuntimeError("invalid package rollback plan identity")
    record = json.loads(content.decode("utf-8"))
    if (
        not isinstance(record, dict)
        or record.get("version") != 1
        or not isinstance(record.get("token"), str)
        or not isinstance(record.get("package_identity"), dict)
        or not isinstance(record.get("initial"), dict)
        or not isinstance(record.get("final"), dict)
        or not isinstance(record.get("steps"), list)
        or not record["steps"]
    ):
        raise RuntimeError("invalid package rollback plan")
    for step in record["steps"]:
        if (
            not isinstance(step, dict)
            or not isinstance(step.get("command"), list)
            or not all(isinstance(item, str) for item in step["command"])
            or not isinstance(step.get("before"), dict)
            or not isinstance(step.get("after"), dict)
        ):
            raise RuntimeError("invalid package rollback step")
    return record, snapshot


def _write_package_rollback_plan(
    ctx: InstallContext,
    mutex: PackageMutex,
    steps: Sequence[Tuple[Sequence[str], Dict[str, str], Dict[str, str]]],
) -> Tuple[Dict[str, Any], FileSnapshot]:
    token = uuid.uuid4().hex
    record = {
        "version": 1,
        "token": token,
        "package_identity": _package_ownership_identity(ctx),
        "initial": steps[0][1],
        "final": steps[-1][2],
        "steps": [{"command": list(command), "before": before, "after": after} for command, before, after in steps],
    }
    path = _package_rollback_plan_path(mutex)
    snapshot = _write_private_json(path, record)
    return record, snapshot


def _write_package_rollback_progress(
    mutex: PackageMutex, plan: Dict[str, Any], index: int
) -> Tuple[Path, FileSnapshot]:
    path = _package_rollback_progress_path(mutex, plan["token"], index)
    record = {
        "version": 1,
        "token": plan["token"],
        "index": index,
        "after": plan["steps"][index]["after"],
    }
    return path, _write_private_json(path, record)


def _write_package_rollback_intent(mutex: PackageMutex, plan: Dict[str, Any], index: int) -> Tuple[Path, FileSnapshot]:
    path = _package_rollback_intent_path(mutex, plan["token"], index)
    step = plan["steps"][index]
    record = {
        "version": 1,
        "token": plan["token"],
        "index": index,
        "command": step["command"],
        "before": step["before"],
        "after": step["after"],
    }
    return path, _write_private_json(path, record)


def _read_package_rollback_record(path: Path, expected: Dict[str, Any]) -> FileSnapshot:
    snapshot = _snapshot(path)
    content, identity = _read_independent_file(path)
    record = json.loads(content.decode("utf-8"))
    if identity != snapshot.identity or record != expected:
        raise RuntimeError("package rollback record mismatch")
    return snapshot


def _recover_package_rollback_record_publication(path: Path) -> None:
    stages = sorted(path.parent.glob(path.name + ".stage-*"))
    if len(stages) > 1:
        raise RuntimeError("ambiguous package rollback record publication")
    if stages:
        _finish_private_publication(path, stages[0])


def _cleanup_package_rollback_plan(
    mutex: PackageMutex,
    plan: Dict[str, Any],
    plan_snapshot: FileSnapshot,
    progress: Sequence[Tuple[Path, FileSnapshot]],
    intents: Sequence[Tuple[Path, FileSnapshot]] = (),
) -> None:
    for path, snapshot in reversed(list(intents)):
        _remove_owned_file(path, snapshot)
    for path, snapshot in reversed(list(progress)):
        _remove_owned_file(path, snapshot)
    _remove_owned_file(_package_rollback_plan_path(mutex), plan_snapshot)


def _reconcile_package_rollback_plan(ctx: InstallContext, mutex: PackageMutex) -> None:
    path = _package_rollback_plan_path(mutex)
    plan_stages = sorted(path.parent.glob(path.name + ".stage-*"))
    progress_stages = list(path.parent.glob(mutex.path.name + ".rollback-*-step-*.json.stage-*"))
    if len(plan_stages) > 1:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")
    if plan_stages and os.path.lexists(str(path)):
        try:
            _collapse_private_publication(path, plan_stages[0])
            plan_stages = []
        except Exception:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")
    if plan_stages:
        try:
            staged_plan, staged_snapshot = _read_package_rollback_plan(plan_stages[0])
            if staged_plan["package_identity"] != _package_ownership_identity(ctx):
                raise RuntimeError("package rollback target mismatch")
            if _snapshot_package_state(ctx).get("fingerprints") != staged_plan["initial"]:
                raise RuntimeError("unpublished package rollback already mutated state")
            _remove_owned_file(plan_stages[0], staged_snapshot)
            return
        except Exception:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")
    if not os.path.lexists(str(path)):
        orphan_progress = list(path.parent.glob(mutex.path.name + ".rollback-*-step-*.json"))
        if orphan_progress or progress_stages:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")
        return
    try:
        plan, plan_snapshot = _read_package_rollback_plan(path)
        if plan["package_identity"] != _package_ownership_identity(ctx):
            raise RuntimeError("package rollback target mismatch")
        progress: List[Tuple[Path, FileSnapshot]] = []
        intents: List[Tuple[Path, FileSnapshot]] = []
        completed = 0
        for index, step in enumerate(plan["steps"]):
            progress_path = _package_rollback_progress_path(mutex, plan["token"], index)
            intent_path = _package_rollback_intent_path(mutex, plan["token"], index)
            _recover_package_rollback_record_publication(progress_path)
            _recover_package_rollback_record_publication(intent_path)
            if os.path.lexists(str(progress_path)):
                if index != completed:
                    raise RuntimeError("package rollback progress is not contiguous")
                progress_snapshot = _read_package_rollback_record(
                    progress_path,
                    {"version": 1, "token": plan["token"], "index": index, "after": step["after"]},
                )
                progress.append((progress_path, progress_snapshot))
                completed += 1
            if os.path.lexists(str(intent_path)):
                intent_snapshot = _read_package_rollback_record(
                    intent_path,
                    {
                        "version": 1,
                        "token": plan["token"],
                        "index": index,
                        "command": step["command"],
                        "before": step["before"],
                        "after": step["after"],
                    },
                )
                intents.append((intent_path, intent_snapshot))
                if index < completed:
                    _remove_owned_file(intent_path, intent_snapshot)
                    intents.pop()
                elif index != completed:
                    raise RuntimeError("package rollback intent is not contiguous")
        for index in range(completed, len(plan["steps"])):
            step = plan["steps"][index]
            intent_path = _package_rollback_intent_path(mutex, plan["token"], index)
            intent = next((item for item in intents if item[0] == intent_path), None)
            if intent is None:
                intent = _write_package_rollback_intent(mutex, plan, index)
                intents.append(intent)
            current = _snapshot_package_state(ctx).get("fingerprints")
            if current == step["before"]:
                _run_owned_pip_command(ctx, step["command"], step["before"], step["after"], mutex)
            elif current != step["after"]:
                raise RuntimeError("package rollback command ownership changed")
            progress_item = _write_package_rollback_progress(mutex, plan, index)
            progress.append(progress_item)
            _remove_owned_file(intent[0], intent[1])
            intents.remove(intent)
        if _snapshot_package_state(ctx).get("fingerprints") != plan["final"]:
            raise RuntimeError("package rollback remains incomplete")
        _cleanup_package_rollback_plan(mutex, plan, plan_snapshot, progress, intents)
    except Exception:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")


def _package_state_for_mutations(
    prior: Dict[str, Any], after: Dict[str, Any], mutation_names: Set[str]
) -> Dict[str, Any]:
    """Project an observed package state onto the names pip reports mutating."""

    by_name = dict(prior["by_name"])
    fingerprints = dict(prior["fingerprints"])
    for raw_name in mutation_names:
        name = _normalized_distribution_name(raw_name)
        if name in after["by_name"]:
            by_name[name] = after["by_name"][name]
            fingerprints[name] = after["fingerprints"][name]
        else:
            by_name.pop(name, None)
            fingerprints.pop(name, None)
    requirements = tuple(sorted(by_name.values()))
    canonical = "\n".join("%s:%s" % item for item in sorted(fingerprints.items()))
    return {
        "requirements": requirements,
        "by_name": by_name,
        "fingerprints": fingerprints,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _restore_package_state(
    ctx: InstallContext,
    prior: Dict[str, Any],
    transaction_owned: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        inherited_mutex = prior.get("_package_mutex")
        with _package_ownership(ctx, inherited_mutex) as package_mutex:
            _restore_package_state_locked(ctx, prior, transaction_owned, package_mutex)
    except Exception:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "rollback", "package_rollback_incomplete")


def _restore_package_state_locked(
    ctx: InstallContext,
    prior: Dict[str, Any],
    transaction_owned: Optional[Dict[str, Any]],
    package_mutex: PackageMutex,
) -> None:
    try:
        if transaction_owned is None:
            transaction_owned = prior.get("_transaction_owned")
        legacy_unowned = transaction_owned is None and not prior.get("_ownership_required")
        if prior.get("_ownership_required") and transaction_owned is None:
            raise RuntimeError("package transaction ownership unavailable")
        current = _snapshot_package_state(ctx)
        prior_by_name = prior["by_name"]
        current_by_name = current["by_name"]
        prior_fingerprints = prior["fingerprints"]
        current_fingerprints = current["fingerprints"]
        if transaction_owned is None:
            remove = sorted(name for name in current_by_name if name not in prior_by_name)
            restore = sorted(
                requirement
                for name, requirement in prior_by_name.items()
                if current_fingerprints.get(name) != prior_fingerprints.get(name)
            )
            expected_fingerprints = dict(prior_fingerprints)
        else:
            owned_fingerprints = transaction_owned["fingerprints"]
            changed_names = {
                name
                for name in set(prior_fingerprints) | set(owned_fingerprints)
                if prior_fingerprints.get(name) != owned_fingerprints.get(name)
            }
            cas_names = {
                name for name in changed_names if current_fingerprints.get(name) == owned_fingerprints.get(name)
            }
            remove = sorted(name for name in cas_names if name not in prior_by_name)
            restore = sorted(prior_by_name[name] for name in cas_names if name in prior_by_name)
            expected_fingerprints = dict(current_fingerprints)
            for name in cas_names:
                if name in prior_fingerprints:
                    expected_fingerprints[name] = prior_fingerprints[name]
                else:
                    expected_fingerprints.pop(name, None)
            if remove or restore:
                confirmed = _snapshot_package_state(ctx)
                if confirmed.get("fingerprints") != current_fingerprints:
                    raise RuntimeError("package transaction ownership changed")
                _package_rollback_before_pip(ctx, package_mutex)
                final_owner = _snapshot_package_state(ctx)
                if final_owner.get("fingerprints") != current_fingerprints:
                    raise RuntimeError("package transaction ownership changed")
                _package_rollback_commit_hook(ctx, package_mutex)
        if legacy_unowned:
            if remove:
                _run_pip_command(ctx, ["uninstall", "-y"] + remove)
            if restore:
                _run_pip_command(ctx, ["install", "--no-deps", "--force-reinstall"] + restore)
            restored = _snapshot_package_state(ctx)
            if restored.get("fingerprints") != expected_fingerprints:
                raise RuntimeError("package state mismatch")
            return
        command_state = dict(current_fingerprints)
        rollback_steps: List[Tuple[Sequence[str], Dict[str, str], Dict[str, str]]] = []
        if remove:
            after_remove = dict(command_state)
            for name in remove:
                after_remove.pop(name, None)
            rollback_steps.append((["uninstall", "-y"] + remove, command_state, after_remove))
            command_state = after_remove
        if restore:
            after_restore = dict(command_state)
            for requirement in restore:
                name = _frozen_requirement_name(requirement)
                if name is None or name not in prior_fingerprints:
                    raise RuntimeError("package restore identity unavailable")
                after_restore[name] = prior_fingerprints[name]
            rollback_steps.append(
                (
                    ["install", "--no-deps", "--force-reinstall"] + restore,
                    command_state,
                    after_restore,
                )
            )
            command_state = after_restore
        if command_state != expected_fingerprints:
            raise RuntimeError("package state mismatch")
        if rollback_steps:
            plan, plan_snapshot = _write_package_rollback_plan(ctx, package_mutex, rollback_steps)
            progress: List[Tuple[Path, FileSnapshot]] = []
            for index, (command, before, after) in enumerate(rollback_steps):
                intent = _write_package_rollback_intent(package_mutex, plan, index)
                _run_owned_pip_command(ctx, command, before, after, package_mutex)
                progress.append(_write_package_rollback_progress(package_mutex, plan, index))
                _remove_owned_file(intent[0], intent[1])
            _cleanup_package_rollback_plan(package_mutex, plan, plan_snapshot, progress)
    except Exception:
        raise


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


def _fsync_directory(directory: Path) -> None:
    """Durably order same-directory metadata changes on the host platform."""

    if os.name != "nt":
        descriptor = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    import ctypes
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(directory),
        0x80000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        _windows_directory_barrier(directory)
        return
    try:
        if not ctypes.windll.kernel32.FlushFileBuffers(handle):
            _windows_directory_barrier(directory)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _windows_directory_barrier(directory: Path) -> None:
    """Order Windows directory metadata through a write-through same-dir move."""

    import ctypes

    token = uuid.uuid4().hex
    source = directory / (".dcc-mcp-dirsync-%s.tmp" % token)
    target = directory / (".dcc-mcp-dirsync-%s.done" % token)
    with source.open("xb") as stream:
        stream.write(b"0")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if not ctypes.windll.kernel32.MoveFileExW(str(source), str(target), 0x00000008):
            raise ctypes.WinError()
        target.unlink()
    finally:
        for candidate in (source, target):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def _durable_unlink(path: Path) -> None:
    path.unlink()
    _fsync_directory(path.parent)


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
    _fsync_directory(path.parent)
    _durable_unlink(claim)


def _snapshot_record(snapshot: Any) -> Dict[str, Any]:
    value = _coerce_file_snapshot(snapshot)
    return {
        "existed": value.existed,
        "content_base64": base64.b64encode(value.content).decode("ascii"),
        "sha256": value.sha256,
        "identity": _identity_record(value.identity),
        "independent": value.independent,
    }


def _snapshot_from_record(record: Any) -> FileSnapshot:
    if not isinstance(record, dict) or type(record.get("existed")) is not bool:
        raise ValueError("invalid transaction snapshot")
    encoded = record.get("content_base64")
    sha256 = record.get("sha256")
    independent = record.get("independent")
    identity_record = record.get("identity")
    if not isinstance(encoded, str) or not isinstance(sha256, str) or type(independent) is not bool:
        raise ValueError("invalid transaction snapshot")
    content = base64.b64decode(encoded.encode("ascii"), validate=True)
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError("invalid transaction snapshot")
    if identity_record is None:
        identity = None
    elif _is_identity_record(identity_record):
        identity = (identity_record["device"], identity_record["inode"])
    else:
        raise ValueError("invalid transaction snapshot")
    if (not record["existed"] and (identity is not None or content)) or (
        record["existed"] and independent and identity is None
    ):
        raise ValueError("invalid transaction snapshot")
    return FileSnapshot(record["existed"], content, sha256, identity, independent)


def _write_file_transaction(
    destination: Path,
    expected: Any,
    desired: Any,
    transaction_stage: Optional[Path] = None,
) -> Tuple[Path, FileSnapshot, Path, Path, Path, Optional[Path]]:
    token = uuid.uuid4().hex
    journal_path = destination.with_name(".%s.transaction-%s.json" % (destination.name, token))
    claim_path = destination.with_name(".%s.claim-%s" % (destination.name, token))
    recovery_path = destination.with_name(".%s.recovery-%s" % (destination.name, token))
    commit_path = destination.with_name(".%s.committed-%s.json" % (destination.name, token))
    journal_stage = destination.with_name(".%s.journal-stage-%s" % (destination.name, token))
    desired_snapshot = _coerce_file_snapshot(desired)
    if transaction_stage is not None:
        stage_snapshot = _snapshot(transaction_stage)
        if _snapshot_record(stage_snapshot) != _snapshot_record(desired_snapshot):
            raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")
    else:
        stage_snapshot = None
    record = {
        "version": 2,
        "destination": str(destination),
        "claim_path": str(claim_path),
        "recovery_path": str(recovery_path),
        "commit_path": str(commit_path),
        "stage_path": str(transaction_stage) if transaction_stage is not None else None,
        "stage": _snapshot_record(stage_snapshot) if stage_snapshot is not None else None,
        "expected": _snapshot_record(expected),
        "desired": _snapshot_record(desired_snapshot),
    }
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        with journal_stage.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(str(journal_stage), str(journal_path))
        _fsync_directory(destination.parent)
        _durable_unlink(journal_stage)
        journal_snapshot = _snapshot(journal_path)
        return journal_path, journal_snapshot, claim_path, recovery_path, commit_path, transaction_stage
    finally:
        try:
            journal_stage.unlink()
        except FileNotFoundError:
            pass


def _file_transaction_after_publish(_path: Path, _desired: FileSnapshot) -> None:
    """Deterministic test seam after publish and before identity post-check."""


def _decode_file_transaction(
    content: bytes, destination: Path, token: str
) -> Tuple[FileSnapshot, FileSnapshot, Path, Path, Path, Optional[Path], Optional[FileSnapshot]]:
    if len(content) > 1024 * 1024:
        raise ValueError("transaction journal too large")
    record = json.loads(content.decode("utf-8"))
    if not isinstance(record, dict) or record.get("version") != 2:
        raise ValueError("invalid transaction journal")
    claim_path = destination.with_name(".%s.claim-%s" % (destination.name, token))
    recovery_path = destination.with_name(".%s.recovery-%s" % (destination.name, token))
    commit_path = destination.with_name(".%s.committed-%s.json" % (destination.name, token))
    stage_value = record.get("stage_path")
    stage_path = _lexical_absolute_path(stage_value) if stage_value is not None else None
    if (
        _lexical_path_key(record.get("destination")) != _lexical_path_key(destination)
        or _lexical_path_key(record.get("claim_path")) != _lexical_path_key(claim_path)
        or _lexical_path_key(record.get("recovery_path")) != _lexical_path_key(recovery_path)
        or _lexical_path_key(record.get("commit_path")) != _lexical_path_key(commit_path)
        or (stage_path is not None and stage_path.parent != destination.parent)
    ):
        raise ValueError("invalid transaction journal")
    stage_record = record.get("stage")
    return (
        _snapshot_from_record(record.get("expected")),
        _snapshot_from_record(record.get("desired")),
        claim_path,
        recovery_path,
        commit_path,
        stage_path,
        _snapshot_from_record(stage_record) if stage_record is not None else None,
    )


def _read_file_transaction(
    journal_path: Path, destination: Path
) -> Tuple[FileSnapshot, FileSnapshot, Path, Path, Path, Optional[Path], Optional[FileSnapshot]]:
    token_match = re.fullmatch(
        r"\.%s\.transaction-([0-9a-f]{32})\.json" % re.escape(destination.name), journal_path.name
    )
    if token_match is None:
        raise ValueError("invalid transaction journal")
    content, _identity = _read_independent_file(journal_path)
    return _decode_file_transaction(content, destination, token_match.group(1))


def _owned_snapshot_matches(path: Path, expected: Any) -> bool:
    """Match an exact transaction identity while allowing its temporary hardlink."""

    snapshot = _coerce_file_snapshot(expected)
    if not snapshot.existed:
        return not os.path.lexists(str(path))
    if snapshot.identity is None or not os.path.lexists(str(path)):
        return False
    try:
        before = os.lstat(str(path))
        reparse = bool(getattr(before, "st_file_attributes", 0) & 0x400)
        if not stat.S_ISREG(before.st_mode) or reparse or _file_identity(before) != snapshot.identity:
            return False
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(opened) != snapshot.identity:
                return False
            content = stream.read()
            after_open = os.fstat(stream.fileno())
        after = os.lstat(str(path))
        return (
            _file_identity(after_open) == snapshot.identity
            and _file_identity(after) == snapshot.identity
            and content == snapshot.content
            and hashlib.sha256(content).hexdigest() == snapshot.sha256
        )
    except Exception:
        return False


def _write_committed_marker(commit_path: Path, destination: Path, desired: Any) -> FileSnapshot:
    stage = commit_path.with_name(commit_path.name + ".stage")
    payload = (
        json.dumps(
            {
                "version": 1,
                "destination": str(destination),
                "desired": _snapshot_record(desired),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        with stage.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(str(stage), str(commit_path))
        _fsync_directory(commit_path.parent)
        _durable_unlink(stage)
        return _snapshot(commit_path)
    finally:
        try:
            stage.unlink()
        except FileNotFoundError:
            pass


def _decode_committed_marker(content: bytes, destination: Path, desired: Optional[Any] = None) -> FileSnapshot:
    record = json.loads(content.decode("utf-8"))
    if not isinstance(record, dict):
        raise ValueError("invalid transaction commit marker")
    recorded_desired = _snapshot_from_record(record.get("desired"))
    if (
        record.get("version") != 1
        or _lexical_path_key(record.get("destination")) != _lexical_path_key(destination)
        or (desired is not None and _snapshot_record(recorded_desired) != _snapshot_record(desired))
    ):
        raise ValueError("invalid transaction commit marker")
    return recorded_desired


def _read_committed_marker(commit_path: Path, destination: Path, desired: Any) -> FileSnapshot:
    marker = _snapshot(commit_path)
    content, _identity = _read_independent_file(commit_path)
    _decode_committed_marker(content, destination, desired)
    return marker


def _collapse_private_publication(primary: Path, stage: Path) -> None:
    """Finish an interrupted same-directory hard-link publication.

    Both names are private, deterministically paired transaction artifacts.
    Only the exact two-link regular-file state created by our publisher is
    recoverable; every foreign alias or identity mismatch remains fail closed.
    """

    if not os.path.lexists(str(stage)):
        return
    if not os.path.lexists(str(primary)):
        raise RuntimeError("published transaction artifact is missing")
    primary_stat = os.lstat(str(primary))
    stage_stat = os.lstat(str(stage))
    primary_reparse = bool(getattr(primary_stat, "st_file_attributes", 0) & 0x400)
    stage_reparse = bool(getattr(stage_stat, "st_file_attributes", 0) & 0x400)
    if (
        not stat.S_ISREG(primary_stat.st_mode)
        or not stat.S_ISREG(stage_stat.st_mode)
        or primary_reparse
        or stage_reparse
        or _file_identity(primary_stat) != _file_identity(stage_stat)
        or primary_stat.st_nlink != 2
        or stage_stat.st_nlink != 2
    ):
        raise RuntimeError("transaction publication identity mismatch")
    _durable_unlink(stage)


def _finish_private_publication(primary: Path, stage: Path) -> None:
    """Durably finish a validated private stage publication without clobbering."""

    if os.path.lexists(str(primary)):
        _collapse_private_publication(primary, stage)
        return
    if not os.path.lexists(str(stage)):
        raise RuntimeError("transaction publication stage is missing")
    _snapshot(stage)
    os.link(str(stage), str(primary))
    _fsync_directory(primary.parent)
    _durable_unlink(stage)


def _remove_owned_file(path: Path, expected: Any) -> None:
    delete_claim = path.with_name(".%s.delete-%s" % (path.name, uuid.uuid4().hex))
    claimed = _claim_file_if_snapshot(path, expected, delete_claim)
    if claimed is not None:
        _durable_unlink(claimed)


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


def _claim_file_if_snapshot(
    path: Path,
    expected: Any,
    claim_path: Optional[Path] = None,
) -> Optional[Path]:
    """Atomically remove and return the exact expected identity from *path*.

    Renaming to a same-directory private name is the platform CAS claim.  A
    replacement that wins before the rename is detected by identity validation
    and restored without overwriting any newer path occupant.
    """

    snapshot = _coerce_file_snapshot(expected)
    if not snapshot.existed:
        _require_snapshot_current(path, snapshot)
        return None
    claim = claim_path or path.with_name(".%s.claim-%s" % (path.name, uuid.uuid4().hex))
    try:
        os.replace(str(path), str(claim))
        _fsync_directory(path.parent)
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
    (
        journal_path,
        journal_snapshot,
        claim_path,
        _recovery_path,
        commit_path,
        _stage_path,
    ) = _write_file_transaction(destination, expected, staged, source)
    claim = _claim_file_if_snapshot(destination, expected, claim_path)
    removed = _absent_file_snapshot()
    committed[destination] = removed
    try:
        # The platform publish is no-clobber and leaves one independent final
        # identity after removing the same-directory stage.
        _replace_file(source, destination)
        committed[destination] = staged
        _fsync_directory(destination.parent)
        commit_snapshot = _write_committed_marker(commit_path, destination, staged)
        _durable_unlink(source)
        if claim is not None:
            _durable_unlink(claim)
        _file_transaction_after_publish(destination, staged)
        _require_snapshot_current(destination, staged)
        _remove_owned_file(journal_path, journal_snapshot)
        _remove_owned_file(commit_path, commit_snapshot)
        return staged
    except FileExistsError:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")
    except Exception:
        try:
            _recover_claim(claim, destination, expected, committed)
            if _snapshot_is_current(destination, expected):
                _remove_owned_file(journal_path, journal_snapshot)
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
    restore_stage = path.with_name(".%s.restore-stage-%s" % (path.name, uuid.uuid4().hex))
    path.parent.mkdir(parents=True, exist_ok=True)
    with restore_stage.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(path.parent)
    desired = _snapshot(restore_stage)
    (
        journal_path,
        journal_snapshot,
        claim_path,
        _recovery_path,
        commit_path,
        _stage_path,
    ) = _write_file_transaction(path, expected, desired, restore_stage)
    claim = _claim_file_if_snapshot(path, expected, claim_path)
    removed = _absent_file_snapshot()
    committed[path] = removed
    try:
        _replace_file(restore_stage, path)
        committed[path] = desired
        _fsync_directory(path.parent)
        commit_snapshot = _write_committed_marker(commit_path, path, desired)
        _durable_unlink(restore_stage)
        if claim is not None:
            _durable_unlink(claim)
        _file_transaction_after_publish(path, desired)
        _require_snapshot_current(path, desired)
        _remove_owned_file(journal_path, journal_snapshot)
        _remove_owned_file(commit_path, commit_snapshot)
        return desired
    except FileExistsError:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "commit", "file_identity_changed")
    except Exception:
        try:
            _recover_claim(claim, path, expected, committed)
            if _snapshot_is_current(path, expected):
                _remove_owned_file(journal_path, journal_snapshot)
        except Exception:
            pass
        raise


def _unlink_if_snapshot(
    path: Path,
    expected: Any,
    committed: Dict[Path, FileSnapshot],
) -> FileSnapshot:
    removed = _absent_file_snapshot()
    (
        journal_path,
        journal_snapshot,
        claim_path,
        _recovery_path,
        commit_path,
        _stage_path,
    ) = _write_file_transaction(path, expected, removed)
    claim = _claim_file_if_snapshot(path, expected, claim_path)
    committed[path] = removed
    try:
        _restore(path, None)
        commit_snapshot = _write_committed_marker(commit_path, path, removed)
        if claim is not None:
            _durable_unlink(claim)
        _require_snapshot_current(path, removed)
        _remove_owned_file(journal_path, journal_snapshot)
        _remove_owned_file(commit_path, commit_snapshot)
        return removed
    except Exception:
        try:
            _recover_claim(claim, path, expected, committed)
            if _snapshot_is_current(path, expected):
                _remove_owned_file(journal_path, journal_snapshot)
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
    with _package_ownership(ctx) as package_mutex:
        _install_transaction_locked(ctx, source, ownership, package_mutex)


def _install_transaction_locked(
    ctx: InstallContext,
    source: str,
    ownership: Optional[Tuple[FileSnapshot, FileSnapshot]],
    package_mutex: PackageMutex,
) -> None:
    hook_before, receipt_before = ownership or _preflight_ownership(ctx)
    _require_snapshot_current(ctx.hook_path, hook_before)
    _require_snapshot_current(ctx.receipt_path, receipt_before)
    prior_receipt = _read_receipt(ctx.receipt_path)
    prior_package_state = dict(_snapshot_package_state(ctx))
    prior_package_state["_ownership_required"] = True
    prior_package_state["_package_mutex"] = package_mutex
    token = uuid.uuid4().hex
    hook_stage = ctx.hook_path.with_name(".%s.stage-%s" % (ctx.hook_path.name, token))
    receipt_stage = ctx.receipt_path.with_name(".%s.stage-%s" % (ctx.receipt_path.name, token))
    package_attempted = False
    committed: Dict[Path, FileSnapshot] = {}
    try:
        package_attempted = True
        mutation_evidence = _install_package(ctx, source)
        package_after = _snapshot_package_state(ctx)
        if isinstance(mutation_evidence, dict):
            normalized_evidence: Dict[str, Dict[str, str]] = {}
            for raw_name, item in mutation_evidence.items():
                if not isinstance(raw_name, str) or not isinstance(item, dict):
                    raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "package_mutation_evidence_unavailable")
                name = _normalized_distribution_name(raw_name)
                requirement = item.get("requirement")
                fingerprint = item.get("fingerprint")
                provenance = item.get("provenance")
                if (
                    not name
                    or name in normalized_evidence
                    or not isinstance(requirement, str)
                    or not isinstance(fingerprint, str)
                    or not isinstance(provenance, str)
                ):
                    raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "package_mutation_evidence_unavailable")
                normalized_evidence[name] = {
                    "requirement": requirement,
                    "fingerprint": fingerprint,
                    "provenance": provenance,
                }
            if len(normalized_evidence) != len(mutation_evidence) or any(
                package_after["by_name"].get(name) != item["requirement"]
                or package_after["fingerprints"].get(name) != item["fingerprint"]
                for name, item in normalized_evidence.items()
            ):
                raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "package_mutation_evidence_changed")
            prior_package_state["_transaction_owned"] = _package_state_for_mutations(
                prior_package_state, package_after, set(normalized_evidence)
            )
        elif isinstance(mutation_evidence, (set, frozenset, list, tuple)):
            raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "package_mutation_evidence_unavailable")
        else:
            raise LifecycleError(INSTALL_EXIT_ACQUIRE, "acquire", "package_mutation_evidence_unavailable")
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
    except LifecycleError as exc:
        if package_attempted:
            if exc.reason in {
                "package_mutation_evidence_changed",
                "package_mutation_evidence_unavailable",
            }:
                _restore_package_state(ctx, prior_package_state)
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


def _reconcile_file_transaction(destination: Path) -> None:
    pattern = ".%s.transaction-*.json" % destination.name
    journals = sorted(destination.parent.glob(pattern)) if destination.parent.is_dir() else []
    journal_stage_pattern = ".%s.journal-stage-*" % destination.name
    journal_stages = sorted(destination.parent.glob(journal_stage_pattern)) if destination.parent.is_dir() else []
    commit_pattern = ".%s.committed-*.json" % destination.name
    markers = sorted(destination.parent.glob(commit_pattern)) if destination.parent.is_dir() else []
    marker_stage_pattern = ".%s.committed-*.json.stage" % destination.name
    marker_stages = sorted(destination.parent.glob(marker_stage_pattern)) if destination.parent.is_dir() else []
    if len(journals) > 1 or len(journal_stages) > 1 or len(markers) > 1 or len(marker_stages) > 1:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
    if journal_stages:
        private_stage = journal_stages[0]
        stage_match = re.fullmatch(
            r"\.%s\.journal-stage-([0-9a-f]{32})" % re.escape(destination.name),
            private_stage.name,
        )
        if stage_match is None:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
        token = stage_match.group(1)
        expected_journal = destination.with_name(".%s.transaction-%s.json" % (destination.name, token))
        if journals and journals[0] != expected_journal:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
        try:
            if os.path.lexists(str(expected_journal)):
                _collapse_private_publication(expected_journal, private_stage)
            else:
                content, _identity = _read_independent_file(private_stage)
                _decode_file_transaction(content, destination, token)
                _finish_private_publication(expected_journal, private_stage)
            journals = [expected_journal]
        except Exception:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
    if marker_stages:
        private_marker = marker_stages[0]
        marker_match = re.fullmatch(
            r"\.%s\.committed-([0-9a-f]{32})\.json\.stage" % re.escape(destination.name),
            private_marker.name,
        )
        if marker_match is None:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
        expected_marker = destination.with_name(".%s.committed-%s.json" % (destination.name, marker_match.group(1)))
        if markers and markers[0] != expected_marker:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
        try:
            if os.path.lexists(str(expected_marker)):
                _collapse_private_publication(expected_marker, private_marker)
            else:
                content, _identity = _read_independent_file(private_marker)
                _decode_committed_marker(content, destination)
                _finish_private_publication(expected_marker, private_marker)
            markers = [expected_marker]
        except Exception:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
    if not journals:
        if len(markers) > 1:
            raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
        if markers:
            marker_path = markers[0]
            try:
                _collapse_private_publication(marker_path, marker_path.with_name(marker_path.name + ".stage"))
                marker_snapshot = _snapshot(marker_path)
                content, _identity = _read_independent_file(marker_path)
                record = json.loads(content.decode("utf-8"))
                desired = _snapshot_from_record(record.get("desired"))
                if (
                    record.get("version") != 1
                    or _lexical_path_key(record.get("destination")) != _lexical_path_key(destination)
                    or not _snapshot_is_current(destination, desired)
                ):
                    raise RuntimeError("orphan commit marker does not match public state")
                _remove_owned_file(marker_path, marker_snapshot)
            except Exception:
                raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")
        return
    journal_path = journals[0]
    try:
        token_match = re.fullmatch(
            r"\.%s\.transaction-([0-9a-f]{32})\.json" % re.escape(destination.name),
            journal_path.name,
        )
        if token_match is None:
            raise RuntimeError("invalid transaction journal name")
        journal_stage = destination.with_name(".%s.journal-stage-%s" % (destination.name, token_match.group(1)))
        _collapse_private_publication(journal_path, journal_stage)
        journal_snapshot = _snapshot(journal_path)
        (
            expected,
            desired,
            claim_path,
            recovery_path,
            commit_path,
            stage_path,
            stage_snapshot,
        ) = _read_file_transaction(journal_path, destination)
        claim_exists = os.path.lexists(str(claim_path))
        recovery_exists = os.path.lexists(str(recovery_path))
        if markers and markers[0] != commit_path:
            raise RuntimeError("transaction commit marker token mismatch")
        commit_exists = os.path.lexists(str(commit_path))
        if claim_exists and not _snapshot_is_current(claim_path, expected):
            raise RuntimeError("transaction claim identity mismatch")
        if recovery_exists and not _snapshot_is_current(recovery_path, desired):
            raise RuntimeError("transaction recovery identity mismatch")
        if stage_path is not None and os.path.lexists(str(stage_path)):
            if stage_snapshot is None or not _owned_snapshot_matches(stage_path, stage_snapshot):
                raise RuntimeError("transaction stage identity mismatch")

        if commit_exists:
            _collapse_private_publication(commit_path, commit_path.with_name(commit_path.name + ".stage"))
            commit_snapshot = _read_committed_marker(commit_path, destination, desired)
            if desired.existed:
                if not _owned_snapshot_matches(destination, desired):
                    raise RuntimeError("committed public identity mismatch")
                if stage_path is not None and os.path.lexists(str(stage_path)):
                    _durable_unlink(stage_path)
                _require_snapshot_current(destination, desired)
            elif os.path.lexists(str(destination)):
                raise RuntimeError("committed absence has a public owner")
            if claim_exists:
                _durable_unlink(claim_path)
            if recovery_exists:
                _durable_unlink(recovery_path)
            _remove_owned_file(journal_path, journal_snapshot)
            _remove_owned_file(commit_path, commit_snapshot)
            return

        if _snapshot_is_current(destination, expected) and not claim_exists and not recovery_exists:
            if stage_path is not None and os.path.lexists(str(stage_path)):
                _durable_unlink(stage_path)
            _remove_owned_file(journal_path, journal_snapshot)
            return
        if not os.path.lexists(str(destination)):
            if expected.existed:
                if not claim_exists:
                    raise RuntimeError("transaction claim missing")
                _restore_claim_without_clobber(claim_path, destination)
            elif claim_exists:
                raise RuntimeError("unexpected transaction claim")
            if recovery_exists:
                _durable_unlink(recovery_path)
            if stage_path is not None and os.path.lexists(str(stage_path)):
                _durable_unlink(stage_path)
            _require_snapshot_current(destination, expected)
            _remove_owned_file(journal_path, journal_snapshot)
            return
        if _owned_snapshot_matches(destination, desired):
            if expected.existed and not claim_exists:
                # The owned commit was already finalized before only the
                # journal cleanup was interrupted.
                _remove_owned_file(journal_path, journal_snapshot)
                return
            os.replace(str(destination), str(recovery_path))
            _fsync_directory(destination.parent)
            if not _owned_snapshot_matches(recovery_path, desired):
                raise RuntimeError("transaction recovery identity mismatch")
            if expected.existed:
                _restore_claim_without_clobber(claim_path, destination)
            _durable_unlink(recovery_path)
            if stage_path is not None and os.path.lexists(str(stage_path)):
                _durable_unlink(stage_path)
            _require_snapshot_current(destination, expected)
            _remove_owned_file(journal_path, journal_snapshot)
            return
        raise RuntimeError("public path has a concurrent owner")
    except Exception:
        raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_conflict")


def _require_no_pending_transaction(destination: Path) -> None:
    if not destination.parent.is_dir():
        return
    prefixes = (
        ".%s.transaction-" % destination.name,
        ".%s.committed-" % destination.name,
        ".%s.journal-stage-" % destination.name,
        ".%s.claim-" % destination.name,
        ".%s.recovery-" % destination.name,
        ".%s.restore-stage-" % destination.name,
    )
    if any(any(path.name.startswith(prefix) for prefix in prefixes) for path in destination.parent.iterdir()):
        raise LifecycleError(INSTALL_EXIT_INSTALL, "recovery", "transaction_recovery_required")


def _preflight_ownership(ctx: InstallContext, recover: bool = True) -> Tuple[FileSnapshot, FileSnapshot]:
    if recover:
        _reconcile_file_transaction(ctx.hook_path)
        _reconcile_file_transaction(ctx.receipt_path)
    else:
        _require_no_pending_transaction(ctx.hook_path)
        _require_no_pending_transaction(ctx.receipt_path)
    return _snapshot(ctx.hook_path), _snapshot(ctx.receipt_path)


def _run_install(ctx: InstallContext, args: argparse.Namespace) -> Tuple[int, Dict[str, Any]]:
    ownership = _preflight_ownership(ctx, recover=not args.dry_run)
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
    if args.dry_run or not args.yes:
        return _run_uninstall_locked(ctx, args, None)
    with _package_ownership(ctx) as package_mutex:
        return _run_uninstall_locked(ctx, args, package_mutex)


def _run_uninstall_locked(
    ctx: InstallContext, args: argparse.Namespace, package_mutex: Optional[PackageMutex]
) -> Tuple[int, Dict[str, Any]]:
    if not os.path.lexists(str(ctx.receipt_path)):
        ownership = _preflight_ownership(ctx, recover=not args.dry_run)
        if os.path.lexists(str(ctx.hook_path)):
            return INSTALL_EXIT_PREFLIGHT, _failure_report(ctx, "uninstall", "receipt", "receipt_missing")
        report = _base_report(ctx, "ok", "uninstall")
        report["steps"] = [{"id": "uninstall", "status": "skipped"}]
        return INSTALL_EXIT_OK, report
    receipt = _read_receipt(ctx.receipt_path, required=True)
    assert receipt is not None
    ownership = _preflight_ownership(ctx, recover=not args.dry_run)
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
    prior_package_state = dict(_snapshot_package_state(ctx))
    prior_package_state["_ownership_required"] = True
    if package_mutex is None:
        raise LifecycleError(INSTALL_EXIT_PREFLIGHT, "preflight", "package_ownership_unavailable")
    prior_package_state["_package_mutex"] = package_mutex
    hook_before, receipt_before = ownership
    _require_snapshot_current(ctx.hook_path, hook_before)
    _require_snapshot_current(ctx.receipt_path, receipt_before)
    package_attempted = False
    committed: Dict[Path, FileSnapshot] = {}
    try:
        package_attempted = True
        _uninstall_package(ctx)
        prior_package_state["_transaction_owned"] = _package_state_for_mutations(
            prior_package_state, _snapshot_package_state(ctx), {"dcc-mcp-3dsmax"}
        )
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
