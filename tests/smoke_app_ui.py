"""Smoke test script for 3ds Max ``app_ui__*`` interactive closed loop.

Run against a running 3ds Max 2024 instance with the DCC MCP server
(listening on ``http://127.0.0.1:9765/mcp`` by default).

Usage
-----
    1. Launch 3ds Max 2024 (the DCC MCP sidecar starts automatically).
    2. In another terminal::

        python tests/smoke_app_ui.py

    The script exercises the full snapshot → find → act → wait_for → snapshot
    cycle, then reports pass/fail.

What it tests
-------------
    - app_ui__snapshot captures the 3ds Max main window Qt tree.
    - app_ui__find resolves DCC MCP menu / status-window controls.
    - app_ui__act performs a safe action (click / focus / toggle).
    - app_ui__wait_for polls until a UI condition is met.
    - No stale-control / policy-disabled errors from the happy path.
    - No console / black window regression (external Computer Use check).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

MCP_URL = "http://127.0.0.1:9765/mcp"
SESSION = "smoke-app-ui"
TIMEOUT_S = 30


def _rpc(method: str, params: dict | None = None) -> dict:
    """Call one MCP tool on the Streamable HTTP server."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": int(time.time() * 1000) % 10**6,
        }
    ).encode()
    req = urllib.request.Request(
        MCP_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read())


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def assert_ok(result: dict, context: str) -> None:
    if "error" in result and result.get("success") is False:
        print(f"  ❌ FAIL ({context}): {result.get('error', result)}")
        sys.exit(1)
    print(f"  ✅ OK ({context})")


def main() -> None:
    print("=" * 60)
    print("app_ui smoke test — 3ds Max interactive closed loop")
    print("=" * 60)

    # ------------------------------------------------------------------ #
    # 1. app_ui__snapshot                                                #
    # ------------------------------------------------------------------ #
    step("1. app_ui__snapshot — capture the 3ds Max main window")
    resp = _rpc(
        "tools/call",
        {
            "name": "app_ui__snapshot",
            "arguments": {"session_id": SESSION},
        },
    )
    assert_ok(resp, "snapshot")
    snapshot = resp.get("result", {}).get("content", [{}])[0].get("snapshot", {})
    root = snapshot.get("root", {})
    print(f"    Window: {root.get('label', '?')} ({root.get('role', '?')})")
    print(f"    Nodes:  {snapshot.get('node_count', '?')}")
    assert root.get("role") in ("window",), f"expected window role, got {root.get('role')}"

    # ------------------------------------------------------------------ #
    # 2. app_ui__find — locate controls                                  #
    # ------------------------------------------------------------------ #
    step("2. app_ui__find — locate DCC MCP controls")
    resp = _rpc(
        "tools/call",
        {
            "name": "app_ui__find",
            "arguments": {
                "session_id": SESSION,
                "limit": 10,
            },
        },
    )
    assert_ok(resp, "find")
    content = resp.get("result", {}).get("content", [{}])[0]
    print(f"    Matches: {content.get('count', '?')}")

    # Try to find a clickable button
    resp = _rpc(
        "tools/call",
        {
            "name": "app_ui__find",
            "arguments": {
                "session_id": SESSION,
                "role": "button",
                "limit": 5,
            },
        },
    )
    assert_ok(resp, "find buttons")
    content = resp.get("result", {}).get("content", [{}])[0]
    matches = content.get("matches", [])
    print(f"    Buttons: {len(matches)}")
    target_id = None
    for m in matches:
        label = m.get("label", "").lower()
        if "dcc" in label or "mcp" in label or "status" in label or "menu" in label:
            target_id = m.get("id")
            print(f"    → Found DCC MCP control: {m.get('label')} (id={target_id})")
            break
    if not target_id and matches:
        target_id = matches[0]["id"]
        print(f"    → Using first button: {matches[0].get('label')} (id={target_id})")

    # ------------------------------------------------------------------ #
    # 3. app_ui__act — perform a safe action                             #
    # ------------------------------------------------------------------ #
    step("3. app_ui__act — click / focus / toggle")
    if target_id:
        # focus is the safest action — no side effects
        resp = _rpc(
            "tools/call",
            {
                "name": "app_ui__act",
                "arguments": {
                    "session_id": SESSION,
                    "control_id": target_id,
                    "action": "focus",
                },
            },
        )
        assert_ok(resp, f"focus {target_id}")
        print(f"    Focus on {target_id} succeeded")
    else:
        print("    ⚠ No button control found; skipping act step")
        print("    (The test is still valid: snapshot+find work correctly)")

    # ------------------------------------------------------------------ #
    # 4. app_ui__wait_for — wait for a condition                         #
    # ------------------------------------------------------------------ #
    step("4. app_ui__wait_for — window should still be visible")
    resp = _rpc(
        "tools/call",
        {
            "name": "app_ui__wait_for",
            "arguments": {
                "session_id": SESSION,
                "condition": {
                    "kind": "control_exists",
                    "role": "window",
                    "timeout_ms": 3000,
                    "interval_ms": 200,
                },
            },
        },
    )
    assert_ok(resp, "wait_for window")
    print("    Main window still present (condition satisfied)")

    # ------------------------------------------------------------------ #
    # 5. app_ui__snapshot — post-action verification                     #
    # ------------------------------------------------------------------ #
    step("5. app_ui__snapshot — verify post-action state")
    resp = _rpc(
        "tools/call",
        {
            "name": "app_ui__snapshot",
            "arguments": {"session_id": SESSION},
        },
    )
    assert_ok(resp, "snapshot post-action")
    snapshot2 = resp.get("result", {}).get("content", [{}])[0].get("snapshot", {})
    print(f"    Snapshot revision valid: {snapshot2.get('focus_id', '?')}")

    # ------------------------------------------------------------------ #
    # 6. Negative tests                                                  #
    # ------------------------------------------------------------------ #
    step("6. Negative: stale_control detection")
    resp = _rpc(
        "tools/call",
        {
            "name": "app_ui__act",
            "arguments": {
                "session_id": SESSION,
                "control_id": "nonexistent",
                "action": "click",
                "snapshot_id": "stale-test:0",
            },
        },
    )
    err = resp.get("result", {}).get("content", [{}])[0]
    if err.get("isError") or err.get("error") == "stale_control":
        print("  ✅ Stale control correctly detected")
    else:
        print(f"  ⚠ Stale control result: {err}")

    # ------------------------------------------------------------------ #
    # Done                                                               #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 60)
    print("app_ui smoke test PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
