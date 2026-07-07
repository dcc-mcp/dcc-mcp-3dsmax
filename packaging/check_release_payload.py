#!/usr/bin/env python3
"""Smoke-check an assembled dcc-mcp-3dsmax release payload."""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from typing import Tuple


def payload_root(path: Path) -> Tuple[Path, Optional[tempfile.TemporaryDirectory]]:
    path = path.resolve()
    if path.is_dir():
        return (path / "payload" if (path / "payload").is_dir() else path), None
    if not zipfile.is_zipfile(str(path)):
        raise RuntimeError("release payload is neither a directory nor a zip-compatible MZP: {}".format(path))

    tmp = tempfile.TemporaryDirectory()
    with zipfile.ZipFile(str(path)) as zf:
        zf.extractall(tmp.name)
    return Path(tmp.name) / "payload", tmp


def python_root(payload: Path) -> Path:
    name = "python37" if sys.version_info < (3, 8) else "python"
    root = payload / name
    if not root.is_dir():
        raise RuntimeError("payload is missing {} for Python {}.{}".format(name, *sys.version_info[:2]))
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", help="Path to an assembled .mzp/.zip or extracted payload directory")
    args = parser.parse_args()

    payload, cleanup = payload_root(Path(args.payload))
    try:
        root = python_root(payload)
        if sys.version_info >= (3, 8) and root.name.lower() == "python37":
            raise RuntimeError("Python 3.8+ must not import from payload/python37")
        sys.path.insert(0, str(root))

        import dcc_mcp_core._core  # noqa: F401, PLC0415
        import dcc_mcp_3dsmax  # noqa: PLC0415
        from dcc_mcp_3dsmax.server import MaxMcpServer, MaxServerOptions  # noqa: PLC0415

        server = MaxMcpServer(options=MaxServerOptions(port=0, enable_gateway_failover=False, job_storage_path=""))
        server.register_builtin_actions(include_bundled=True)
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
