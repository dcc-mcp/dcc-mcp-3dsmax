"""Tests for the MZP package assembler."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "packaging" / "templates"


def _load_assembler():
    module_path = Path(__file__).resolve().parents[1] / "packaging" / "assemble_mzp.py"
    spec = importlib.util.spec_from_file_location("assemble_mzp", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_payload_checker():
    module_path = Path(__file__).resolve().parents[1] / "packaging" / "check_release_payload.py"
    spec = importlib.util.spec_from_file_location("check_release_payload", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metadata(name: str, version: str, requirements=()) -> str:
    return f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + "".join(
        f"Requires-Dist: {item}\n" for item in requirements
    )


def _record_hash(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _generated_install_script(tmp_path: Path, version: str = "1.2.3") -> str:
    assembler = _load_assembler()
    assembler.write_install_script(tmp_path, version)
    return (tmp_path / "install.ms").read_text(encoding="utf-8")


def test_mzp_scripts_are_maintained_as_templates():
    """Long MZP scripts live as source files instead of inline assembler strings."""
    startup = TEMPLATES_DIR / "dcc_mcp_3dsmax_startup.ms"
    install = TEMPLATES_DIR / "install.ms"

    assert startup.is_file()
    assert install.is_file()
    assert "dcc_mcp_3dsmax.main()" in startup.read_text(encoding="utf-8")
    assert "{{VERSION}}" in install.read_text(encoding="utf-8")


def test_mzp_run_is_control_file(tmp_path):
    """mzp.run must contain MZP commands, not the installer MaxScript body."""
    assembler = _load_assembler()

    assembler.write_mzp_run(tmp_path, "1.2.3")

    text = (tmp_path / "mzp.run").read_text(encoding="utf-8")
    assert 'name "dcc-mcp-3dsmax"' in text
    assert 'description "dcc-mcp-3dsmax 1.2.3 drag-and-drop installer"' in text
    assert 'run "install.ms"' in text
    assert 'drop "install.ms"' in text
    assert "clear temp on MAX exit" in text
    assert "python.Execute" not in text
    assert "messageBox" not in text


@pytest.mark.parametrize("version", ["..", "../escape", "1.2", "1.2.3\"\nrun \"evil.ms\""])
def test_assembler_rejects_malicious_release_versions_before_rendering(version):
    assembler = _load_assembler()
    with pytest.raises((ValueError, RuntimeError), match="version|X.Y.Z"):
        assembler._mzp_run_text(version)
    with pytest.raises((ValueError, RuntimeError), match="version|X.Y.Z"):
        assembler.write_install_script(Path("."), version)


def test_assembler_does_not_delete_outside_output_for_parent_version(tmp_path):
    assembler = _load_assembler()
    outside = tmp_path.parent / "dcc-mcp-3dsmax-escape-win64"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError), match="version|X.Y.Z"):
        assembler.assemble(tmp_path / "project", "..", tmp_path / "out")
    assert marker.exists()


def test_install_script_normalizes_paths_before_embedding_in_python(tmp_path):
    """Generated MaxScript avoids raw Python strings ending in backslashes."""
    text = _generated_install_script(tmp_path)
    assert "local sourceRoot = _dccMcpNormalizePath (getFilenamePath (getSourceFileName()))" in text
    assert "local userScripts = _dccMcpNormalizePath (getDir #userScripts)" in text
    assert "local userStartupScripts = _dccMcpNormalizePath (getDir #userStartupScripts)" in text
    assert "source = Path(r'''" in text
    assert "versions_dir = install_dir / 'versions'" in text
    assert "current_file = install_dir / 'current.txt'" in text
    assert "version_name = '1.2.3'" in text
    assert "{{VERSION}}" not in text
    assert "dcc_mcp_3dsmax.install_menu()" in text
    assert "dcc_mcp_3dsmax.install_shutdown_callback()" in text
    assert "dcc_mcp_3dsmax.main()" in text
    assert "capture_bootstrap_errors" in text
    assert "with capture_bootstrap_errors(" in text
    assert "def _cleanup_obsolete_payloads(active_key):" in text
    assert "_cleanup_obsolete_payloads(key)" in text
    assert 'button installBtn "Install"' in text
    assert 'button uninstallBtn "Uninstall"' in text
    assert "dcc_mcp_3dsmax.stop_sidecar_bridge()" in text
    assert "from dcc_mcp_core.install_lifecycle import safe_remove_tree" in text
    assert "sys.modules.pop(name, None)" in text
    assert "installed and runtime startup requested" in text
    assert "dcc-mcp-3dsmax install failed:" in text
    assert "Failed to stop dcc-mcp-3dsmax sidecar before uninstall" in text
    assert "uninstall requires a 3ds Max restart" in text
    assert "dcc-mcp-3dsmax uninstall failed:" in text
    assert "startup_script.unlink()" in text
    assert "uninstall_marker = Path(r'''" in text
    assert ") / 'dcc_mcp_3dsmax_uninstall_pending'" in text
    assert "_SAFE_NAME = re.compile" in text
    assert "st_file_attributes" in text
    assert "refusing" in text


def test_uninstall_script_escapes_pending_marker_newline_for_nested_python(tmp_path):
    """Uninstall marker Python must survive MaxScript string unescaping."""
    text = _generated_install_script(tmp_path)

    assert "uninstall_marker.write_text('pending\\\\n', encoding='utf-8')" in text
    assert "uninstall_marker.write_text('pending\\n', encoding='utf-8')" not in text


def test_startup_script_installs_menu_after_adding_package_path(tmp_path):
    """Restarting 3ds Max after MZP install should restore the DCC MCP menu."""
    assembler = _load_assembler()

    assembler.write_startup_template(tmp_path)

    text = (tmp_path / "startup" / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8")
    assert text == (TEMPLATES_DIR / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8")
    assert "local installRoot = _dccMcpNormalizePath" in text
    assert "DCC_MCP_3DSMAX_BOOTSTRAP_PATHS" in text
    assert "DCC_MCP_3DSMAX_ROOT" in text
    assert "DCC_MCP_CORE_ROOT" in text
    assert "DCC_MCP_SERVER_ROOT" in text
    assert "current = current_file.read_text" in text
    assert "versions_dir / current" in text
    assert "def _is_python37_root(path):" in text
    assert "base.parent / 'python'" in text
    assert "def _compatible_python_path(path):" in text
    assert "sys.path.insert(0, str(pkg))" in text
    assert "dcc_mcp_3dsmax.install_menu()" in text
    assert "dcc_mcp_3dsmax.install_shutdown_callback()" in text
    assert "dcc_mcp_3dsmax.main()" in text
    assert "capture_bootstrap_errors" in text
    assert "phase='mzp_startup'" in text
    assert "DCC_MCP_3DSMAX_PORT" not in text
    assert "DCC_MCP_GATEWAY_PORT" not in text
    assert "def _cleanup_obsolete_payloads(active_root):" in text
    assert "_cleanup_obsolete_payloads(install_payload)" in text
    assert "from dcc_mcp_core.install_lifecycle import safe_remove_tree" in text
    assert "_SAFE_NAME = re.compile" in text
    assert "st_file_attributes" in text
    assert "refusing" in text


@pytest.mark.parametrize("current", ["..", ".", "../outside", "C:/outside", "foo\\\\bar", "CON"])
def test_generated_runtime_rejects_unsafe_current_names(tmp_path, current):
    """The embedded runtime must fail closed for traversal, absolute, and reserved names."""
    assembler = _load_assembler()
    install = _generated_install_script(tmp_path)
    startup_path = tmp_path / "startup"
    assembler.write_startup_template(tmp_path)
    startup = (startup_path / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8")
    for text in (install, startup):
        assert "_SAFE_NAME.fullmatch(current or '')" in text
        assert "current.rstrip(' .') != current" in text
        assert "current.split('.')[0].upper() in _RESERVED_NAMES" in text
        assert current not in ("C:/outside",) or "os.path.realpath" in text


def test_generated_runtime_rebinds_identity_around_cleanup_and_import(tmp_path):
    assembler = _load_assembler()
    install = _generated_install_script(tmp_path)
    assembler.write_startup_template(tmp_path)
    startup = (tmp_path / "startup" / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8")
    for text in (install, startup):
        assert "def _identity(path):" in text
        assert "identity changed" in text
        assert "st_file_attributes" in text
        assert "os.path.lexists(str(path))" in text
        assert "cleanup target reappeared" in text
        assert "runtime package" in text
    assert "os.replace(str(current_tmp), str(current_file))" in install


def test_release_payload_checker_uses_modern_python_root_on_py38_plus(tmp_path, monkeypatch):
    """Release smoke must not import cp37 native payloads on modern 3ds Max Python."""
    checker = _load_payload_checker()
    payload = tmp_path / "payload"
    (payload / "python").mkdir(parents=True)
    (payload / "python37").mkdir()

    monkeypatch.setattr(checker.sys, "version_info", (3, 10, 14))

    assert checker.python_root(payload) == payload / "python"


def test_extract_wheel_maps_data_scripts_next_to_packages(tmp_path):
    """Wheel .data/scripts entries must land where dcc_mcp_server can find them."""
    assembler = _load_assembler()
    wheel = tmp_path / "dcc_mcp_server-0.17.56-py3-none-win_amd64.whl"
    dest = tmp_path / "python"

    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("dcc_mcp_server/__init__.py", "__version__ = '0.17.56'\n")
        zf.writestr("dcc_mcp_server-0.17.56.data/scripts/dcc-mcp-server.exe", b"binary")
        zf.writestr(
            "dcc_mcp_server-0.17.56.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: dcc-mcp-server\nVersion: 0.17.56\n",
        )

    assembler.extract_wheel(wheel, dest)

    assert (dest / "dcc_mcp_server" / "__init__.py").is_file()
    assert (dest / "scripts" / "dcc-mcp-server.exe").read_bytes() == b"binary"
    assert not (dest / "dcc_mcp_server-0.17.56.data").exists()
    assert (dest / "dcc_mcp_server-0.17.56.dist-info" / "METADATA").is_file()


def test_extract_extension_lane_preserves_selected_distribution_metadata(tmp_path):
    assembler = _load_assembler()
    wheel = tmp_path / "dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl"
    dest = tmp_path / "python37"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("dcc_mcp_core/_core.pyd", b"cp37")
        zf.writestr(
            "dcc_mcp_core-0.20.22.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: dcc-mcp-core\nVersion: 0.20.22\n",
        )

    assembler.extract_wheel(wheel, dest, extensions_only=True)

    assert (dest / "dcc_mcp_core" / "_core.pyd").read_bytes() == b"cp37"
    assert (dest / "dcc_mcp_core-0.20.22.dist-info" / "METADATA").is_file()


def test_wheel_metadata_path_must_match_distribution_and_version(tmp_path):
    assembler = _load_assembler()
    wheel = tmp_path / "dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("foreign_project-9.9.dist-info/METADATA", _metadata("dcc-mcp-core", "0.20.22"))

    with pytest.raises(RuntimeError, match="metadata|identity|canonical"):
        assembler._verified_wheel_metadata(wheel, "dcc-mcp-core", "0.20.22")


def test_wheel_extraction_rejects_parent_path_escape_before_write(tmp_path):
    assembler = _load_assembler()
    wheel = tmp_path / "payload.whl"
    destination = tmp_path / "runtime"
    escaped = tmp_path / "escaped.py"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../escaped.py", b"foreign write")

    with pytest.raises(RuntimeError, match="path|archive|unsafe"):
        assembler.extract_wheel(wheel, destination)

    assert not escaped.exists()


def test_wheel_extraction_rejects_duplicate_members(tmp_path):
    assembler = _load_assembler()
    wheel = tmp_path / "payload.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("dcc_mcp_core/__init__.py", b"first")
        archive.writestr("dcc_mcp_core/__init__.py", b"replacement")

    with pytest.raises(RuntimeError, match="duplicate|archive"):
        assembler.extract_wheel(wheel, tmp_path / "runtime")


def test_runtime_contract_rejects_tampered_record_payload(tmp_path):
    assembler = _load_assembler()
    requirements = ("dcc-mcp-server>=0.20.22,<1.0.0",)
    metadata_path = "dcc_mcp_core-0.20.22.dist-info/METADATA"
    core_metadata = _metadata("dcc-mcp-core", "0.20.22", requirements)
    trusted_digest = _record_hash(b"trusted bytes")
    bad_record = "dcc_mcp_core/__init__.py,sha256=" + trusted_digest + ",13\n"
    wheels = []
    for filename, payload in (
        ("dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl", b"tampered bytes"),
        ("dcc_mcp_core-0.20.22-cp38-abi3-win_amd64.whl", b"trusted bytes"),
    ):
        wheel = tmp_path / filename
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(metadata_path, core_metadata)
            archive.writestr("dcc_mcp_core/__init__.py", payload)
            archive.writestr("dcc_mcp_core-0.20.22.dist-info/RECORD", bad_record)
        wheels.append(wheel)
    server = tmp_path / "dcc_mcp_server-0.20.22-py3-none-win_amd64.whl"
    with zipfile.ZipFile(server, "w") as archive:
        server_metadata_path = "dcc_mcp_server-0.20.22.dist-info/METADATA"
        server_metadata = _metadata("dcc-mcp-server", "0.20.22")
        archive.writestr(server_metadata_path, server_metadata)
        archive.writestr(
            "dcc_mcp_server-0.20.22.dist-info/RECORD",
            f"{server_metadata_path},sha256={_record_hash(server_metadata.encode())},{len(server_metadata.encode())}\n"
            "dcc_mcp_server-0.20.22.dist-info/RECORD,,\n",
        )

    with pytest.raises(RuntimeError, match="RECORD|hash|tamper|integrity"):
        assembler.verify_python37_runtime_contract(
            wheels,
            server,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )


def test_release_payload_rejects_duplicate_archive_members_before_extract(tmp_path):
    checker = _load_payload_checker()
    mzp = tmp_path / "dcc-mcp-3dsmax-0.2.2-win64.mzp"
    with zipfile.ZipFile(mzp, "w") as archive:
        archive.writestr("payload/python/action.py", b"first")
        archive.writestr("payload/python/action.py", b"replacement")

    with pytest.raises(RuntimeError, match="duplicate|archive"):
        checker.payload_root(mzp)


def _minimal_mzp(assembler, tmp_path: Path, version: str = "0.2.2") -> Path:
    root = tmp_path / (f"dcc-mcp-3dsmax-{version}-win64")
    payload = root / "payload"
    for lane in ("python", "python37"):
        lane_root = payload / lane / "dcc_mcp_3dsmax"
        lane_root.mkdir(parents=True)
        (lane_root / "__version__.py").write_text(f"__version__ = '{version}'\n", encoding="utf-8")
    assembler.write_runtime_manifests(payload)
    (payload / "README.txt").write_bytes((ROOT / "packaging" / "README.txt").read_bytes())
    assembler.write_startup_template(payload)
    assembler.write_mzp_run(root, version)
    assembler.write_install_script(root, version)
    archive = tmp_path / f"dcc-mcp-3dsmax-{version}-win64.mzp"
    zip_base = archive.with_suffix("")
    import shutil

    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=root)
    Path(zip_path).rename(archive)
    return archive


def test_mzp_control_plane_rejects_redirect_and_extra_executable_member(tmp_path):
    assembler = _load_assembler()
    source = _minimal_mzp(assembler, tmp_path)
    tampered = tmp_path / "tampered.mzp"
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(tampered, "w") as outgoing:
        for info in incoming.infolist():
            data = incoming.read(info)
            if info.filename == "mzp.run":
                data = data.replace(b'run "install.ms"', b'run "review-tamper.ms"')
            outgoing.writestr(info, data)
        outgoing.writestr("review-tamper.ms", b'python.Execute "foreign"\n')

    with zipfile.ZipFile(tampered) as archive:
        with pytest.raises(RuntimeError, match="control|allowlist|mzp.run"):
            assembler._verify_mzp_archive_integrity(archive, tampered.name, expected_version="0.2.2")


def test_release_checker_rejects_runtime_bytes_before_extract(tmp_path):
    assembler = _load_assembler()
    checker = _load_payload_checker()
    source = _minimal_mzp(assembler, tmp_path)
    tampered = tmp_path / "runtime-tampered.mzp"
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(tampered, "w") as outgoing:
        for info in incoming.infolist():
            data = incoming.read(info)
            if info.filename == "payload/python/dcc_mcp_3dsmax/__version__.py":
                data = b"__version__ = 'foreign'\n"
            outgoing.writestr(info, data)

    with pytest.raises(RuntimeError, match="manifest|integrity"):
        checker.payload_root(tampered, expected_version="0.2.2")


def test_runtime_manifest_rejects_post_assembly_payload_tamper(tmp_path):
    assembler = _load_assembler()
    checker = _load_payload_checker()
    payload = tmp_path / "payload"
    for lane in ("python", "python37"):
        root = payload / lane
        root.mkdir(parents=True)
        (root / "action.py").write_bytes(b"trusted")
    assembler.write_runtime_manifests(payload)
    (payload / "python" / "action.py").write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="manifest|integrity|hash"):
        checker.verify_runtime_manifests(payload)


def _write_distribution_metadata(root, distribution, version, *, suffix=""):
    normalized = distribution.replace("-", "_")
    metadata = root / f"{normalized}-{version}{suffix}.dist-info" / "METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n",
        encoding="utf-8",
    )
    return metadata


def test_release_payload_checker_executes_strict_runtime_version_contract(tmp_path):
    checker = _load_payload_checker()
    _write_distribution_metadata(tmp_path, "dcc-mcp-core", "0.20.22")
    _write_distribution_metadata(tmp_path, "dcc-mcp-server", "0.20.22")

    checker.verify_runtime_dependency_versions(tmp_path)

    core_metadata = next(tmp_path.glob("dcc_mcp_core-*.dist-info/METADATA"))
    core_metadata.write_text(
        "Metadata-Version: 2.1\nName: dcc-mcp-core\nVersion: 0.20.22rc1\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="invalid dcc-mcp-core version"):
        checker.verify_runtime_dependency_versions(tmp_path)


def test_release_payload_checker_rejects_missing_or_ambiguous_runtime_metadata(tmp_path):
    checker = _load_payload_checker()
    _write_distribution_metadata(tmp_path, "dcc-mcp-core", "0.20.22")
    _write_distribution_metadata(tmp_path, "dcc-mcp-server", "0.20.22")
    _write_distribution_metadata(tmp_path, "dcc-mcp-core", "0.20.22", suffix=".duplicate")

    with pytest.raises(RuntimeError, match="ambiguous dcc-mcp-core metadata"):
        checker.verify_runtime_dependency_versions(tmp_path)

    for metadata in tmp_path.glob("dcc_mcp_core-*.duplicate.dist-info/METADATA"):
        metadata.unlink()
    for metadata in tmp_path.glob("dcc_mcp_server-*.dist-info/METADATA"):
        metadata.unlink()
    with pytest.raises(RuntimeError, match="ambiguous dcc-mcp-server metadata"):
        checker.verify_runtime_dependency_versions(tmp_path)


def test_all_runtime_guards_read_canonical_distribution_metadata():
    from dcc_mcp_3dsmax import install_cli as cli

    rendered = cli.render_startup_script(ROOT / ".tmp-bootstrap-errors")
    guards = [
        (TEMPLATES_DIR / "install.ms").read_text(encoding="utf-8"),
        (TEMPLATES_DIR / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8"),
        rendered,
    ]
    for guard in guards:
        assert "*.dist-info/METADATA" in guard
        assert "from email.parser import Parser" in guard
        assert "getattr(dcc_mcp_core, '__version__'" not in guard
        assert "re.fullmatch('[0-9]+(?:[.][0-9]+)*', value)" in guard


def _write_wheel(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _metadata_wheel(path, name, requirements=(), version="0.20.22", extra_files=None):
    metadata = f"Metadata-Version: 2.1\nName: {name}\n"
    if version is not None:
        metadata += f"Version: {version}\n"
    metadata += "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
    dist_info_root = name.replace("-", "_") + f"-{version or 'unknown'}.dist-info"
    metadata_path = dist_info_root + "/METADATA"
    record_path = dist_info_root + "/RECORD"
    files = {metadata_path: metadata}
    files.update(extra_files or {})
    rows = []
    for member, value in files.items():
        data = value.encode("utf-8") if isinstance(value, str) else value
        rows.append(f"{member},sha256={_record_hash(data)},{len(data)}")
    files[record_path] = "\n".join(rows + [f"{record_path},,"]) + "\n"
    _write_wheel(path, files)


def _runtime_wheels(tmp_path, *, core_requirements=None, abi3_requirements=None, server_requirements=()):
    core_requirements = core_requirements or (
        "dcc-mcp-server>=0.18.17,<1.0.0",
        "typing-extensions==4.7.1 ; python_full_version < '3.8' and extra == 'test'",
    )
    cp37 = tmp_path / "dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl"
    abi3 = tmp_path / "dcc_mcp_core-0.20.22-cp38-abi3-win_amd64.whl"
    server = tmp_path / "dcc_mcp_server-0.20.22-py3-none-win_amd64.whl"
    _metadata_wheel(cp37, "dcc-mcp-core", core_requirements, extra_files={"dcc_mcp_core/_core.pyd": b"cp37"})
    _metadata_wheel(
        abi3,
        "dcc-mcp-core",
        abi3_requirements or core_requirements,
        extra_files={"dcc_mcp_core/__init__.py": "__version__ = '0.20.22'\n", "dcc_mcp_core/_core.pyd": b"abi3"},
    )
    _metadata_wheel(
        server,
        "dcc-mcp-server",
        server_requirements,
        extra_files={"dcc_mcp_server/__init__.py": "__version__ = '0.20.22'\n"},
    )
    return [cp37, abi3], server


def test_python37_runtime_accepts_public_zero_dependency_core(tmp_path):
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(tmp_path)

    assembler.verify_python37_runtime_contract(
        core_wheels,
        server_wheel,
        expected_core_version="0.20.22",
        expected_server_version="0.20.22",
    )


def test_runtime_contract_accepts_applicable_marker_on_required_server_dependency(tmp_path):
    assembler = _load_assembler()
    marked_requirement = "dcc-mcp-server>=0.20.22,<1.0.0 ; python_version >= '3.7'"
    core_wheels, server_wheel = _runtime_wheels(
        tmp_path,
        core_requirements=(marked_requirement,),
    )

    assembler.verify_python37_runtime_contract(
        core_wheels,
        server_wheel,
        expected_core_version="0.20.22",
        expected_server_version="0.20.22",
    )


def test_python37_marker_environment_cannot_inherit_the_build_interpreter_version(tmp_path):
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(
        tmp_path,
        core_requirements=(
            "dcc-mcp-server>=0.20.22,<1.0.0",
            "typing-extensions==4.7.1 ; implementation_version < '3.8'",
        ),
    )

    with pytest.raises(RuntimeError, match="unclosed|typing"):
        assembler.verify_python37_runtime_contract(
            core_wheels,
            server_wheel,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )


def test_runtime_contract_accepts_standards_valid_pep440_exclusions(tmp_path):
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(
        tmp_path,
        core_requirements=("dcc-mcp-server>=0.20.22,!=0.20.23.post1,<1.0.0",),
    )

    assembler.verify_python37_runtime_contract(
        core_wheels,
        server_wheel,
        expected_core_version="0.20.22",
        expected_server_version="0.20.22",
    )


def test_python37_runtime_rejects_default_dependency_hidden_by_extra_or(tmp_path):
    """An extra marker must not hide a dependency that also applies to default Python 3.7."""
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(
        tmp_path,
        core_requirements=(
            "dcc-mcp-server>=0.20.22,<1.0.0",
            "typing-extensions==4.7.1 ; python_full_version < '3.8' or extra == 'test'",
        ),
    )

    with pytest.raises(RuntimeError, match="unclosed|dependency drift"):
        assembler.verify_python37_runtime_contract(
            core_wheels,
            server_wheel,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )


@pytest.mark.parametrize(
    "extra_requirement",
    [
        "typing-extensions==4.7.1 ; python_full_version < '3.8'",
        "another-runtime-dependency>=1",
    ],
)
def test_python37_runtime_rejects_adapter_backfill_and_unclosed_dependencies(tmp_path, extra_requirement):
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(
        tmp_path,
        core_requirements=("dcc-mcp-server>=0.18.17,<1.0.0", extra_requirement),
    )

    with pytest.raises(RuntimeError, match="unclosed dcc-mcp-core"):
        assembler.verify_python37_runtime_contract(
            core_wheels,
            server_wheel,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )


def test_python37_runtime_rejects_server_dependencies_and_wrong_identity(tmp_path):
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(tmp_path, server_requirements=("unexpected>=1",))

    with pytest.raises(RuntimeError, match="unclosed dcc-mcp-server"):
        assembler.verify_python37_runtime_contract(
            core_wheels,
            server_wheel,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )

    _metadata_wheel(server_wheel, "not-dcc-mcp-server")
    with pytest.raises(RuntimeError, match="identity"):
        assembler.verify_python37_runtime_contract(
            core_wheels,
            server_wheel,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )


def test_python37_runtime_requires_exact_two_core_lanes_and_versions(tmp_path):
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(tmp_path)

    with pytest.raises(RuntimeError, match="cp37"):
        assembler.verify_python37_runtime_contract(
            core_wheels[1:],
            server_wheel,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )


def test_runtime_contract_rejects_default_dependency_drift_between_core_lanes(tmp_path):
    assembler = _load_assembler()
    core_wheels, server_wheel = _runtime_wheels(
        tmp_path,
        abi3_requirements=("dcc-mcp-server>=0.18.17,<1.0.0", "modern-only-default>=1"),
    )

    with pytest.raises(RuntimeError, match="Core wheel runtime dependency drift"):
        assembler.verify_python37_runtime_contract(
            core_wheels,
            server_wheel,
            expected_core_version="0.20.22",
            expected_server_version="0.20.22",
        )

    with pytest.raises(RuntimeError, match="version"):
        assembler.verify_python37_runtime_contract(
            core_wheels,
            server_wheel,
            expected_core_version="0.20.23",
            expected_server_version="0.20.22",
        )


@pytest.mark.parametrize(
    "path",
    [
        "typing_extensions.py",
        "Typing-Extensions/__init__.py",
        "typing.extensions-4.7.1.dist-info/METADATA",
        "nested/typing_extensions.pyc",
    ],
)
def test_payload_policy_rejects_typing_extensions_aliases(path):
    assembler = _load_assembler()

    with pytest.raises(RuntimeError, match="typing_extensions"):
        assembler.assert_no_typing_extensions([path], "test payload")


def test_adapter_has_no_runtime_backfill_lock_and_requires_fixed_public_core():
    assembler = _load_assembler()
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert not (ROOT / "packaging" / "mzp-runtime.lock.json").exists()
    assert assembler.resolve_dependency_minimum(ROOT, "dcc-mcp-core") >= "0.20.22"
    assert "typing-extensions" not in project.casefold()


def test_dependency_resolver_honors_the_complete_declared_range(tmp_path):
    assembler = _load_assembler()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["dcc-mcp-core>=0.20.22,<1.0.0"]\n',
        encoding="utf-8",
    )
    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {
            "info": {"version": "1.0.0"},
            "releases": {
                "0.20.22": [
                    {
                        "filename": "dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl",
                        "packagetype": "bdist_wheel",
                    },
                    {
                        "filename": "dcc_mcp_core-0.20.22-cp38-abi3-win_amd64.whl",
                        "packagetype": "bdist_wheel",
                    },
                ],
                "1.0.0": [
                    {
                        "filename": "dcc_mcp_core-1.0.0-cp37-cp37m-win_amd64.whl",
                        "packagetype": "bdist_wheel",
                    },
                    {
                        "filename": "dcc_mcp_core-1.0.0-cp38-abi3-win_amd64.whl",
                        "packagetype": "bdist_wheel",
                    },
                ],
            },
        }
    ).encode("utf-8")
    response.__enter__.return_value = response

    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        assert assembler.resolve_dependency_version(tmp_path, "dcc-mcp-core") == "0.20.22"


def test_selected_pep440_post_release_satisfies_server_specifier():
    assembler = _load_assembler()
    from packaging.specifiers import SpecifierSet

    assert assembler._satisfies_release_specifier("0.20.22.post1", SpecifierSet(">=0.20.22,<1.0.0"))


def test_dependency_resolver_selects_a_pep440_post_release(tmp_path):
    assembler = _load_assembler()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["dcc-mcp-server>=0.20.22,<1.0.0"]\n', encoding="utf-8"
    )
    filename = "dcc_mcp_server-0.20.22.post1-py3-none-win_amd64.whl"
    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {
            "releases": {
                "0.20.22.post1": [
                    {
                        "filename": filename,
                        "packagetype": "bdist_wheel",
                        "yanked": False,
                    }
                ]
            }
        }
    ).encode("utf-8")
    response.__enter__.return_value = response
    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        assert assembler.resolve_dependency_version(tmp_path, "dcc-mcp-server") == "0.20.22.post1"


def test_dependency_resolver_fails_closed_without_a_matching_release(tmp_path):
    assembler = _load_assembler()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["dcc-mcp-server>=0.20.22,<1.0.0"]\n',
        encoding="utf-8",
    )
    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {"info": {"version": "1.0.0"}, "releases": {"1.0.0": [{"packagetype": "bdist_wheel"}]}}
    ).encode("utf-8")
    response.__enter__.return_value = response

    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        with pytest.raises(RuntimeError, match="no published version.*declared range"):
            assembler.resolve_dependency_version(tmp_path, "dcc-mcp-server")


def test_dependency_resolver_rejects_a_release_with_yanked_required_windows_wheels(tmp_path):
    assembler = _load_assembler()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["dcc-mcp-core>=0.20.22,<1.0.0"]\n',
        encoding="utf-8",
    )
    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {
            "releases": {
                "0.20.22": [
                    {
                        "filename": "dcc_mcp_core-0.20.22-cp312-cp312-manylinux_x86_64.whl",
                        "packagetype": "bdist_wheel",
                        "yanked": False,
                    },
                    {
                        "filename": "dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl",
                        "packagetype": "bdist_wheel",
                        "yanked": True,
                    },
                    {
                        "filename": "dcc_mcp_core-0.20.22-cp38-abi3-win_amd64.whl",
                        "packagetype": "bdist_wheel",
                        "yanked": True,
                    },
                ]
            }
        }
    ).encode("utf-8")
    response.__enter__.return_value = response

    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        with pytest.raises(RuntimeError, match="no published version.*declared range"):
            assembler.resolve_dependency_version(tmp_path, "dcc-mcp-core")


@pytest.mark.parametrize(
    ("distribution", "dependency", "required", "decoys"),
    [
        (
            "dcc-mcp-core",
            "dcc-mcp-core>=0.20.22,<1.0.0",
            [
                "dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl",
                "dcc_mcp_core-0.20.22-cp38-abi3-win_amd64.whl",
            ],
            [
                "attacker_core-0.20.22-cp37-cp37m-win_amd64.whl",
                "attacker_core-0.20.22-cp38-abi3-win_amd64.whl",
            ],
        ),
        (
            "dcc-mcp-server",
            "dcc-mcp-server>=0.20.22,<1.0.0",
            ["dcc_mcp_server-0.20.22-py3-none-win_amd64.whl"],
            ["attacker_server-0.20.22-py3-none-win_amd64.whl"],
        ),
    ],
)
def test_dependency_resolver_rejects_unrelated_wheels_with_required_tags(
    tmp_path, distribution, dependency, required, decoys
):
    assembler = _load_assembler()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["%s"]\n' % dependency,
        encoding="utf-8",
    )
    files = [{"filename": filename, "packagetype": "bdist_wheel", "yanked": True} for filename in required] + [
        {"filename": filename, "packagetype": "bdist_wheel", "yanked": False} for filename in decoys
    ]
    response = mock.MagicMock()
    response.read.return_value = json.dumps({"releases": {"0.20.22": files}}).encode("utf-8")
    response.__enter__.return_value = response

    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        with pytest.raises(RuntimeError, match="no published version.*declared range"):
            assembler.resolve_dependency_version(tmp_path, distribution)


@pytest.mark.parametrize(
    ("downloader", "filename"),
    [
        ("download_core_wheels", "dcc_mcp_core-0.20.22-cp37-cp37m-win_amd64.whl"),
        ("download_server_wheel", "dcc_mcp_server-0.20.22-py3-none-win_amd64.whl"),
    ],
)
def test_dependency_downloaders_never_consume_yanked_target_wheels(tmp_path, downloader, filename):
    assembler = _load_assembler()
    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {
            "urls": [
                {
                    "filename": filename,
                    "packagetype": "bdist_wheel",
                    "url": "https://example.invalid/yanked.whl",
                    "yanked": True,
                }
            ]
        }
    ).encode("utf-8")
    response.__enter__.return_value = response

    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        with mock.patch.object(assembler.urllib.request, "urlretrieve") as retrieve:
            with pytest.raises(RuntimeError, match="Expected one"):
                getattr(assembler, downloader)("0.20.22", tmp_path)
    retrieve.assert_not_called()


@pytest.mark.parametrize(
    ("downloader", "filenames"),
    [
        (
            "download_core_wheels",
            [
                "attacker_core-0.20.22-cp37-cp37m-win_amd64.whl",
                "attacker_core-0.20.22-cp38-abi3-win_amd64.whl",
            ],
        ),
        ("download_server_wheel", ["attacker_server-0.20.22-py3-none-win_amd64.whl"]),
    ],
)
def test_dependency_downloaders_reject_unrelated_wheels_with_required_tags(tmp_path, downloader, filenames):
    assembler = _load_assembler()
    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {
            "urls": [
                {
                    "filename": filename,
                    "packagetype": "bdist_wheel",
                    "url": "https://example.invalid/%s" % filename,
                    "yanked": False,
                }
                for filename in filenames
            ]
        }
    ).encode("utf-8")
    response.__enter__.return_value = response

    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        with mock.patch.object(assembler.urllib.request, "urlretrieve") as retrieve:
            with pytest.raises(RuntimeError, match="Expected one"):
                getattr(assembler, downloader)("0.20.22", tmp_path)
    retrieve.assert_not_called()


def test_dependency_downloader_binds_downloaded_bytes_to_pypi_sha256(tmp_path):
    assembler = _load_assembler()
    filename = "dcc_mcp_server-0.20.22-py3-none-win_amd64.whl"
    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {
            "urls": [
                {
                    "filename": filename,
                    "packagetype": "bdist_wheel",
                    "url": "https://example.invalid/server.whl",
                    "yanked": False,
                    "digests": {"sha256": hashlib.sha256(b"trusted wheel").hexdigest()},
                }
            ]
        }
    ).encode("utf-8")
    response.__enter__.return_value = response

    with mock.patch.object(assembler.urllib.request, "urlopen", return_value=response):
        with mock.patch.object(
            assembler.urllib.request,
            "urlretrieve",
            side_effect=lambda _url, target: Path(target).write_bytes(b"tampered wheel"),
        ):
            with pytest.raises(RuntimeError, match="digest|sha256|integrity"):
                assembler.download_server_wheel("0.20.22", tmp_path)


def test_release_payload_rejects_archive_install_and_adapter_version_drift(tmp_path):
    checker = _load_payload_checker()
    package_root = tmp_path / "dcc-mcp-3dsmax-0.2.2-win64"
    payload = package_root / "payload"
    for lane in ("python", "python37"):
        package = payload / lane / "dcc_mcp_3dsmax"
        package.mkdir(parents=True)
        (package / "__version__.py").write_text('__version__ = "0.2.2"\n', encoding="utf-8")
    (package_root / "install.ms").write_text("py += \"version_name = '0.1.0'\\n\"\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="release version mismatch"):
        checker.verify_release_versions(payload, package_root, "0.2.2")


def test_release_workflow_binds_manual_tag_checkout_and_payload_version():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "resolve-release-ref:" in text
    assert "release_commit:" in text
    assert "release_tag:" in text
    assert "needs.resolve-release-ref.outputs.release_commit" in text
    assert text.count("ref: ${{ needs.resolve-release-ref.outputs.release_commit }}") == 4
    assert "softprops/action-gh-release" not in text
    assert text.count("python packaging/upload_release_assets.py") == 2
    assert "--expected-version $env:MZP_VERSION" in text


@pytest.mark.parametrize("workflow_name", ["ci.yml", "release.yml"])
def test_mzp_workflows_install_the_assembler_packaging_dependency_first(workflow_name):
    text = (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")

    install_index = text.index('python -m pip install "packaging>=21.3"')
    assemble_index = text.index("python packaging/assemble_mzp.py")
    assert install_index < assemble_index


@pytest.mark.parametrize("workflow_name", ["ci.yml", "release.yml"])
def test_mzp_workflows_resolve_version_before_switching_to_python37(workflow_name):
    text = (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
    python37_index = text.index('python-version: "3.7"', text.index("build-mzp:"))

    assert 'Add-Content -Path $env:GITHUB_ENV -Value "MZP_VERSION=$VERSION"' in text[:python37_index]
    assert "$env:MZP_VERSION" in text[python37_index:]
    assert "import tomllib" not in text[python37_index:]


def test_runtime_and_bootstrap_dependency_range_matches_project_metadata():
    from dcc_mcp_3dsmax import install_cli as cli

    install_template = (TEMPLATES_DIR / "install.ms").read_text(encoding="utf-8")
    startup_template = (TEMPLATES_DIR / "dcc_mcp_3dsmax_startup.ms").read_text(encoding="utf-8")

    assert cli.MIN_CORE_VERSION == "0.20.22"
    assert cli.MIN_SERVER_VERSION == "0.20.22"
    assert cli.MAX_CORE_VERSION == "1.0.0"
    assert cli.MAX_SERVER_VERSION == "1.0.0"
    for template in (install_template, startup_template):
        assert "min_core_version='0.20.22'" in template
        assert "min_server_version = '0.20.22'" in template
        assert "max_server_version = '1.0.0'" in template


@pytest.mark.parametrize(
    ("compatibility", "reason"),
    [
        (
            {
                "python_version": "3.7.9",
                "host_version": "2022",
                "core_version": "0.20.21",
                "server_version": "0.20.22",
            },
            "core_version_too_old",
        ),
        (
            {
                "python_version": "3.7.9",
                "host_version": "2022",
                "core_version": "0.20.22",
                "server_version": "0.20.21",
            },
            "server_version_too_old",
        ),
        (
            {
                "python_version": "3.7.9",
                "host_version": "2022",
                "core_version": "0.20.22",
                "server_version": "1.0.0",
            },
            "server_version_unsupported",
        ),
    ],
)
def test_lifecycle_rejects_core_and_server_versions_outside_the_release_range(compatibility, reason):
    from dcc_mcp_3dsmax import install_cli as cli

    with pytest.raises(cli.LifecycleError, match=reason) as captured:
        cli._validate_compatibility(compatibility)
    assert captured.value.reason == reason


def test_release_payload_rejects_adapter_local_typing_extensions(tmp_path):
    checker = _load_payload_checker()
    payload = tmp_path / "payload"
    (payload / "python" / "typing_extensions").mkdir(parents=True)
    (payload / "python" / "typing_extensions" / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="typing_extensions"):
        checker.reject_adapter_runtime_backfills(payload)


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_workflow_cannot_upload_mzp_before_real_python37_registration(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    build_mzp = text.index("build-mzp:")
    upload = text.index("Upload MZP artifact", build_mzp)
    modern = text.index("Smoke-check MZP payload imports", build_mzp)
    python37_setup = text.index('python-version: "3.7"', modern)
    python37_check = text.index("Smoke-check MZP payload with Python 3.7", python37_setup)

    assert modern < python37_setup < python37_check < upload
