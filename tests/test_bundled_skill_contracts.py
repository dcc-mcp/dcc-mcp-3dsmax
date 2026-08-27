"""Contract tests for bundled 3ds Max skills."""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "src" / "dcc_mcp_3dsmax" / "skills"
INDEX_DOC = ROOT / "docs" / "BUNDLED_SKILLS.md"

REQUIRED_ANNOTATIONS = {
    "read_only_hint",
    "destructive_hint",
    "idempotent_hint",
    "open_world_hint",
}


def _skill_dirs():
    return sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir())


def _frontmatter(skill_dir: Path) -> dict:
    raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert raw.startswith("---\n"), skill_dir
    return yaml.safe_load(raw.split("---", 2)[1])


def _tools(skill_dir: Path) -> list[dict]:
    data = yaml.safe_load((skill_dir / "tools.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict), skill_dir
    tools = data.get("tools")
    assert isinstance(tools, list) and tools, skill_dir
    return tools


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _assert_object_schema(schema: dict, *, field: str, tool_name: str) -> None:
    assert isinstance(schema, dict), (tool_name, field)
    assert schema.get("type") == "object", (tool_name, field)
    assert "properties" in schema, (tool_name, field)
    assert isinstance(schema["properties"], dict), (tool_name, field)


def _exported_tool_name(skill_name: str, tool_name: str) -> str:
    return "{}__{}".format(skill_name, tool_name)


def test_bundled_skill_metadata_and_tools_are_ci_validatable():
    """Bundled skills expose modern metadata and tool contracts without 3ds Max."""
    skill_dirs = _skill_dirs()
    assert skill_dirs, SKILLS_DIR

    for skill_dir in skill_dirs:
        metadata = _frontmatter(skill_dir)
        dcc_mcp = metadata["metadata"]["dcc-mcp"]
        assert dcc_mcp["dcc"] == "3dsmax"
        assert dcc_mcp["layer"] == "domain"
        assert dcc_mcp["stage"] in {"scene", "authoring"}
        assert dcc_mcp["tools"] == "tools.yaml"
        assert isinstance(dcc_mcp.get("search-hint"), str) and dcc_mcp["search-hint"].strip()
        assert isinstance(dcc_mcp.get("tags"), str) and dcc_mcp["tags"].strip()

        for tool in _tools(skill_dir):
            tool_name = tool.get("name")
            assert isinstance(tool_name, str) and tool_name.strip(), skill_dir
            assert isinstance(tool.get("description"), str) and tool["description"].strip(), tool_name
            assert tool.get("execution") in {"sync", "async"}, tool_name
            assert tool.get("affinity") == "main", tool_name
            assert isinstance(tool.get("timeout_hint_secs"), int) and tool["timeout_hint_secs"] > 0, tool_name

            source_file = tool.get("source_file")
            assert isinstance(source_file, str) and source_file.endswith(".py"), tool_name
            source_path = skill_dir / source_file
            assert source_path.is_file(), (tool_name, source_file)
            assert _is_relative_to(source_path, skill_dir), (tool_name, source_file)

            _assert_object_schema(tool.get("input_schema"), field="input_schema", tool_name=tool_name)
            _assert_object_schema(tool.get("output_schema"), field="output_schema", tool_name=tool_name)

            for flag in ("read_only", "destructive", "idempotent"):
                assert isinstance(tool.get(flag), bool), (tool_name, flag)
            annotations = tool.get("annotations")
            assert isinstance(annotations, dict), tool_name
            assert REQUIRED_ANNOTATIONS.issubset(annotations), tool_name
            assert annotations["read_only_hint"] is tool["read_only"], tool_name
            assert annotations["destructive_hint"] is tool["destructive"], tool_name
            assert annotations["idempotent_hint"] is tool["idempotent"], tool_name
            assert isinstance(annotations["open_world_hint"], bool), tool_name


def test_asset_source_loads_one_registered_handler_through_the_real_core_loader():
    """The minimum supported Core must accept the shipped asset-source manifest."""
    from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions

    server = MaxMcpServer(
        options=MaxServerOptions(
            port=0,
            enable_gateway_failover=False,
            job_storage_path="",
        )
    )
    try:
        server.register_builtin_actions(include_bundled=False)
        before = server._server.registry.count_actions()
        loaded = server._server.load_skill("3dsmax-asset-source")
        action_name = "3dsmax_asset_source__search_assets"

        assert loaded == [action_name]
        assert server._server.registry.count_actions() == before + 1
        assert server._server.registry.get_action(action_name) is not None
        assert server._server.has_handler(action_name) is True
    finally:
        server.stop()


def test_public_bundled_skill_index_lists_every_exported_tool():
    """The public index should stay in sync with bundled tool metadata."""
    index = INDEX_DOC.read_text(encoding="utf-8")
    for skill_dir in _skill_dirs():
        assert "`{}`".format(skill_dir.name) in index
        for tool in _tools(skill_dir):
            assert "`{}`".format(_exported_tool_name(skill_dir.name, tool["name"])) in index


def test_public_docs_and_fixtures_avoid_private_paths():
    """Public docs and fixtures should be portable across machines."""
    candidates = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    fixtures_dir = ROOT / "tests" / "fixtures"
    if fixtures_dir.is_dir():
        candidates.extend(sorted(fixtures_dir.rglob("*")))

    forbidden = [
        re.compile(r"[A-Z]:\\Users\\", re.IGNORECASE),
        re.compile(r"PycharmProjects", re.IGNORECASE),
        re.compile(r"git\.woa\.com", re.IGNORECASE),
        re.compile(r"Originated from", re.IGNORECASE),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not pattern.search(text), path


def test_bundled_skill_files_are_included_in_built_wheel(tmp_path):
    """Built wheels must include bundled skill metadata, tools, and scripts."""
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())

    for skill_dir in _skill_dirs():
        package_prefix = "dcc_mcp_3dsmax/skills/{}/".format(skill_dir.name)
        assert package_prefix + "SKILL.md" in names
        assert package_prefix + "tools.yaml" in names
        for script_path in skill_dir.glob("*.py"):
            assert package_prefix + script_path.name in names
