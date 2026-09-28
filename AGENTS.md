# AGENTS.md — dcc-mcp-3dsmax

> Navigation map for AI agents. Detailed API lives in `README.md` and `llms.txt`.
> This file is a **map**, not an encyclopedia — follow the links for depth.

## Build & test

```bash
vx just setup              # install dev deps + verify imports
vx just check              # ruff lint + quick tests
vx just prek               # pre-commit gate: autofix, format, lint, quick tests
vx just ci                 # lint-all + coverage (local CI simulation)
```

Extra recipes (verify names in `justfile` before inventing new ones):

```bash
vx just test-e2e           # automated E2E debug tests, no 3ds Max required
vx just test-sidecar       # sidecar bridge protocol tests
vx just max-link-win       # symlink into 3ds Max scripts dir (PowerShell)
vx just max-dev-debug-win  # build core + link + launch 3ds Max
vx just max-gateway-health # probe http://127.0.0.1:9765/mcp
```

## Agent control path

AI agent runtimes default to the shared gateway through the `dcc-mcp` skill and
`dcc-mcp-cli` REST commands:

```bash
dcc-mcp-cli search --query "<task>" --dcc-type 3dsmax
dcc-mcp-cli describe <tool-slug>
dcc-mcp-cli call <tool-slug> --json '{"key":"value"}'
```

Use `dcc-mcp-cli list` for live instances and `dcc-mcp-cli dcc-types` for
release-catalog support. IDE users may continue to configure the gateway MCP
endpoint; adapter-local Python start APIs are for host bootstrap and tests.

### CLI availability and updates

If `dcc-mcp-cli` is missing, obtain user consent before using the official
install commands in the README Agent workflow. Keep an official build current
with:

```bash
dcc-mcp-cli update check
dcc-mcp-cli update apply
```

`update apply` stages the latest CLI for the next launch; it does not replace a
running server.

## Quick start

```python
import dcc_mcp_3dsmax
server = dcc_mcp_3dsmax.start_server()
# MCP client connects to http://127.0.0.1:9765/mcp
```

## Quick facts

- **Current version:** 0.2.14 <!-- x-release-please-version -->
- **Core dependency:** `dcc-mcp-core>=0.20.24,<1.0.0`
- **Server dependency:** `dcc-mcp-server>=0.20.22,<1.0.0`
- **Python:** 3.7+ (keep py37 syntax valid; `scripts/check_py37_syntax.py` gates it)
- **3ds Max:** 2017+ with `pymxs`
- **Skill families:** 16 — full inventory in `docs/BUNDLED_SKILLS.md`

## Skills-first workflow

```
1. dcc_capability_manifest({loaded_only: false}) for a compact index
2. search_skills(query="scene") -> load_skill("3dsmax-scene") -> typed tool
3. use 3dsmax-scripting__execute_python only as last resort
```

**Skill authoring gotcha:** lazy-import `pymxs` *inside* the function body, never
at module import time.

```python
from dcc_mcp_3dsmax.api import max_success, with_max

@with_max
def create_box(width: float = 100.0) -> dict:
    import pymxs
    rt = pymxs.runtime
    return max_success("Created box", object_name=str(rt.Box(width=width)))
```

## Repo layout

| Path | Role |
|------|------|
| `src/dcc_mcp_3dsmax/server.py` | `MaxMcpServer` composition root |
| `src/dcc_mcp_3dsmax/_env.py` | All `DCC_MCP_3DSMAX_*` env-var resolution |
| `src/dcc_mcp_3dsmax/dispatcher/` | Thread-affinity dispatchers |
| `src/dcc_mcp_3dsmax/context_snapshot.py` | Real-time scene state |
| `src/dcc_mcp_3dsmax/capabilities.py` | DCC capability reporting |
| `src/dcc_mcp_3dsmax/api.py` | Authoring helpers (`max_success`, `with_max`, `require_param`) |
| `src/dcc_mcp_3dsmax/skills/` | 16 bundled skill packages |
| `skills/dcc-mcp-3dsmax-setup/` | Agent-facing setup skill |
| `tests/` | Unit + automated E2E debug tests |
| `tools/` | Windows dev-link / status PowerShell helpers |
| `scripts/` | `check_py37_syntax.py`, CI helpers |
| `packaging/assemble_mzp.py` | MZP installer builder |
| `docs/` | API, bundled skills, sidecar, undo, skill development |

## Reference material (follow, do not inline)

- **Bundled skill + tool inventory:** [docs/BUNDLED_SKILLS.md](docs/BUNDLED_SKILLS.md)
- **Skill authoring guide:** [docs/SKILL_DEVELOPMENT.md](docs/SKILL_DEVELOPMENT.md)
- **Python API reference:** [docs/API.md](docs/API.md)
- **Runtime bridge protocol:** [docs/SIDECAR.md](docs/SIDECAR.md)
- **Undo semantics:** [docs/UNDO.md](docs/UNDO.md)
- **Environment variables:** `README.md` (full `DCC_MCP_3DSMAX_*` table)
- **Compact / exhaustive AI reference:** `llms.txt` / `llms-full.txt`
- **Agent-facing install:** `install.md`
- **Examples:** `examples/` (`start_server.py`, `start_sidecar_bridge.py`)

## Vendor integration notes

- [docs/integrations/claude.md](docs/integrations/claude.md) — Claude Desktop
  config, progressive loading, viewport + security tips.
- [docs/integrations/gemini.md](docs/integrations/gemini.md) — Gemini / Vertex
  setup, code-first workflows, validation chains.

## Release

- release-please drives versioning from Conventional Commits on `main`.
- Whether a release is cut at all is a changelog question, not a prefix question: if every
  commit in the batch lands in a `hidden: true` section the changelog entry is empty, and
  release-please skips the whole batch — no release pull request, **no version bump**
  (`strategies/base.ts` logs “No user facing commits found since … - skipping” when
  `changelogEmpty()` finds only the heading line).
- This repo overrides `changelog-sections` in `release-please-config.json`: `feat:`, `fix:`, 
  `perf:`, `refactor:` and `docs:` are **visible**; `style:`, `chore:`, `test:`, `ci:` and
  `build:` are `hidden: true`. A visible `refactor:` therefore cuts a release.
- Only once a release *is* cut does the prefix choose the bump. This repo is pre-1.0 and sets
  `bump-minor-pre-major` and `bump-patch-for-minor-pre-major`, so on `0.x`: breaking → minor
  and `feat:` → **patch** — not major/minor. Anything else → patch.
- Use `chore:` when the batch should **not** cut a release; use `docs:` when doc-only work
  should cut a patch release.
- release-please also rewrites the version marker in this file, in
  `pyproject.toml`, and in `src/dcc_mcp_3dsmax/__version__.py`.

## Do / Don't

- **Do** single-source agent instructions here. This is the only agent contract
  file at the repo root.
- **Do** keep this file a navigation map — long reference material belongs in
  `docs/` or `llms.txt`.
- **Don't** add `CLAUDE.md` / `GEMINI.md` / `CURSOR.md` / `ANTHROPIC.md` /
  `OPENAI.md` / `COPILOT.md` / `CODEBUDDY.md` / `.cursorrules` / `.clinerules` /
  `.windsurfrules` at the root. Vendor-specific notes live under
  `docs/integrations/`, linked from here.
- **Don't** hardcode an exact version in tests (`assert __version__ == "X.Y.Z"`)
  — release-please bumps will break it. Use `>=` or read package metadata.
- **Don't** commit build artifacts to the repo root (`*.o`, `coverage.json`,
  `audit-result.json`, `clippy_check.txt`, `commit_msg.txt`).
