"""Release surfaces must move together under release-please."""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.7-3.10
    import tomli as tomllib


ROOT = Path(__file__).parents[1]
RELEASE_MARKER = "x-release-please-version"
VERSION_BEFORE_MARKER = re.compile(r"(?P<version>\d+\.\d+\.\d+)[^\r\n]*x-release-please-version")
RUNTIME_VERSION = re.compile(r"^__version__\s*=\s*['\"](?P<version>[^'\"]+)['\"]", re.MULTILINE)
IGNORED_DIRECTORIES = {".git", ".pytest_cache", ".venv", "build", "dist", "node_modules", "venv"}


def _release_marker_documents():
    # type: () -> dict
    markers = {}
    for path in ROOT.rglob("*"):
        if path.suffix.lower() not in {".md", ".rst", ".txt"}:
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8")
        if RELEASE_MARKER not in text:
            continue
        match = VERSION_BEFORE_MARKER.search(text)
        assert match is not None, "release marker has no semantic version: {}".format(path.relative_to(ROOT))
        markers[path.relative_to(ROOT).as_posix()] = match.group("version")
    assert markers, "at least one release-marker document must be present"
    return markers


def _release_config():
    # type: () -> dict
    return json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))


def test_all_release_version_surfaces_match_the_manifest():
    # type: () -> None
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))
    manifest_version = manifest["."]
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project_version = tomllib.load(stream)["project"]["version"]
    runtime_text = (ROOT / "src" / "dcc_mcp_3dsmax" / "__version__.py").read_text(encoding="utf-8")
    runtime_match = RUNTIME_VERSION.search(runtime_text)
    assert runtime_match is not None

    observed = {
        "manifest": manifest_version,
        "pyproject.toml": project_version,
        "src/dcc_mcp_3dsmax/__version__.py": runtime_match.group("version"),
    }
    observed.update(_release_marker_documents())
    mismatches = {path: version for path, version in observed.items() if version != manifest_version}

    assert not mismatches, "release version surfaces differ from {}: {}".format(manifest_version, mismatches)


def test_release_please_manages_every_release_version_surface():
    # type: () -> None
    package = _release_config()["packages"]["."]
    extra_files = package["extra-files"]
    generic_paths = {entry["path"] for entry in extra_files if entry.get("type") == "generic"}
    marker_paths = set(_release_marker_documents())
    required_generic_paths = marker_paths | {"src/dcc_mcp_3dsmax/__version__.py"}
    toml_targets = {(entry["path"], entry.get("jsonpath")) for entry in extra_files if entry.get("type") == "toml"}

    assert required_generic_paths <= generic_paths, "unmanaged generic release surfaces: {}".format(
        sorted(required_generic_paths - generic_paths)
    )
    assert ("pyproject.toml", "$.project.version") in toml_targets
