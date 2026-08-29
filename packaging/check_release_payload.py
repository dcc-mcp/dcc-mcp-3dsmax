#!/usr/bin/env python3
"""Smoke-check an assembled dcc-mcp-3dsmax release payload."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import stat
import sys
import tempfile
import unicodedata
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple

ASSET_SOURCE_ACTION = "3dsmax_asset_source__search_assets"
PACKAGE_NAME = "dcc-mcp-3dsmax"
TARGET_PLATFORM = "win64"
MIN_CORE_VERSION = "0.20.22"
MAX_CORE_VERSION = "1.0.0"
MIN_SERVER_VERSION = "0.20.22"
MAX_SERVER_VERSION = "1.0.0"
RUNTIME_MANIFEST_NAME = "dcc-mcp-runtime-manifest.json"
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}


def _portable_member_parts(name: str, source: str) -> Tuple[str, ...]:
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or name.startswith("//")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise RuntimeError("{} contains an unsafe archive path: {!r}".format(source, name))
    raw = name[:-1] if name.endswith("/") else name
    parts = raw.split("/")
    if not raw or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError("{} contains an unsafe archive path: {!r}".format(source, name))
    for part in parts:
        normalized = unicodedata.normalize("NFKC", part).casefold()
        if part.endswith((" ", ".")) or ":" in part or normalized.split(".", 1)[0] in _WINDOWS_RESERVED_NAMES:
            raise RuntimeError("{} contains an unsafe archive path: {!r}".format(source, name))
    return tuple(parts)


def _portable_member_key(parts: Tuple[str, ...]) -> str:
    return "/".join(unicodedata.normalize("NFKC", part).casefold() for part in parts)


def _validated_archive_members(zf: zipfile.ZipFile, source: str):
    members = {}
    portable_keys = set()
    for info in zf.infolist():
        parts = _portable_member_parts(info.filename, source)
        path = PurePosixPath(*parts).as_posix()
        portable_key = _portable_member_key(parts)
        if path in members or portable_key in portable_keys:
            raise RuntimeError("{} contains a duplicate archive member: {}".format(source, info.filename))
        if info.flag_bits & 0x1:
            raise RuntimeError("{} contains an encrypted archive member: {}".format(source, info.filename))
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        allowed = {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG}
        if kind not in allowed:
            raise RuntimeError("{} contains a link or special archive member: {}".format(source, info.filename))
        members[path] = info
        portable_keys.add(portable_key)
    return members


def reject_adapter_runtime_backfills(payload: Path) -> None:
    """Fail if either runtime root contains an adapter-local typing_extensions copy."""
    _reject_adapter_runtime_backfills_paths(path.relative_to(payload).as_posix() for path in payload.rglob("*"))


def _reject_adapter_runtime_backfills_paths(paths) -> None:
    for relative in paths:
        for part in str(relative).replace("\\", "/").split("/"):
            normalized = re.sub(r"[-_.]+", "_", unicodedata.normalize("NFKC", part).casefold())
            if normalized == "typing_extensions" or normalized.startswith("typing_extensions_"):
                raise RuntimeError(
                    "release payload contains forbidden adapter-local typing_extensions payload: {}".format(relative)
                )


def _mzp_run_text(version: str) -> str:
    return (
        'name "dcc-mcp-3dsmax"\n'
        f'description "dcc-mcp-3dsmax {version} drag-and-drop installer"\n'
        "version 1\n"
        'run "install.ms"\n'
        'drop "install.ms"\n'
        "clear temp on MAX exit\n"
    )


def _trusted_control_files(version: str):
    packaging_root = Path(__file__).resolve().parent
    template_root = packaging_root / "templates"
    try:
        install = (template_root / "install.ms").read_text(encoding="utf-8")
        install = install.replace("{{VERSION}}", version).encode("utf-8")
        return {
            "install.ms": install,
            "mzp.run": _mzp_run_text(version).encode("utf-8"),
            "payload/README.txt": (packaging_root / "README.txt").read_bytes(),
            "payload/startup/dcc_mcp_3dsmax_startup.ms": (template_root / "dcc_mcp_3dsmax_startup.ms").read_bytes(),
        }
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("MZP trusted control templates are unavailable") from exc


def _verify_control_members(zf: zipfile.ZipFile, members, source: str, version: str) -> None:
    expected = _trusted_control_files(version)
    runtime_prefixes = ("payload/python/", "payload/python37/")
    observed = {path for path, info in members.items() if not info.is_dir() and not path.startswith(runtime_prefixes)}
    if observed != set(expected):
        raise RuntimeError("release MZP control-plane member allowlist mismatch: {}".format(source))
    for path, expected_bytes in expected.items():
        info = members.get(path)
        if info is None or info.is_dir():
            raise RuntimeError("release MZP control-plane member is missing: {}".format(path))
        if zf.read(info) != expected_bytes:
            raise RuntimeError("release MZP control-plane member bytes changed: {}".format(path))


def _verify_archive_runtime_manifests(zf: zipfile.ZipFile, members, source: str) -> None:
    for lane in ("python", "python37"):
        prefix = "payload/{}/".format(lane)
        manifest_path = prefix + RUNTIME_MANIFEST_NAME
        manifest_info = members.get(manifest_path)
        if manifest_info is None or manifest_info.is_dir():
            raise RuntimeError("release payload {} manifest is missing".format(lane))
        try:
            document = json.loads(zf.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("release payload {} manifest is missing or invalid".format(lane)) from exc
        expected = _manifest_entries(document, lane)
        observed = {}
        for member_path, info in members.items():
            if info.is_dir() or not member_path.startswith(prefix) or member_path == manifest_path:
                continue
            relative = member_path[len(prefix) :]
            data = zf.read(info)
            observed[relative] = (hashlib.sha256(data).hexdigest(), len(data))
        if observed != expected:
            raise RuntimeError("release payload {} manifest integrity check failed".format(lane))


def verify_control_plane_directory(payload: Path, expected_version: str) -> None:
    root = payload.parent
    expected = _trusted_control_files(expected_version)
    expected_paths = set(expected)
    observed = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("release MZP control-plane member cannot be a link")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(("payload/python/", "payload/python37/")):
            continue
        observed.add(relative)
    if observed != expected_paths:
        raise RuntimeError("release MZP control-plane member allowlist mismatch")
    for relative, expected_bytes in expected.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or path.is_symlink() or path.read_bytes() != expected_bytes:
            raise RuntimeError("release MZP control-plane member bytes changed: {}".format(relative))


def payload_root(
    path: Path, expected_version: Optional[str] = None
) -> Tuple[Path, Optional[tempfile.TemporaryDirectory]]:
    path = path.resolve()
    if path.is_dir():
        return (path / "payload" if (path / "payload").is_dir() else path), None
    if not zipfile.is_zipfile(str(path)):
        raise RuntimeError("release payload is neither a directory nor a zip-compatible MZP: {}".format(path))

    tmp = tempfile.TemporaryDirectory()
    try:
        with zipfile.ZipFile(str(path)) as zf:
            members = _validated_archive_members(zf, path.name)
            if expected_version is None:
                match = re.fullmatch(
                    r"{}-([0-9]+\.[0-9]+\.[0-9]+)-{}\.mzp".format(PACKAGE_NAME, TARGET_PLATFORM), path.name
                )
                if not match:
                    raise RuntimeError("release payload version is unavailable: {}".format(path.name))
                expected_version = match.group(1)
            _reject_adapter_runtime_backfills_paths(members)
            _verify_control_members(zf, members, path.name, expected_version)
            _verify_archive_runtime_manifests(zf, members, path.name)
            destination = Path(tmp.name).resolve()
            for member_path, info in members.items():
                out = destination.joinpath(*PurePosixPath(member_path).parts)
                try:
                    out.resolve(strict=False).relative_to(destination)
                except ValueError as exc:
                    raise RuntimeError("release archive path escaped the extraction root") from exc
                if info.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, out.open("wb") as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
    except Exception:
        tmp.cleanup()
        raise
    return Path(tmp.name) / "payload", tmp


def _manifest_entries(document, lane: str):
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "root", "files"}
        or document.get("schema_version") != 1
        or document.get("root") != lane
        or not isinstance(document.get("files"), list)
    ):
        raise RuntimeError("release payload {} manifest is invalid".format(lane))
    entries = {}
    portable_keys = set()
    for item in document["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise RuntimeError("release payload {} manifest is invalid".format(lane))
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        parts = _portable_member_parts(path, "release payload {} manifest".format(lane))
        normalized_path = PurePosixPath(*parts).as_posix()
        portable_key = _portable_member_key(parts)
        if (
            normalized_path == RUNTIME_MANIFEST_NAME
            or normalized_path in entries
            or portable_key in portable_keys
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise RuntimeError("release payload {} manifest is invalid".format(lane))
        entries[normalized_path] = (digest, size)
        portable_keys.add(portable_key)
    return entries


def verify_runtime_manifests(payload: Path) -> None:
    for lane in ("python", "python37"):
        root = payload / lane
        manifest_path = root / RUNTIME_MANIFEST_NAME
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("release payload {} manifest is missing or invalid".format(lane)) from exc
        expected = _manifest_entries(document, lane)
        observed = {}
        portable_keys = set()
        for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
            if path == manifest_path:
                continue
            if path.is_symlink():
                raise RuntimeError("release payload {} manifest cannot bind a link".format(lane))
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            parts = _portable_member_parts(relative, "release payload {}".format(lane))
            portable_key = _portable_member_key(parts)
            if portable_key in portable_keys:
                raise RuntimeError("release payload {} contains a duplicate portable path".format(lane))
            portable_keys.add(portable_key)
            data = path.read_bytes()
            observed[relative] = (hashlib.sha256(data).hexdigest(), len(data))
        if observed != expected:
            raise RuntimeError("release payload {} manifest integrity check failed".format(lane))


def python_root(payload: Path) -> Path:
    name = "python37" if sys.version_info < (3, 8) else "python"
    root = payload / name
    if not root.is_dir():
        raise RuntimeError("payload is missing {} for Python {}.{}".format(name, *sys.version_info[:2]))
    return root


def _declared_adapter_version(version_file: Path) -> str:
    try:
        tree = ast.parse(version_file.read_text(encoding="utf-8"), filename=str(version_file))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise RuntimeError("release version mismatch: adapter version declaration is invalid") from exc
    values = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "__version__":
            continue
        value = node.value
        if isinstance(value, ast.Str):
            values.append(value.s)
        elif isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.append(value.value)
    if len(values) != 1:
        raise RuntimeError("release version mismatch: adapter version declaration is ambiguous")
    return values[0]


def verify_release_versions(payload: Path, source: Path, expected_version: str) -> None:
    """Bind the archive name, installer, and both runtime roots to one release version."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", expected_version):
        raise RuntimeError("release version mismatch: expected version is not strict semantic version")
    source = Path(source)
    expected_name = "{}-{}-{}.mzp".format(PACKAGE_NAME, expected_version, TARGET_PLATFORM)
    if source.is_file() and source.name != expected_name:
        raise RuntimeError("release version mismatch: archive name does not match expected version")
    if source.is_dir() and source.name != expected_name[:-4]:
        raise RuntimeError("release version mismatch: archive root does not match expected version")

    installer = payload.parent / "install.ms"
    try:
        install_text = installer.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("release version mismatch: installer version is unavailable") from exc
    install_versions = re.findall(r"\bversion_name\s*=\s*['\"]([^'\"]+)['\"]", install_text)
    if install_versions != [expected_version]:
        raise RuntimeError("release version mismatch: installer version does not match expected version")

    observed = {}
    for lane in ("python", "python37"):
        version_file = payload / lane / "dcc_mcp_3dsmax" / "__version__.py"
        observed[lane] = _declared_adapter_version(version_file)
    if set(observed.values()) != {expected_version}:
        raise RuntimeError("release version mismatch: adapter runtime roots do not match expected version")


def _strict_runtime_version(value: object, distribution: str) -> Tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value):
        raise RuntimeError("invalid {} version in release payload: {!r}".format(distribution, value))
    return tuple(int(part) for part in value.split("."))


def _normalized_distribution_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[-_.]+", "-", value).lower()


def _distribution_metadata_version(root: Path, distribution: str) -> str:
    expected = _normalized_distribution_name(distribution)
    matches = []
    for metadata_path in sorted(Path(root).glob("*.dist-info/METADATA")):
        try:
            metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("ambiguous {} metadata in release payload".format(distribution)) from exc
        names = metadata.get_all("Name", [])
        matching_names = [name for name in names if _normalized_distribution_name(name) == expected]
        if not matching_names:
            continue
        versions = metadata.get_all("Version", [])
        if len(names) != 1 or len(matching_names) != 1 or len(versions) != 1:
            raise RuntimeError("ambiguous {} metadata in release payload".format(distribution))
        matches.append(versions[0])
    if len(matches) != 1:
        raise RuntimeError("ambiguous {} metadata in release payload".format(distribution))
    return matches[0]


def verify_runtime_dependency_versions(root: Path) -> None:
    """Execute the same strict dependency boundary as the MZP install/startup templates."""
    core = _strict_runtime_version(_distribution_metadata_version(root, "dcc-mcp-core"), "dcc-mcp-core")
    server = _strict_runtime_version(_distribution_metadata_version(root, "dcc-mcp-server"), "dcc-mcp-server")
    if not (
        _strict_runtime_version(MIN_CORE_VERSION, "Core minimum")
        <= core
        < _strict_runtime_version(MAX_CORE_VERSION, "Core maximum")
    ):
        raise RuntimeError("unsupported dcc-mcp-core version in release payload")
    if not (
        _strict_runtime_version(MIN_SERVER_VERSION, "Server minimum")
        <= server
        < _strict_runtime_version(MAX_SERVER_VERSION, "Server maximum")
    ):
        raise RuntimeError("unsupported dcc-mcp-server version in release payload")


def verify_asset_source_handler(server) -> None:
    loaded = server._server.load_skill("3dsmax-asset-source")
    registry = server._server.registry
    if (
        loaded != [ASSET_SOURCE_ACTION]
        or registry.get_action(ASSET_SOURCE_ACTION) is None
        or not server._server.has_handler(ASSET_SOURCE_ACTION)
    ):
        raise RuntimeError("release payload asset-source handler registration failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", help="Path to an assembled .mzp/.zip or extracted payload directory")
    parser.add_argument("--expected-version", required=True, help="Exact X.Y.Z release version")
    args = parser.parse_args()

    source = Path(args.payload).resolve()
    payload, cleanup = payload_root(source, args.expected_version)
    try:
        reject_adapter_runtime_backfills(payload)
        verify_control_plane_directory(payload, args.expected_version)
        verify_runtime_manifests(payload)
        verify_release_versions(payload, source, args.expected_version)
        root = python_root(payload)
        if sys.version_info >= (3, 8) and root.name.lower() == "python37":
            raise RuntimeError("Python 3.8+ must not import from payload/python37")
        sys.path.insert(0, str(root))

        verify_runtime_dependency_versions(root)

        import dcc_mcp_core._core  # noqa: F401, PLC0415

        import dcc_mcp_3dsmax  # noqa: PLC0415
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions  # noqa: PLC0415

        server = MaxMcpServer(options=MaxServerOptions(port=0, enable_gateway_failover=False, job_storage_path=""))
        try:
            server.register_builtin_actions(include_bundled=True)
            verify_asset_source_handler(server)
        finally:
            server.stop()
        print("release payload import-registration OK: root={} adapter={}".format(root, dcc_mcp_3dsmax.__version__))
    finally:
        if cleanup is not None:
            try:
                cleanup.cleanup()
            except OSError:
                # Windows keeps imported .pyd files locked until process exit.
                pass


if __name__ == "__main__":
    main()
