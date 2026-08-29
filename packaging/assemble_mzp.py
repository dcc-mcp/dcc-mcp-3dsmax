#!/usr/bin/env python3
"""Assemble a drag-and-drop 3ds Max MZP installer for dcc-mcp-3dsmax.

The output is a ZIP-compatible ``.mzp`` archive with an MZP control file
(``mzp.run``) at the archive root. The control file runs ``install.ms`` when
users run the package or drag it into the viewport.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import urllib.request
import uuid
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import List, Tuple

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.7-3.10 build lanes
    import tomli as tomllib
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

PACKAGE_NAME = "dcc-mcp-3dsmax"
PY_PACKAGE_NAME = "dcc_mcp_3dsmax"
CORE_PACKAGE_NAME = "dcc_mcp_core"
SERVER_PACKAGE_NAME = "dcc_mcp_server"
TARGET_PLATFORM = "win64"
_RELEASE_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


def _validate_release_version(version: str) -> str:
    """Validate a release version before it reaches paths or control files."""
    if not isinstance(version, str) or _RELEASE_VERSION_RE.fullmatch(version) is None:
        raise ValueError("version must match X.Y.Z")
    return version
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        raise RuntimeError(f"{source} contains an unsafe archive path: {name!r}")
    raw = name[:-1] if name.endswith("/") else name
    parts = raw.split("/")
    if not raw or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"{source} contains an unsafe archive path: {name!r}")
    for part in parts:
        normalized = unicodedata.normalize("NFKC", part).casefold()
        if part.endswith((" ", ".")) or ":" in part or normalized.split(".", 1)[0] in _WINDOWS_RESERVED_NAMES:
            raise RuntimeError(f"{source} contains an unsafe archive path: {name!r}")
    return tuple(parts)


def _portable_member_key(parts: Tuple[str, ...]) -> str:
    return "/".join(unicodedata.normalize("NFKC", part).casefold() for part in parts)


def _validated_zip_members(zf: zipfile.ZipFile, source: str):
    members = {}
    portable_keys = set()
    for info in zf.infolist():
        parts = _portable_member_parts(info.filename, source)
        path = PurePosixPath(*parts).as_posix()
        portable_key = _portable_member_key(parts)
        if path in members or portable_key in portable_keys:
            raise RuntimeError(f"{source} contains a duplicate archive member: {info.filename}")
        if info.flag_bits & 0x1:
            raise RuntimeError(f"{source} contains an encrypted archive member: {info.filename}")
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        allowed = {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG}
        if kind not in allowed:
            raise RuntimeError(f"{source} contains a link or special archive member: {info.filename}")
        members[path] = info
        portable_keys.add(portable_key)
    return members


def _expected_dist_info_dir(distribution: str, version: str) -> str:
    normalized_name = re.sub(r"[-_.]+", "_", canonicalize_name(distribution))
    normalized_version = str(Version(version)).replace("-", "_")
    return f"{normalized_name}-{normalized_version}.dist-info"


def _wheel_metadata(zf: zipfile.ZipFile, distribution: str, version: str, members):
    dist_info = _expected_dist_info_dir(distribution, version)
    metadata_path = f"{dist_info}/METADATA"
    metadata_paths = [name for name in members if name.endswith(".dist-info/METADATA")]
    if metadata_paths != [metadata_path]:
        raise RuntimeError(f"{distribution} wheel metadata identity/version is not canonical")
    try:
        parsed = Parser().parsestr(zf.read(members[metadata_path]).decode("utf-8"))
    except (KeyError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{distribution} wheel metadata is invalid") from exc
    return parsed


def _verify_wheel_record(zf: zipfile.ZipFile, distribution: str, version: str, members) -> None:
    dist_info = _expected_dist_info_dir(distribution, version)
    record_path = f"{dist_info}/RECORD"
    record_info = members.get(record_path)
    if record_info is None or record_info.is_dir():
        raise RuntimeError(f"{distribution} wheel RECORD is missing")
    try:
        rows = list(csv.reader(io.StringIO(zf.read(record_info).decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"{distribution} wheel RECORD is invalid") from exc
    records = {}
    for row in rows:
        if len(row) != 3:
            raise RuntimeError(f"{distribution} wheel RECORD is invalid")
        parts = _portable_member_parts(row[0], f"{distribution} wheel RECORD")
        path = PurePosixPath(*parts).as_posix()
        if path in records:
            raise RuntimeError(f"{distribution} wheel RECORD contains a duplicate path")
        records[path] = (row[1], row[2])
    file_members = {name: info for name, info in members.items() if not info.is_dir()}
    if set(records) != set(file_members):
        raise RuntimeError(f"{distribution} wheel RECORD coverage is incomplete")
    for path, info in file_members.items():
        digest, size = records[path]
        if path == record_path:
            if digest or size:
                raise RuntimeError(f"{distribution} wheel RECORD self-entry is invalid")
            continue
        if not digest.startswith("sha256=") or not size.isdigit():
            raise RuntimeError(f"{distribution} wheel RECORD hash or size is invalid")
        data = zf.read(info)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        if digest != "sha256=" + encoded or int(size) != len(data):
            raise RuntimeError(f"{distribution} wheel RECORD integrity check failed for {path}")


def _runtime_requirements(metadata, python_full_version: str) -> List[Requirement]:
    environment = {
        "extra": "",
        "implementation_name": "cpython",
        "implementation_version": python_full_version,
        "os_name": "nt",
        "platform_machine": "AMD64",
        "platform_python_implementation": "CPython",
        "platform_release": "10",
        "platform_system": "Windows",
        "platform_version": "10",
        "python_full_version": python_full_version,
        "python_version": ".".join(python_full_version.split(".")[:2]),
        "sys_platform": "win32",
    }
    requirements = []
    for raw_requirement in metadata.get_all("Requires-Dist", []):
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement as exc:
            raise RuntimeError(f"wheel has an invalid runtime requirement: {raw_requirement!r}") from exc
        if requirement.marker is not None and not requirement.marker.evaluate(environment=environment):
            continue
        requirements.append(requirement)
    return requirements


def _normalized_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def assert_no_typing_extensions(paths, source: str) -> None:
    """Reject adapter-local typing_extensions payload aliases."""
    for path in paths:
        for part in str(path).replace("\\", "/").split("/"):
            normalized = re.sub(r"[-_.]+", "_", unicodedata.normalize("NFKC", part).casefold())
            if normalized == "typing_extensions" or normalized.startswith("typing_extensions_"):
                raise RuntimeError(f"{source} contains forbidden adapter-local typing_extensions payload: {path}")


def _release_version(value: str, source: str) -> Tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value.strip()):
        raise RuntimeError(f"{source} has missing or invalid version: {value!r}")
    return tuple(int(part) for part in value.strip().split("."))


def _satisfies_release_specifier(version: str, specifier) -> bool:
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        raise RuntimeError(f"selected dcc-mcp-server wheel has invalid PEP 440 version: {version!r}") from exc
    return specifier.contains(parsed, prereleases=True)


def _server_requirement_specifier(requirement: Requirement):
    if _normalized_distribution(requirement.name) != "dcc-mcp-server":
        return None
    if requirement.url or requirement.extras or not str(requirement.specifier):
        raise RuntimeError(f"dcc-mcp-core has a missing or invalid dcc-mcp-server specifier: {requirement!r}")
    return requirement.specifier


def _evaluated_requirement_key(requirement: Requirement):
    return (
        _normalized_distribution(requirement.name),
        tuple(sorted(requirement.extras)),
        str(requirement.specifier),
        requirement.url or "",
    )


def _verified_wheel_metadata(wheel: Path, distribution: str, version: str):
    with zipfile.ZipFile(str(wheel)) as zf:
        members = _validated_zip_members(zf, wheel.name)
        assert_no_typing_extensions(members, wheel.name)
        metadata = _wheel_metadata(zf, distribution, version, members)
        _verify_wheel_record(zf, distribution, version, members)
    if _normalized_distribution(metadata.get("Name", "")) != distribution:
        raise RuntimeError(f"selected {distribution} wheel has an ambiguous distribution identity")
    if metadata.get("Version", "") != version:
        raise RuntimeError(
            f"selected {distribution} wheel version {metadata.get('Version', '')!r} does not match {version}"
        )
    return metadata


def verify_python37_runtime_contract(
    core_wheels: List[Path],
    server_wheel: Path,
    *,
    expected_core_version: str,
    expected_server_version: str,
) -> None:
    """Prove the selected Core/server payload is closed without an adapter backfill."""
    cp37_wheels = [wheel for wheel in core_wheels if "cp37-cp37m" in wheel.name]
    abi3_wheels = [wheel for wheel in core_wheels if "cp38-abi3" in wheel.name]
    if len(cp37_wheels) != 1:
        raise RuntimeError(f"expected one dcc-mcp-core cp37 wheel, found {[wheel.name for wheel in cp37_wheels]}")
    if len(abi3_wheels) != 1 or len(core_wheels) != 2:
        raise RuntimeError(f"expected one dcc-mcp-core cp38-abi3 wheel, found {[wheel.name for wheel in abi3_wheels]}")
    core_metadata = _verified_wheel_metadata(cp37_wheels[0], "dcc-mcp-core", expected_core_version)
    abi3_metadata = _verified_wheel_metadata(abi3_wheels[0], "dcc-mcp-core", expected_core_version)
    server_metadata = _verified_wheel_metadata(server_wheel, "dcc-mcp-server", expected_server_version)

    runtime_requirements = _runtime_requirements(core_metadata, "3.7.9")
    modern_requirements = _runtime_requirements(abi3_metadata, "3.12.0")
    server_contracts = []
    for requirement in runtime_requirements:
        specifier = _server_requirement_specifier(requirement)
        if specifier is not None:
            server_contracts.append((requirement, specifier))
    server_requirements = [requirement for requirement, _specifier in server_contracts]
    if len(server_requirements) != len(runtime_requirements):
        raise RuntimeError(
            f"unclosed dcc-mcp-core Python 3.7 runtime dependencies: "
            f"{sorted(str(requirement) for requirement in runtime_requirements if requirement not in server_requirements)}"
        )
    if len(server_requirements) != 1:
        raise RuntimeError(f"expected one dcc-mcp-server runtime requirement, found {server_requirements}")
    if sorted(_evaluated_requirement_key(requirement) for requirement in modern_requirements) != sorted(
        _evaluated_requirement_key(requirement) for requirement in runtime_requirements
    ):
        raise RuntimeError("Core wheel runtime dependency drift between cp37 and cp38-abi3 lanes")
    server_runtime_requirements = sorted(
        {
            str(requirement)
            for requirement in (
                _runtime_requirements(server_metadata, "3.7.9") + _runtime_requirements(server_metadata, "3.12.0")
            )
        }
    )
    if server_runtime_requirements:
        raise RuntimeError(f"unclosed dcc-mcp-server runtime dependencies: {server_runtime_requirements}")
    server_specifier = server_contracts[0][1]
    if not _satisfies_release_specifier(expected_server_version, server_specifier):
        raise RuntimeError(
            f"server wheel version {expected_server_version} does not satisfy dcc-mcp-core requirement {server_specifier}"
        )


def resolve_core_version(project_root: Path) -> str:
    """Resolve the latest PyPI dcc-mcp-core version satisfying pyproject."""
    return resolve_dependency_version(project_root, "dcc-mcp-core")


def resolve_server_version(project_root: Path) -> str:
    """Resolve the latest PyPI dcc-mcp-server version satisfying pyproject."""
    return resolve_dependency_version(project_root, "dcc-mcp-server")


def _declared_dependency(project_root: Path, distribution: str) -> Requirement:
    with (project_root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream).get("project", {})
    matches = []
    for raw_requirement in project.get("dependencies", []):
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement as exc:
            raise RuntimeError(f"pyproject has an invalid dependency requirement: {raw_requirement!r}") from exc
        if _normalized_distribution(requirement.name) == _normalized_distribution(distribution):
            matches.append(requirement)
    if len(matches) != 1 or not str(matches[0].specifier) or matches[0].url or matches[0].extras or matches[0].marker:
        raise RuntimeError(f"pyproject must declare exactly one closed version range for {distribution}")
    return matches[0]


def resolve_dependency_minimum(project_root: Path, distribution: str) -> str:
    requirement = _declared_dependency(project_root, distribution)
    minimums = [item.version for item in requirement.specifier if item.operator == ">="]
    if len(minimums) != 1:
        raise RuntimeError(f"Cannot find one inclusive {distribution} minimum version in pyproject.toml")
    return minimums[0]


def resolve_dependency_version(project_root: Path, distribution: str) -> str:
    """Resolve the highest published wheel version inside the complete declared range."""
    requirement = _declared_dependency(project_root, distribution)

    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{distribution}/json", timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not query published {distribution} releases") from exc

    candidates = []
    releases = data.get("releases", {})
    if not isinstance(releases, dict):
        raise RuntimeError(f"PyPI returned an invalid release index for {distribution}")
    for raw_version, files in releases.items():
        if not _has_required_non_yanked_wheels(distribution, raw_version, files):
            continue
        try:
            version = Version(raw_version)
        except InvalidVersion:
            continue
        if requirement.specifier.contains(version, prereleases=None):
            candidates.append(version)
    if not candidates:
        raise RuntimeError(f"no published version of {distribution} satisfies declared range {requirement.specifier}")
    selected = str(max(candidates))
    print(f"Resolved {distribution} {selected} from PyPI range {requirement.specifier}")
    return selected


def _version_gte(version: str, minimum: str) -> bool:
    return [int(part) for part in version.split(".")] >= [int(part) for part in minimum.split(".")]


def _core_wheel_patterns() -> List[Tuple[str, str]]:
    return [
        ("cp37-cp37m-win_amd64", "Python 3.7 / 3ds Max 2022"),
        ("cp38-abi3-win_amd64", "Python 3.8+ / modern 3ds Max"),
    ]


def _parsed_wheel(filename: str):
    try:
        return parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return None


def _wheel_matches_distribution_version(filename: str, distribution: str, version: str) -> bool:
    parsed = _parsed_wheel(filename)
    if parsed is None:
        return False
    name, wheel_version, _build, _tags = parsed
    try:
        expected_version = Version(version)
    except InvalidVersion:
        return False
    return name == canonicalize_name(distribution) and wheel_version == expected_version


def _wheel_has_exact_tag(filename: str, interpreter: str, abi: str, platform: str) -> bool:
    parsed = _parsed_wheel(filename)
    if parsed is None:
        return False
    return any(tag.interpreter == interpreter and tag.abi == abi and tag.platform == platform for tag in parsed[3])


def _non_yanked_wheel_filenames(distribution: str, version: str, files) -> List[str]:
    if not isinstance(files, list):
        return []
    return [
        item["filename"]
        for item in files
        if isinstance(item, dict)
        and item.get("packagetype") == "bdist_wheel"
        and not item.get("yanked", False)
        and isinstance(item.get("filename"), str)
        and item["filename"]
        and _wheel_matches_distribution_version(item["filename"], distribution, version)
    ]


def _has_required_non_yanked_wheels(distribution: str, version: str, files) -> bool:
    filenames = _non_yanked_wheel_filenames(distribution, version, files)
    normalized = _normalized_distribution(distribution)
    if normalized == "dcc-mcp-core":
        return all(
            sum(_wheel_has_exact_tag(filename, *tag) for filename in filenames) == 1 for tag in _core_wheel_tags()
        )
    if normalized == "dcc-mcp-server":
        return sum(_wheel_has_exact_tag(filename, "py3", "none", "win_amd64") for filename in filenames) == 1
    return bool(filenames)


def _non_yanked_wheel_map(distribution: str, version: str, files):
    if not isinstance(files, list):
        return {}
    return {
        item["filename"]: (item["url"], item["digests"]["sha256"])
        for item in files
        if isinstance(item, dict)
        and item.get("packagetype") == "bdist_wheel"
        and not item.get("yanked", False)
        and isinstance(item.get("filename"), str)
        and item["filename"]
        and isinstance(item.get("url"), str)
        and item["url"]
        and isinstance(item.get("digests"), dict)
        and isinstance(item["digests"].get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", item["digests"]["sha256"])
        and _wheel_matches_distribution_version(item["filename"], distribution, version)
    }


def _core_wheel_tags() -> List[Tuple[str, str, str]]:
    return [
        ("cp37", "cp37m", "win_amd64"),
        ("cp38", "abi3", "win_amd64"),
    ]


def _download_verified_wheel(url: str, expected_sha256: str, target: Path) -> None:
    if not target.exists():
        urllib.request.urlretrieve(url, str(target))
    try:
        observed = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError(f"downloaded wheel integrity could not be read: {target.name}") from exc
    if observed != expected_sha256:
        raise RuntimeError(f"downloaded wheel sha256 integrity mismatch: {target.name}")


def download_core_wheels(version: str, dest: Path) -> List[Path]:
    """Download Windows dcc-mcp-core wheels needed by the offline installer."""
    pypi_url = f"https://pypi.org/pypi/dcc-mcp-core/{version}/json"
    print(f"Querying {pypi_url}")
    with urllib.request.urlopen(pypi_url, timeout=30) as resp:
        data = json.loads(resp.read())

    files = data.get("releases", {}).get(version, []) or data.get("urls", [])
    wheel_map = _non_yanked_wheel_map("dcc-mcp-core", version, files)

    downloaded: List[Path] = []
    for (pattern, label), tag in zip(_core_wheel_patterns(), _core_wheel_tags()):
        matches = [filename for filename in wheel_map if _wheel_has_exact_tag(filename, *tag)]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one dcc-mcp-core wheel for {label} ({pattern}), found {sorted(matches)}")
        filename = matches[0]
        target = dest / filename
        print(f"Downloading {filename}")
        url, expected_sha256 = wheel_map[filename]
        _download_verified_wheel(url, expected_sha256, target)
        downloaded.append(target)

    return downloaded


def download_server_wheel(version: str, dest: Path) -> Path:
    """Download the Windows dcc-mcp-server wheel needed by the sidecar installer."""
    pypi_url = f"https://pypi.org/pypi/dcc-mcp-server/{version}/json"
    print(f"Querying {pypi_url}")
    with urllib.request.urlopen(pypi_url, timeout=30) as resp:
        data = json.loads(resp.read())

    files = data.get("releases", {}).get(version, []) or data.get("urls", [])
    wheel_map = _non_yanked_wheel_map("dcc-mcp-server", version, files)
    matches = [filename for filename in wheel_map if _wheel_has_exact_tag(filename, "py3", "none", "win_amd64")]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Windows dcc-mcp-server wheel for version {version}, found {sorted(matches)}")

    filename = matches[0]
    target = dest / filename
    print(f"Downloading {filename}")
    url, expected_sha256 = wheel_map[filename]
    _download_verified_wheel(url, expected_sha256, target)
    return target


def extract_wheel(wheel_path: Path, dest: Path, *, extensions_only: bool = False) -> None:
    """Extract package files from a wheel without importing platform binaries."""
    with zipfile.ZipFile(str(wheel_path)) as zf:
        members = _validated_zip_members(zf, wheel_path.name)
        assert_no_typing_extensions(members, wheel_path.name)
        selected = []
        output_keys = set()
        for member_path, info in members.items():
            if info.is_dir():
                continue
            parts = tuple(PurePosixPath(member_path).parts)
            distribution_metadata = any(part.endswith(".dist-info") for part in parts)
            if (
                extensions_only
                and not distribution_metadata
                and PurePosixPath(member_path).suffix.lower() not in {".pyd", ".dll"}
            ):
                continue
            relative_parts = parts
            if len(parts) >= 3 and parts[0].endswith(".data") and parts[1] == "scripts":
                relative_parts = ("scripts",) + parts[2:]
            elif any(part.endswith(".data") for part in parts):
                continue
            output_key = _portable_member_key(relative_parts)
            if output_key in output_keys:
                raise RuntimeError(f"{wheel_path.name} contains duplicate extracted archive paths")
            output_keys.add(output_key)
            selected.append((info, relative_parts))

        if os.path.lexists(str(dest)):
            dest_info = os.lstat(str(dest))
            if _is_link_or_reparse_info(dest_info):
                raise RuntimeError(f"{wheel_path.name} extraction root is unsafe")
        dest.mkdir(parents=True, exist_ok=True)
        destination_identity = _object_identity(os.lstat(str(dest)))
        _assert_owned_identity(dest, destination_identity, "wheel extraction root")
        destination_root = dest.resolve()
        for info, relative_parts in selected:
            _assert_owned_identity(dest, destination_identity, "wheel extraction root")
            with os.scandir(str(dest)) as _destination_handle:
                out = dest.joinpath(*relative_parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                parent_info = os.lstat(str(out.parent))
                if _is_link_or_reparse_info(parent_info):
                    raise RuntimeError(f"{wheel_path.name} extraction parent is unsafe: {out.parent.name}")
                if os.path.lexists(str(out)) and _is_link_or_reparse_info(os.lstat(str(out))):
                    raise RuntimeError(f"{wheel_path.name} extraction target is unsafe: {out.name}")
                try:
                    out.resolve(strict=False).relative_to(destination_root)
                except ValueError as exc:
                    raise RuntimeError(f"{wheel_path.name} extraction path escaped the runtime root") from exc
                with zf.open(info) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            _assert_owned_identity(dest, destination_identity, "wheel extraction root")


def _runtime_manifest_entries(root: Path):
    entries = []
    portable_keys = set()
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        if path.is_symlink():
            raise RuntimeError(f"runtime manifest cannot bind a link: {path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == RUNTIME_MANIFEST_NAME:
            continue
        parts = _portable_member_parts(relative, f"MZP {root.name}")
        portable_key = _portable_member_key(parts)
        if portable_key in portable_keys:
            raise RuntimeError(f"runtime manifest contains a duplicate portable path: {relative}")
        portable_keys.add(portable_key)
        data = path.read_bytes()
        entries.append({"path": relative, "sha256": _sha256_bytes(data), "size": len(data)})
    return entries


def _runtime_manifest_document(root: Path):
    return {"schema_version": 1, "root": root.name, "files": _runtime_manifest_entries(root)}


def write_runtime_manifests(payload: Path) -> None:
    for lane in ("python", "python37"):
        root = payload / lane
        if not root.is_dir():
            raise RuntimeError(f"MZP payload is missing required runtime root: {lane}")
        manifest = root / RUNTIME_MANIFEST_NAME
        manifest.write_text(
            json.dumps(_runtime_manifest_document(root), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


def _manifest_entries(document, lane: str):
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "root", "files"}
        or document.get("schema_version") != 1
        or document.get("root") != lane
        or not isinstance(document.get("files"), list)
    ):
        raise RuntimeError(f"MZP {lane} runtime manifest is invalid")
    entries = {}
    portable_keys = set()
    for item in document["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise RuntimeError(f"MZP {lane} runtime manifest is invalid")
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        parts = _portable_member_parts(path, f"MZP {lane} runtime manifest")
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
            raise RuntimeError(f"MZP {lane} runtime manifest is invalid")
        entries[normalized_path] = (digest, size)
        portable_keys.add(portable_key)
    return entries


def _mzp_run_text(version: str) -> str:
    _validate_release_version(version)
    return (
        f'name "dcc-mcp-3dsmax"\n'
        f'description "dcc-mcp-3dsmax {version} drag-and-drop installer"\n'
        "version 1\n"
        'run "install.ms"\n'
        'drop "install.ms"\n'
        "clear temp on MAX exit\n"
    )


def _trusted_control_files(version: str):
    """Return the exact bytes for every non-runtime MZP control member."""
    _validate_release_version(version)
    packaging_root = Path(__file__).resolve().parent
    try:
        return {
            "install.ms": _render_template("install.ms", VERSION=version).encode("utf-8"),
            "mzp.run": _mzp_run_text(version).encode("utf-8"),
            "payload/README.txt": (packaging_root / "README.txt").read_bytes(),
            "payload/startup/dcc_mcp_3dsmax_startup.ms": (_template_dir() / "dcc_mcp_3dsmax_startup.ms").read_bytes(),
        }
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("MZP trusted control templates are unavailable") from exc


def _verify_mzp_control_members(zf: zipfile.ZipFile, members, source: str, version: str) -> None:
    expected = _trusted_control_files(version)
    runtime_prefixes = ("payload/python/", "payload/python37/")
    observed_control_paths = {
        path for path, info in members.items() if not info.is_dir() and not path.startswith(runtime_prefixes)
    }
    if observed_control_paths != set(expected):
        raise RuntimeError(f"MZP control-plane member allowlist mismatch: {source}")
    for path, expected_bytes in expected.items():
        info = members.get(path)
        if info is None or info.is_dir():
            raise RuntimeError(f"MZP control-plane member is missing: {path}")
        actual = zf.read(info)
        if actual != expected_bytes:
            raise RuntimeError(f"MZP control-plane member bytes changed: {path}")


def _verify_mzp_archive_integrity(zf: zipfile.ZipFile, source: str, expected_version: str = None) -> None:
    members = _validated_zip_members(zf, source)
    assert_no_typing_extensions(members, source)
    if expected_version is None:
        match = re.fullmatch(rf"{re.escape(PACKAGE_NAME)}-([0-9]+\.[0-9]+\.[0-9]+)-{TARGET_PLATFORM}\.mzp", source)
        if not match:
            raise RuntimeError(f"MZP release version is unavailable: {source}")
        expected_version = match.group(1)
    _verify_mzp_control_members(zf, members, source, expected_version)
    for lane in ("python", "python37"):
        prefix = f"payload/{lane}/"
        manifest_path = prefix + RUNTIME_MANIFEST_NAME
        manifest_info = members.get(manifest_path)
        if manifest_info is None or manifest_info.is_dir():
            raise RuntimeError(f"MZP {lane} runtime manifest is missing")
        try:
            document = json.loads(zf.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"MZP {lane} runtime manifest is invalid") from exc
        expected = _manifest_entries(document, lane)
        observed = {}
        for member_path, info in members.items():
            if info.is_dir() or not member_path.startswith(prefix) or member_path == manifest_path:
                continue
            relative = member_path[len(prefix) :]
            data = zf.read(info)
            observed[relative] = (_sha256_bytes(data), len(data))
        if observed != expected:
            raise RuntimeError(f"MZP {lane} runtime manifest integrity check failed")


def _object_identity(info) -> Tuple[int, int, int, int]:
    return (
        getattr(info, "st_dev", 0),
        getattr(info, "st_ino", 0),
        getattr(info, "st_mode", 0),
        getattr(info, "st_file_attributes", 0),
    )


def _is_link_or_reparse_info(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _assert_owned_identity(path: Path, expected, label: str) -> None:
    try:
        info = os.lstat(str(path))
    except OSError as exc:
        raise RuntimeError(f"{label} disappeared during cleanup") from exc
    actual = _object_identity(info)
    expected_identity = _object_identity(expected) if hasattr(expected, "st_mode") else tuple(expected)
    if actual != expected_identity:
        raise RuntimeError(f"{label} identity changed")
    if _is_link_or_reparse_info(info):
        raise RuntimeError(f"refusing linked/reparse {label}")


def _remove_tree_no_links(root: Path, root_identity=None) -> None:
    """Delete a detached tree while rebinding every physical object seam."""
    root_info = os.lstat(str(root))
    if _is_link_or_reparse_info(root_info):
        raise RuntimeError("refusing linked/reparse tombstone root")
    expected_root = root_identity or _object_identity(root_info)

    def remove_directory(directory: Path, directory_identity) -> None:
        _assert_owned_identity(directory, directory_identity, "cleanup directory")
        try:
            entries = list(os.scandir(str(directory)))
        except OSError as exc:
            raise RuntimeError(f"unable to enumerate cleanup directory {directory}") from exc
        for entry in entries:
            # Rebind the containing directory before and after every child seam.
            _assert_owned_identity(directory, directory_identity, "cleanup parent")
            child = Path(entry.path)
            try:
                child_info = os.lstat(str(child))
            except FileNotFoundError:
                continue
            if _is_link_or_reparse_info(child_info):
                raise RuntimeError("refusing nested linked/reparse payload")
            child_identity = _object_identity(child_info)
            if stat.S_ISDIR(child_info.st_mode):
                remove_directory(child, child_identity)
            else:
                _assert_owned_identity(child, child_identity, "cleanup file")
                child.unlink()
                if os.path.lexists(str(child)):
                    raise RuntimeError("nested cleanup target reappeared")
            _assert_owned_identity(directory, directory_identity, "cleanup parent")
        _assert_owned_identity(directory, directory_identity, "cleanup directory")
        directory.rmdir()
        if os.path.lexists(str(directory)):
            raise RuntimeError("owned path cleanup incomplete")

    remove_directory(root, expected_root)


def _remove_owned_path(path: Path, identity, *, directory: bool) -> None:
    """Atomically detach an identity-bound path before deleting its contents."""
    tombstone = path.with_name(path.name + ".deleting-" + uuid.uuid4().hex)
    parent = path.parent
    parent_info = os.lstat(str(parent))
    if _is_link_or_reparse_info(parent_info):
        raise RuntimeError("refusing linked/reparse cleanup parent")
    parent_identity = _object_identity(parent_info)
    expected_identity = _object_identity(identity) if hasattr(identity, "st_mode") else tuple(identity)
    _assert_owned_identity(path, expected_identity, "owned path")
    try:
        if os.path.lexists(str(tombstone)):
            raise RuntimeError(f"quarantine collision at {tombstone}")
        _assert_owned_identity(parent, parent_identity, "cleanup parent")
        os.rename(str(path), str(tombstone))
        _assert_owned_identity(parent, parent_identity, "cleanup parent after detach")
        _assert_owned_identity(tombstone, expected_identity, "tombstone root")
        tombstone_identity = _object_identity(os.lstat(str(tombstone)))
        if directory:
            _remove_tree_no_links(tombstone, tombstone_identity)
        else:
            _assert_owned_identity(tombstone, tombstone_identity, "tombstone file")
            tombstone.unlink()
            if os.path.lexists(str(tombstone)):
                raise RuntimeError("owned path cleanup incomplete")
        _assert_owned_identity(parent, parent_identity, "cleanup parent after delete")
    except Exception as exc:
        # Restore only when both the parent and detached object still match.
        try:
            _assert_owned_identity(parent, parent_identity, "cleanup parent during recovery")
            if os.path.lexists(str(tombstone)):
                _assert_owned_identity(tombstone, expected_identity, "quarantine tombstone during recovery")
                if not os.path.lexists(str(path)):
                    os.rename(str(tombstone), str(path))
                    _assert_owned_identity(path, expected_identity, "restored owned path")
        except Exception as recovery_exc:
            raise RuntimeError(f"quarantine retained for recovery at {tombstone}: {recovery_exc}") from exc
        raise RuntimeError(f"cleanup failed; quarantine restored from {tombstone}: {exc}") from exc


def _assert_output_root(output: Path, identity) -> None:
    """Rebind the output directory before each producer/destructive seam."""
    _assert_owned_identity(output, identity, "MZP output root")


def _assert_archive_root(archive_root: Path, identity) -> None:
    _assert_owned_identity(archive_root, identity, "MZP archive root")


def copy_package(project_root: Path, dest: Path) -> None:
    src = project_root / "src" / PY_PACKAGE_NAME
    target = dest / PY_PACKAGE_NAME
    if os.path.lexists(str(target)):
        existing = os.lstat(str(target))
        if stat.S_ISLNK(existing.st_mode) or bool(getattr(existing, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RuntimeError("refusing to replace linked package target")
        if target.resolve().parent != dest.resolve():
            raise RuntimeError("package target escapes destination")
        current = os.lstat(str(target))
        if (current.st_dev, current.st_ino, current.st_mode) != (existing.st_dev, existing.st_ino, existing.st_mode):
            raise RuntimeError("package target identity changed")
        _remove_owned_path(target, existing, directory=True)
        if os.path.lexists(str(target)):
            raise RuntimeError("package target cleanup incomplete")
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def _template_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _read_template(name: str) -> str:
    return (_template_dir() / name).read_text(encoding="utf-8")


def _render_template(name: str, **tokens: str) -> str:
    if "VERSION" in tokens:
        _validate_release_version(tokens["VERSION"])
    text = _read_template(name)
    for key, value in tokens.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _write_utf8_lf(path: Path, text: str) -> None:
    """Write generated control text with deterministic LF bytes on every host."""
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def write_startup_template(package_root: Path) -> None:
    startup = package_root / "startup" / "dcc_mcp_3dsmax_startup.ms"
    startup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_template_dir() / "dcc_mcp_3dsmax_startup.ms", startup)


def write_mzp_run(package_root: Path, version: str) -> None:
    _validate_release_version(version)
    _write_utf8_lf(package_root / "mzp.run", _mzp_run_text(version))


def write_install_script(package_root: Path, version: str) -> None:
    _validate_release_version(version)
    _write_utf8_lf(package_root / "install.ms", _render_template("install.ms", VERSION=version))


def assemble(project_root: Path, version: str, output: Path) -> Path:
    # Validate untrusted input before the first filesystem operation.
    _validate_release_version(version)
    raw_output = Path(output)
    if os.path.lexists(str(raw_output)):
        raw_info = os.lstat(str(raw_output))
        if _is_link_or_reparse_info(raw_info):
            raise RuntimeError("refusing linked/reparse MZP output root")
    output = Path(output).resolve()
    archive_root = output / f"{PACKAGE_NAME}-{version}-{TARGET_PLATFORM}"
    archive_root_resolved = archive_root.resolve()
    if archive_root_resolved.parent != output:
        raise RuntimeError("MZP archive path escapes output root")
    output.mkdir(parents=True, exist_ok=True)
    output_identity = os.lstat(str(output))
    _assert_output_root(output, output_identity)
    if os.path.lexists(str(archive_root)):
        before = os.lstat(str(archive_root))
        if stat.S_ISLNK(before.st_mode) or bool(getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RuntimeError("refusing to replace linked MZP archive root")
        if archive_root.resolve().parent != output:
            raise RuntimeError("MZP archive path escapes output root")
        current = os.lstat(str(archive_root))
        if (before.st_dev, before.st_ino, before.st_mode) != (current.st_dev, current.st_ino, current.st_mode):
            raise RuntimeError("MZP archive root identity changed")
        output_before_delete = os.lstat(str(output))
        if (output_identity.st_dev, output_identity.st_ino, output_identity.st_mode) != (output_before_delete.st_dev, output_before_delete.st_ino, output_before_delete.st_mode):
            raise RuntimeError("MZP output root identity changed")
        _remove_owned_path(archive_root, before, directory=True)
        output_after_delete = os.lstat(str(output))
        if (output_before_delete.st_dev, output_before_delete.st_ino, output_before_delete.st_mode) != (output_after_delete.st_dev, output_after_delete.st_ino, output_after_delete.st_mode):
            raise RuntimeError("MZP output root identity changed")
        if os.path.lexists(str(archive_root)):
            raise RuntimeError("MZP archive root cleanup incomplete")
    payload = archive_root / "payload"
    _assert_output_root(output, output_identity)
    if not os.path.lexists(str(archive_root)):
        archive_root.mkdir()
    archive_root_identity = os.lstat(str(archive_root))
    _assert_archive_root(archive_root, archive_root_identity)
    python_dir = payload / "python"
    python37_dir = payload / "python37"
    with os.scandir(str(archive_root)) as _archive_handle:
        python_dir.mkdir(parents=True)
    _assert_output_root(output, output_identity)
    _assert_archive_root(archive_root, archive_root_identity)

    core_version = resolve_core_version(project_root)
    server_version = resolve_server_version(project_root)
    with tempfile.TemporaryDirectory() as tmp:
        temporary = Path(tmp)
        wheels = download_core_wheels(core_version, temporary)
        server_wheel = download_server_wheel(server_version, temporary)
        verify_python37_runtime_contract(
            wheels,
            server_wheel,
            expected_core_version=core_version,
            expected_server_version=server_version,
        )
        abi3_wheels = [wheel for wheel in wheels if "abi3" in wheel.name]
        cp37_wheels = [wheel for wheel in wheels if "cp37-cp37m" in wheel.name]

        for wheel in abi3_wheels or wheels:
            _assert_output_root(output, output_identity)
            _assert_archive_root(archive_root, archive_root_identity)
            print(f"Extracting {wheel.name} to python/")
            extract_wheel(wheel, python_dir)

        if cp37_wheels:
            with os.scandir(str(archive_root)) as _archive_handle:
                shutil.copytree(python_dir, python37_dir)
            _assert_output_root(output, output_identity)
            _assert_archive_root(archive_root, archive_root_identity)
            for wheel in cp37_wheels:
                print(f"Extracting {wheel.name} extension files to python37/")
                extract_wheel(wheel, python37_dir, extensions_only=True)

        print(f"Extracting {server_wheel.name} to python/")
        _assert_output_root(output, output_identity)
        _assert_archive_root(archive_root, archive_root_identity)
        extract_wheel(server_wheel, python_dir)
        if python37_dir.exists():
            _assert_output_root(output, output_identity)
            _assert_archive_root(archive_root, archive_root_identity)
            print(f"Extracting {server_wheel.name} to python37/")
            extract_wheel(server_wheel, python37_dir)

    _assert_output_root(output, output_identity)
    _assert_archive_root(archive_root, archive_root_identity)
    copy_package(project_root, python_dir)
    if python37_dir.exists():
        _assert_output_root(output, output_identity)
        _assert_archive_root(archive_root, archive_root_identity)
        copy_package(project_root, python37_dir)

    _assert_output_root(output, output_identity)
    _assert_archive_root(archive_root, archive_root_identity)
    write_runtime_manifests(payload)

    for payload_root in (python_dir, python37_dir):
        if not payload_root.is_dir():
            raise RuntimeError(f"MZP payload is missing required runtime root: {payload_root.name}")
        assert_no_typing_extensions(
            (path.relative_to(payload_root).as_posix() for path in payload_root.rglob("*")),
            f"MZP {payload_root.name}",
        )

    readme = project_root / "packaging" / "README.txt"
    if readme.exists():
        _assert_output_root(output, output_identity)
        _assert_archive_root(archive_root, archive_root_identity)
        shutil.copy2(readme, payload / "README.txt")

    _assert_output_root(output, output_identity)
    _assert_archive_root(archive_root, archive_root_identity)
    write_startup_template(payload)
    _assert_output_root(output, output_identity)
    _assert_archive_root(archive_root, archive_root_identity)
    write_mzp_run(archive_root, version)
    _assert_output_root(output, output_identity)
    _assert_archive_root(archive_root, archive_root_identity)
    write_install_script(archive_root, version)

    mzp_base = output / archive_root.name
    _assert_output_root(output, output_identity)
    _assert_archive_root(archive_root, archive_root_identity)
    zip_path = shutil.make_archive(str(mzp_base), "zip", root_dir=archive_root)
    mzp_path = Path(zip_path).with_suffix(".mzp")
    if os.path.lexists(str(mzp_path)):
        existing_mzp = os.lstat(str(mzp_path))
        if stat.S_ISLNK(existing_mzp.st_mode) or bool(getattr(existing_mzp, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RuntimeError("refusing to replace linked MZP file")
        current_mzp = os.lstat(str(mzp_path))
        if (current_mzp.st_dev, current_mzp.st_ino, current_mzp.st_mode) != (existing_mzp.st_dev, existing_mzp.st_ino, existing_mzp.st_mode):
            raise RuntimeError("MZP output file identity changed")
        _remove_owned_path(mzp_path, existing_mzp, directory=False)
    _assert_output_root(output, output_identity)
    Path(zip_path).rename(mzp_path)
    _assert_output_root(output, output_identity)
    with zipfile.ZipFile(str(mzp_path)) as zf:
        _verify_mzp_archive_integrity(zf, mzp_path.name, version)
    print(f"Created {mzp_path}")
    return mzp_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble dcc-mcp-3dsmax MZP installer")
    parser.add_argument("--version", required=True, help="Package version, for example 0.1.0")
    parser.add_argument("--output", default="dist/mzp", help="Output directory")
    parser.add_argument("--project-root", default=".", help="Project root")
    args = parser.parse_args()

    assemble(Path(args.project_root).resolve(), args.version, Path(args.output).resolve())


if __name__ == "__main__":
    main()
