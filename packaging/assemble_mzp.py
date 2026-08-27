#!/usr/bin/env python3
"""Assemble a drag-and-drop 3ds Max MZP installer for dcc-mcp-3dsmax.

The output is a ZIP-compatible ``.mzp`` archive with an MZP control file
(``mzp.run``) at the archive root. The control file runs ``install.ms`` when
users run the package or drag it into the viewport.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import List, Tuple
from urllib.parse import urlsplit

PACKAGE_NAME = "dcc-mcp-3dsmax"
PY_PACKAGE_NAME = "dcc_mcp_3dsmax"
CORE_PACKAGE_NAME = "dcc_mcp_core"
SERVER_PACKAGE_NAME = "dcc_mcp_server"
TARGET_PLATFORM = "win64"
RUNTIME_LOCK_FILENAME = "mzp-runtime.lock.json"
REQUIRED_RUNTIME_DISTRIBUTION = "typing-extensions"
REQUIRED_RUNTIME_FILES = [
    "typing_extensions-4.7.1.dist-info/LICENSE",
    "typing_extensions-4.7.1.dist-info/METADATA",
    "typing_extensions-4.7.1.dist-info/RECORD",
    "typing_extensions-4.7.1.dist-info/WHEEL",
    "typing_extensions.py",
]


def _safe_wheel_path(name: str) -> PurePosixPath:
    parts = name.split("/")
    if not name or "\\" in name or any(not part or part in {".", ".."} or ":" in part for part in parts):
        raise RuntimeError(f"unsafe wheel path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise RuntimeError(f"unsafe wheel path: {name!r}")
    return path


def load_runtime_dependency(project_root: Path) -> dict:
    """Load the one authenticated dependency missing from the embedded payload."""
    path = project_root / "packaging" / RUNTIME_LOCK_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"runtime lock is unreadable: {path}: {exc}") from exc
    distributions = data.get("distributions") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(distributions, list) or len(distributions) != 1:
        raise RuntimeError(f"runtime lock must contain exactly one {REQUIRED_RUNTIME_DISTRIBUTION} distribution")
    dependency = distributions[0]
    artifact = dependency.get("artifact") if isinstance(dependency, dict) else None
    if not isinstance(dependency, dict) or dependency.get("name") != REQUIRED_RUNTIME_DISTRIBUTION or dependency.get("version") != "4.7.1":
        raise RuntimeError(f"runtime lock is missing exact {REQUIRED_RUNTIME_DISTRIBUTION} 4.7.1")
    if dependency.get("license") != "PSF-2.0" or dependency.get("payloads") != ["python", "python37"]:
        raise RuntimeError(f"runtime lock has invalid {REQUIRED_RUNTIME_DISTRIBUTION} license or payload lanes")
    if dependency.get("files") != REQUIRED_RUNTIME_FILES:
        raise RuntimeError(f"runtime lock has drifted {REQUIRED_RUNTIME_DISTRIBUTION} file list")
    if not isinstance(artifact, dict):
        raise RuntimeError(f"runtime lock is missing {REQUIRED_RUNTIME_DISTRIBUTION} artifact")
    filename = "typing_extensions-4.7.1-py3-none-any.whl"
    parsed = urlsplit(artifact.get("url", ""))
    if (
        artifact.get("filename") != filename
        or parsed.scheme != "https"
        or parsed.hostname != "files.pythonhosted.org"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"runtime lock has invalid {REQUIRED_RUNTIME_DISTRIBUTION} artifact URL")
    sha256 = artifact.get("sha256")
    if (
        Path(parsed.path).name != filename
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        or len(set(sha256)) == 1
    ):
        raise RuntimeError(f"runtime lock has invalid {REQUIRED_RUNTIME_DISTRIBUTION} sha256")
    if not isinstance(artifact.get("size"), int) or artifact["size"] <= 0:
        raise RuntimeError(f"runtime lock has invalid {REQUIRED_RUNTIME_DISTRIBUTION} artifact size")
    return dependency


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wheel_metadata(zf: zipfile.ZipFile, distribution: str):
    metadata_paths = [name for name in zf.namelist() if name.endswith(".dist-info/METADATA")]
    if len(metadata_paths) != 1:
        raise RuntimeError(f"{distribution} wheel has ambiguous distribution metadata")
    try:
        parsed = Parser().parsestr(zf.read(metadata_paths[0]).decode("utf-8"))
    except (KeyError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{distribution} wheel metadata is invalid") from exc
    return parsed


def _runtime_requirements(metadata) -> List[str]:
    return [requirement.strip() for requirement in metadata.get_all("Requires-Dist", []) if "extra ==" not in requirement]


def _release_version(value: str, source: str) -> Tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value.strip()):
        raise RuntimeError(f"{source} has missing or invalid version: {value!r}")
    return tuple(int(part) for part in value.strip().split("."))


def _compare_release_versions(left: Tuple[int, ...], right: Tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    normalized_left = left + (0,) * (width - len(left))
    normalized_right = right + (0,) * (width - len(right))
    return (normalized_left > normalized_right) - (normalized_left < normalized_right)


def _satisfies_release_specifier(version: str, specifier: str) -> bool:
    parsed_version = _release_version(version, "selected dcc-mcp-server wheel")
    for clause in specifier.split(","):
        match = re.fullmatch(r"\s*(~=|==|!=|<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)*(?:\.\*)?)\s*", clause)
        if not match:
            raise RuntimeError(f"dcc-mcp-core has an invalid dcc-mcp-server specifier: {specifier!r}")
        operator, expected_text = match.groups()
        wildcard = expected_text.endswith(".*")
        if wildcard and operator not in {"==", "!="}:
            raise RuntimeError(f"dcc-mcp-core has an invalid dcc-mcp-server specifier: {specifier!r}")
        expected = _release_version(expected_text[:-2] if wildcard else expected_text, "Core specifier")
        comparison = _compare_release_versions(parsed_version, expected)
        if wildcard:
            matches = parsed_version[: len(expected)] == expected
        elif operator == "~=":
            if len(expected) < 2:
                raise RuntimeError(f"dcc-mcp-core has an invalid dcc-mcp-server specifier: {specifier!r}")
            matches = comparison >= 0 and parsed_version[: len(expected) - 1] == expected[:-1]
        else:
            matches = {
                "==": comparison == 0,
                "!=": comparison != 0,
                "<": comparison < 0,
                "<=": comparison <= 0,
                ">": comparison > 0,
                ">=": comparison >= 0,
            }[operator]
        if operator == "!=" and wildcard:
            matches = not matches
        if not matches:
            return False
    return True


def _server_requirement_specifier(requirement: str):
    name_match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if not name_match or re.sub(r"[-_.]+", "-", name_match.group(1)).lower() != "dcc-mcp-server":
        return None
    remainder = requirement[name_match.end() :].strip()
    if remainder.startswith("("):
        if not remainder.endswith(")") or "(" in remainder[1:] or ")" in remainder[:-1]:
            raise RuntimeError(f"dcc-mcp-core has an invalid dcc-mcp-server requirement: {requirement!r}")
        remainder = remainder[1:-1].strip()
    if not remainder or ";" in remainder:
        raise RuntimeError(f"dcc-mcp-core has a missing or invalid dcc-mcp-server specifier: {requirement!r}")
    return remainder


def verify_python37_runtime_contract(core_wheels: List[Path], server_wheel: Path, dependency: dict) -> None:
    """Prove the selected Core/server Python 3.7 runtime closure is present."""
    cp37_wheels = [wheel for wheel in core_wheels if "cp37-cp37m" in wheel.name]
    if len(cp37_wheels) != 1:
        raise RuntimeError(f"expected one dcc-mcp-core cp37 wheel, found {[wheel.name for wheel in cp37_wheels]}")
    with zipfile.ZipFile(str(cp37_wheels[0])) as zf:
        core_metadata = _wheel_metadata(zf, "dcc-mcp-core")
    with zipfile.ZipFile(str(server_wheel)) as zf:
        server_metadata = _wheel_metadata(zf, "dcc-mcp-server")

    typing_pattern = re.compile(
        r"^typing[-_]extensions\s*==\s*([^\s;]+)\s*;\s*python_full_version\s*<\s*['\"]3\.8['\"]$",
        re.IGNORECASE,
    )
    runtime_requirements = _runtime_requirements(core_metadata)
    typing_matches = [typing_pattern.match(requirement) for requirement in runtime_requirements]
    typing_versions = {match.group(1) for match in typing_matches if match}
    server_contracts = []
    for requirement in runtime_requirements:
        specifier = _server_requirement_specifier(requirement)
        if specifier is not None:
            server_contracts.append((requirement, specifier))
    server_requirements = [requirement for requirement, _specifier in server_contracts]
    recognized = set(server_requirements)
    recognized.update(requirement for requirement, match in zip(runtime_requirements, typing_matches) if match)
    if recognized != set(runtime_requirements):
        raise RuntimeError(f"unclosed dcc-mcp-core Python 3.7 runtime dependencies: {sorted(set(runtime_requirements) - recognized)}")
    if len(server_requirements) != 1:
        raise RuntimeError(f"expected one dcc-mcp-server runtime requirement, found {server_requirements}")
    if _runtime_requirements(server_metadata):
        raise RuntimeError(f"unclosed dcc-mcp-server runtime dependencies: {_runtime_requirements(server_metadata)}")
    if re.sub(r"[-_.]+", "-", server_metadata.get("Name", "")).lower() != "dcc-mcp-server":
        raise RuntimeError("selected server wheel has an ambiguous distribution identity")
    server_specifier = server_contracts[0][1]
    server_version = server_metadata.get("Version", "")
    if not _satisfies_release_specifier(server_version, server_specifier):
        raise RuntimeError(
            f"server wheel version {server_version} does not satisfy dcc-mcp-core requirement {server_specifier}"
        )
    if len(typing_versions) != 1:
        raise RuntimeError(
            f"Core wheels must declare one exact Python 3.7 {REQUIRED_RUNTIME_DISTRIBUTION} pin; "
            f"found {sorted(typing_versions)}"
        )
    core_version = next(iter(typing_versions))
    if core_version != dependency["version"]:
        raise RuntimeError(
            f"Core requires {REQUIRED_RUNTIME_DISTRIBUTION} {core_version} but runtime lock pins "
            f"{dependency['version']}"
        )


def download_locked_artifact(dependency: dict, dest: Path) -> Path:
    """Download one immutable wheel and verify its locked size and SHA-256."""
    artifact = dependency["artifact"]
    target = dest / artifact["filename"]
    print(f"Downloading locked {dependency['name']} {dependency['version']}")
    with urllib.request.urlopen(artifact["url"], timeout=30) as response, target.open("wb") as stream:
        if response.geturl() != artifact["url"]:
            raise RuntimeError(f"locked {dependency['name']} artifact redirected away from its exact URL")
        shutil.copyfileobj(response, stream)
    actual_size = target.stat().st_size
    actual_sha256 = sha256_file(target)
    if actual_size != artifact["size"]:
        raise RuntimeError(
            f"locked {dependency['name']} artifact size mismatch: expected {artifact['size']}, got {actual_size}"
        )
    if actual_sha256 != artifact["sha256"]:
        raise RuntimeError(
            f"locked {dependency['name']} artifact sha256 mismatch: expected {artifact['sha256']}, got {actual_sha256}"
        )
    return target


def extract_locked_wheel(wheel_path: Path, dest: Path, dependency: dict) -> None:
    """Extract exactly the audited regular files from an authenticated wheel."""
    artifact = dependency["artifact"]
    if wheel_path.name != artifact["filename"]:
        raise RuntimeError(f"locked {dependency['name']} artifact filename mismatch")
    if wheel_path.stat().st_size != artifact["size"]:
        raise RuntimeError(f"locked {dependency['name']} artifact size mismatch")
    if sha256_file(wheel_path) != artifact["sha256"]:
        raise RuntimeError(f"locked {dependency['name']} artifact sha256 mismatch")

    with zipfile.ZipFile(str(wheel_path)) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        for info in infos:
            _safe_wheel_path(info.filename)
            if stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}:
                raise RuntimeError(f"wheel contains a link or non-regular file: {info.filename}")
        actual_files = sorted(info.filename for info in infos)
        if actual_files != REQUIRED_RUNTIME_FILES or len({name.casefold() for name in actual_files}) != len(actual_files):
            raise RuntimeError(f"locked {dependency['name']} wheel file list drift")
        metadata = _wheel_metadata(zf, dependency["name"])
        if re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower() != dependency["name"] or metadata.get(
            "Version"
        ) != dependency["version"]:
            raise RuntimeError(f"locked {dependency['name']} wheel has ambiguous distribution identity")
        outputs = []
        for info in infos:
            relative = _safe_wheel_path(info.filename)
            out = dest.joinpath(*relative.parts)
            if out.exists():
                raise RuntimeError(f"locked {dependency['name']} would create a duplicate or ambiguous file: {relative}")
            outputs.append((info, out))
        for info, out in outputs:
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def resolve_core_version(project_root: Path) -> str:
    """Resolve the latest PyPI dcc-mcp-core version satisfying pyproject."""
    return resolve_dependency_version(project_root, "dcc-mcp-core")


def resolve_server_version(project_root: Path) -> str:
    """Resolve the latest PyPI dcc-mcp-server version satisfying pyproject."""
    return resolve_dependency_version(project_root, "dcc-mcp-server")


def resolve_dependency_version(project_root: Path, distribution: str) -> str:
    """Resolve the latest PyPI version satisfying a pyproject lower bound."""
    content = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(distribution)}>=(\d+\.\d+\.\d+)", content)
    if not match:
        raise RuntimeError(f"Cannot find {distribution} minimum version in pyproject.toml")
    minimum = match.group(1)

    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{distribution}/json", timeout=15) as resp:
            data = json.loads(resp.read())
        latest = data.get("info", {}).get("version", "")
        if latest and _version_gte(latest, minimum):
            print(f"Resolved {distribution} {latest} from PyPI")
            return latest
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not query latest {distribution} ({exc}); using {minimum}")

    return minimum


def _version_gte(version: str, minimum: str) -> bool:
    return [int(part) for part in version.split(".")] >= [int(part) for part in minimum.split(".")]


def _core_wheel_patterns() -> List[Tuple[str, str]]:
    return [
        ("cp37-cp37m-win_amd64", "Python 3.7 / 3ds Max 2022"),
        ("cp38-abi3-win_amd64", "Python 3.8+ / modern 3ds Max"),
    ]


def download_core_wheels(version: str, dest: Path) -> List[Path]:
    """Download Windows dcc-mcp-core wheels needed by the offline installer."""
    pypi_url = f"https://pypi.org/pypi/dcc-mcp-core/{version}/json"
    print(f"Querying {pypi_url}")
    with urllib.request.urlopen(pypi_url, timeout=30) as resp:
        data = json.loads(resp.read())

    files = data.get("releases", {}).get(version, []) or data.get("urls", [])
    wheel_map = {item["filename"]: item["url"] for item in files if item.get("packagetype") == "bdist_wheel"}

    downloaded: List[Path] = []
    for pattern, label in _core_wheel_patterns():
        matches = [filename for filename in wheel_map if pattern in filename]
        if not matches:
            print(f"Warning: no dcc-mcp-core wheel found for {label} ({pattern})")
            continue
        filename = matches[0]
        target = dest / filename
        if not target.exists():
            print(f"Downloading {filename}")
            urllib.request.urlretrieve(wheel_map[filename], str(target))
        downloaded.append(target)

    if not downloaded:
        raise RuntimeError(f"No Windows dcc-mcp-core wheels found for version {version}")
    return downloaded


def download_server_wheel(version: str, dest: Path) -> Path:
    """Download the Windows dcc-mcp-server wheel needed by the sidecar installer."""
    pypi_url = f"https://pypi.org/pypi/dcc-mcp-server/{version}/json"
    print(f"Querying {pypi_url}")
    with urllib.request.urlopen(pypi_url, timeout=30) as resp:
        data = json.loads(resp.read())

    files = data.get("releases", {}).get(version, []) or data.get("urls", [])
    wheel_map = {item["filename"]: item["url"] for item in files if item.get("packagetype") == "bdist_wheel"}
    matches = [filename for filename in wheel_map if filename.endswith("win_amd64.whl")]
    if not matches:
        raise RuntimeError(f"No Windows dcc-mcp-server wheel found for version {version}")

    filename = matches[0]
    target = dest / filename
    if not target.exists():
        print(f"Downloading {filename}")
        urllib.request.urlretrieve(wheel_map[filename], str(target))
    return target


def extract_wheel(wheel_path: Path, dest: Path, *, extensions_only: bool = False) -> None:
    """Extract package files from a wheel without importing platform binaries."""
    with zipfile.ZipFile(str(wheel_path)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if any(part.endswith(".dist-info") for part in parts):
                continue
            if extensions_only and Path(info.filename).suffix.lower() not in {".pyd", ".dll"}:
                continue
            relative_path = Path(info.filename)
            if len(parts) >= 3 and parts[0].endswith(".data") and parts[1] == "scripts":
                relative_path = Path("scripts", *parts[2:])
            elif any(part.endswith(".data") for part in parts):
                continue
            out = dest / relative_path
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def copy_package(project_root: Path, dest: Path) -> None:
    src = project_root / "src" / PY_PACKAGE_NAME
    target = dest / PY_PACKAGE_NAME
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def _template_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _read_template(name: str) -> str:
    return (_template_dir() / name).read_text(encoding="utf-8")


def _render_template(name: str, **tokens: str) -> str:
    text = _read_template(name)
    for key, value in tokens.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def write_startup_template(package_root: Path) -> None:
    startup = package_root / "startup" / "dcc_mcp_3dsmax_startup.ms"
    startup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_template_dir() / "dcc_mcp_3dsmax_startup.ms", startup)


def write_mzp_run(package_root: Path, version: str) -> None:
    (package_root / "mzp.run").write_text(
        f"""name "dcc-mcp-3dsmax"
description "dcc-mcp-3dsmax {version} drag-and-drop installer"
version 1
run "install.ms"
drop "install.ms"
clear temp on MAX exit
""",
        encoding="utf-8",
    )


def write_install_script(package_root: Path, version: str) -> None:
    (package_root / "install.ms").write_text(
        _render_template("install.ms", VERSION=version),
        encoding="utf-8",
    )


def assemble(project_root: Path, version: str, output: Path) -> Path:
    runtime_dependency = load_runtime_dependency(project_root)
    output.mkdir(parents=True, exist_ok=True)
    archive_root = output / f"{PACKAGE_NAME}-{version}-{TARGET_PLATFORM}"
    if archive_root.exists():
        shutil.rmtree(archive_root)
    payload = archive_root / "payload"
    python_dir = payload / "python"
    python37_dir = payload / "python37"
    python_dir.mkdir(parents=True)

    core_version = resolve_core_version(project_root)
    server_version = resolve_server_version(project_root)
    with tempfile.TemporaryDirectory() as tmp:
        temporary = Path(tmp)
        wheels = download_core_wheels(core_version, temporary)
        server_wheel = download_server_wheel(server_version, temporary)
        verify_python37_runtime_contract(wheels, server_wheel, runtime_dependency)
        abi3_wheels = [wheel for wheel in wheels if "abi3" in wheel.name]
        cp37_wheels = [wheel for wheel in wheels if "cp37-cp37m" in wheel.name]

        for wheel in abi3_wheels or wheels:
            print(f"Extracting {wheel.name} to python/")
            extract_wheel(wheel, python_dir)

        if cp37_wheels:
            shutil.copytree(python_dir, python37_dir)
            for wheel in cp37_wheels:
                print(f"Extracting {wheel.name} extension files to python37/")
                extract_wheel(wheel, python37_dir, extensions_only=True)

        print(f"Extracting {server_wheel.name} to python/")
        extract_wheel(server_wheel, python_dir)
        if python37_dir.exists():
            print(f"Extracting {server_wheel.name} to python37/")
            extract_wheel(server_wheel, python37_dir)

        payload_roots = {"python": python_dir, "python37": python37_dir}
        locked_wheel = download_locked_artifact(runtime_dependency, temporary)
        for payload_name in runtime_dependency["payloads"]:
            destination = payload_roots[payload_name]
            if not destination.is_dir():
                raise RuntimeError(
                    f"cannot install locked {runtime_dependency['name']}: MZP payload is missing {payload_name}"
                )
            print(
                f"Extracting locked {runtime_dependency['name']} {runtime_dependency['version']} to {payload_name}/"
            )
            extract_locked_wheel(locked_wheel, destination, runtime_dependency)

    copy_package(project_root, python_dir)
    if python37_dir.exists():
        copy_package(project_root, python37_dir)

    readme = project_root / "packaging" / "README.txt"
    if readme.exists():
        shutil.copy2(readme, payload / "README.txt")
    shutil.copy2(project_root / "packaging" / RUNTIME_LOCK_FILENAME, payload / RUNTIME_LOCK_FILENAME)

    write_startup_template(payload)
    write_mzp_run(archive_root, version)
    write_install_script(archive_root, version)

    mzp_base = output / archive_root.name
    zip_path = shutil.make_archive(str(mzp_base), "zip", root_dir=archive_root)
    mzp_path = Path(zip_path).with_suffix(".mzp")
    if mzp_path.exists():
        mzp_path.unlink()
    Path(zip_path).rename(mzp_path)
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
