"""Prepare a 3ds Max 3dsmaxpy environment for dcc-mcp-3dsmax MCP use."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_GATEWAY_URL = "http://127.0.0.1:9765/mcp"
STARTUP_SCRIPT_NAME = "dcc_mcp_3dsmax_startup.ms"


def run(command: list[str], cwd: Optional[Path] = None) -> None:
    print("+ " + " ".join(command))
    subprocess.check_call(command, cwd=str(cwd) if cwd else None)


def capture(command: list[str], cwd: Optional[Path] = None) -> str:
    print("+ " + " ".join(command))
    return subprocess.check_output(command, cwd=str(cwd) if cwd else None, text=True)


def candidate_maxpy_paths() -> Iterable[Path]:
    for env_name in ("MAX_PY", "DCC_MCP_3DSMAX_PYTHON", "DCC_MCP_3DSMAX_MAXPY"):
        env_value = os.environ.get(env_name)
        if env_value:
            yield Path(env_value)

    path_match = shutil.which("3dsmaxpy")
    if path_match:
        yield Path(path_match)

    # 3ds Max is Windows-only; focus discovery on common Autodesk install dirs.
    program_files = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    for root in program_files:
        if not root:
            continue
        autodesk = Path(root) / "Autodesk"
        for year in range(2027, 2019, -1):
            yield autodesk / ("3ds Max %s" % year) / "3dsmaxpy.exe"


def resolve_maxpy(explicit: Optional[str]) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return path
        raise SystemExit("3dsmaxpy does not exist: %s" % path)

    seen = set()
    for path in candidate_maxpy_paths():
        expanded = path.expanduser()
        key = str(expanded).lower()
        if key in seen:
            continue
        seen.add(key)
        if expanded.exists():
            return expanded

    raise SystemExit(
        "Could not find 3dsmaxpy. Re-run with --maxpy, or set "
        "DCC_MCP_3DSMAX_PYTHON (or MAX_PY) to the full 3dsmaxpy.exe path."
    )


def find_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists() and (parent / "src" / "dcc_mcp_3dsmax").exists():
            return parent
    return Path.cwd()


def install_package(maxpy: Path, source: str, repo_root: Path, skip_install: bool) -> None:
    if skip_install:
        print("Skipping pip install because --skip-install was passed.")
        return

    run([str(maxpy), "-m", "ensurepip", "--upgrade"])
    run(
        [
            str(maxpy),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip<25; python_version<'3.8'",
            "pip; python_version>='3.8'",
        ]
    )

    if source == "local":
        run([str(maxpy), "-m", "pip", "install", "-e", "."], cwd=repo_root)
    elif source == "pypi":
        run([str(maxpy), "-m", "pip", "install", "--upgrade", "dcc-mcp-3dsmax"])
    else:
        raise SystemExit("Unknown source: %s" % source)


def verify_import(maxpy: Path) -> None:
    code = (
        "import dcc_mcp_3dsmax; "
        "print('dcc-mcp-3dsmax', dcc_mcp_3dsmax.__version__); "
        "import dcc_mcp_core; "
        "print('dcc-mcp-core import ok')"
    )
    run([str(maxpy), "-c", code])


def query_3dsmax_startup_dir(maxpy: Path) -> Optional[Path]:
    code = r"""
import pathlib
try:
    import pymxs
    rt = pymxs.runtime
    name = rt.Name('userStartupScripts')
    value = rt.getDir(name)
    print(pathlib.Path(str(value)).resolve())
except Exception:
    raise SystemExit(42)
""".strip()
    try:
        output = capture([str(maxpy), "-c", code])
    except (subprocess.CalledProcessError, OSError):
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return Path(lines[-1]) if lines else None


def infer_max_year(maxpy: Path) -> Optional[str]:
    text = str(maxpy)
    match = re.search(r"3ds Max (\d{4})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def fallback_startup_dir(maxpy: Path) -> Optional[Path]:
    year = infer_max_year(maxpy)
    if not year:
        return None
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "Autodesk" / "3dsMax" / ("%s - 64bit" % year) / "ENU" / "scripts" / "startup"
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Autodesk" / ("3ds Max %s" % year) / "scripts" / "startup"
    return None


def resolve_startup_dir(maxpy: Path, explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        return Path(explicit).expanduser()
    return query_3dsmax_startup_dir(maxpy) or fallback_startup_dir(maxpy)


def _maxscript_string(value: str) -> str:
    return value.replace("\\", "/").replace('"', '\\"')


def build_startup_script(*, source: str, repo_root: Path) -> str:
    bootstrap_paths = []
    if source == "local":
        bootstrap_paths.append(str((repo_root / "src").resolve()))
        bootstrap_paths.append(str(repo_root.resolve()))

    path_lines = []
    for path in bootstrap_paths:
        path_lines.append(
            "for path in [r'''{0}''']:\n"
            "    if path and path not in sys.path:\n"
            "        sys.path.insert(0, path)\n".format(path)
        )

    repo_env = ""
    if source == "local":
        repo_env = "os.environ.setdefault('DCC_MCP_3DSMAX_ROOT', r'''{}''')\n".format(str(repo_root.resolve()))

    python_code = (
        "import os, sys\n"
        + "".join(path_lines)
        + repo_env
        + "import dcc_mcp_3dsmax\n"
        + "dcc_mcp_3dsmax.install_menu()\n"
        + "dcc_mcp_3dsmax.install_shutdown_callback()\n"
        + "dcc_mcp_3dsmax.main()\n"
        + "print('dcc-mcp-3dsmax runtime ready from startup hook')\n"
    )
    escaped_lines = ["    py += \"{}\\n\"".format(_maxscript_string(line)) for line in python_code.splitlines()]
    return "\n".join(
        [
            "-- Auto-generated by dcc-mcp-3dsmax agent setup.",
            "-- Re-run setup_dcc_mcp_3dsmax.py to refresh this file.",
            "(",
            "    local py = \"\"",
            *escaped_lines,
            "    python.Execute py",
            ")",
            "",
        ]
    )


def write_startup_hook(
    *,
    startup_dir: Optional[Path],
    out_dir: Path,
    source: str,
    repo_root: Path,
    skip_startup_hook: bool,
) -> Optional[Path]:
    script = build_startup_script(source=source, repo_root=repo_root)
    out_path = out_dir / STARTUP_SCRIPT_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(script, encoding="utf-8")
    print("Wrote %s" % out_path)

    if skip_startup_hook:
        print("Skipping 3ds Max startup hook install because --no-startup-hook was passed.")
        return None

    if startup_dir is None:
        print("Could not resolve 3ds Max startup directory; generated startup script only.")
        return None

    startup_dir.mkdir(parents=True, exist_ok=True)
    installed = startup_dir / STARTUP_SCRIPT_NAME
    installed.write_text(script, encoding="utf-8")
    print("Installed startup hook %s" % installed)
    return installed


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("Wrote %s" % path)


def write_mcp_snippets(out_dir: Path, server_name: str, mcp_url: str) -> None:
    payload = {"mcpServers": {server_name: {"url": mcp_url}}}
    write_json(out_dir / "mcp-streamable-http.json", payload)

    smoke_prompt = """Use the 3ds Max MCP server. First call dcc_capability_manifest with loaded_only=false.
Then load the 3dsmax-modeling skill, create a sphere named mcp_setup_smoke_sphere
with radius 50, and tell me the MCP URL and created node name.
Use typed tools where available and avoid execute_python unless no typed tool fits.
"""
    smoke_path = out_dir / "smoke-prompt.txt"
    smoke_path.write_text(smoke_prompt, encoding="utf-8")
    print("Wrote %s" % smoke_path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maxpy", help="Full path to Autodesk 3ds Max 3dsmaxpy.exe.")
    parser.add_argument(
        "--source",
        choices=["local", "pypi"],
        default="local",
        help="Install from this checkout or from PyPI. Default: local.",
    )
    parser.add_argument(
        "--mcp-url",
        default=DEFAULT_GATEWAY_URL,
        help="MCP URL to write into generated host config. Default: gateway URL.",
    )
    parser.add_argument(
        "--server-name",
        default="3dsmax",
        help="MCP server name in generated config. Default: 3dsmax.",
    )
    parser.add_argument(
        "--out-dir",
        default=".dcc-mcp/agent-setup",
        help="Directory for generated MCP snippets. Default: .dcc-mcp/agent-setup.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Only verify imports and write MCP snippets.",
    )
    parser.add_argument(
        "--startup-dir",
        help="3ds Max userStartupScripts directory. Auto-detected with 3dsmaxpy when omitted.",
    )
    parser.add_argument(
        "--no-startup-hook",
        action="store_true",
        help="Generate but do not install the 3ds Max startup hook.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_root = find_repo_root()
    maxpy = resolve_maxpy(args.maxpy)
    out_dir = (repo_root / args.out_dir).resolve()

    print("Repository: %s" % repo_root)
    print("3dsmaxpy: %s" % maxpy)
    print("MCP URL: %s" % args.mcp_url)

    install_package(maxpy, args.source, repo_root, args.skip_install)
    verify_import(maxpy)
    write_mcp_snippets(out_dir, args.server_name, args.mcp_url)
    startup_dir = resolve_startup_dir(maxpy, args.startup_dir)
    installed_startup = write_startup_hook(
        startup_dir=startup_dir,
        out_dir=out_dir,
        source=args.source,
        repo_root=repo_root,
        skip_startup_hook=args.no_startup_hook,
    )

    print("")
    print("Next:")
    if installed_startup is not None:
        print("1. Open or restart 3ds Max; the startup hook starts the runtime automatically.")
        print("   Startup hook: %s" % installed_startup)
    else:
        print("1. Open 3ds Max and run the generated startup script once:")
        print("   python.ExecuteFile @\"%s\"" % (out_dir / STARTUP_SCRIPT_NAME))
    print("2. Configure the MCP host with %s." % (out_dir / "mcp-streamable-http.json"))
    print("3. Run the smoke prompt in %s." % (out_dir / "smoke-prompt.txt"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
