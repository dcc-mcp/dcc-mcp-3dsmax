---
name: 3dsmax-scripting
description: >-
  Domain skill - run explicit 3ds Max scripting checks and inspect host-side
  developer surfaces. Use for debugging automation, resolving nodes, and
  inspecting runtime symbols when typed tools are not enough.
license: MIT
compatibility: "dcc-mcp-core 0.17.56+, 3ds Max 2024+"
metadata:
  dcc-mcp:
    dcc: 3dsmax
    version: "1.0.0"
    layer: domain
    stage: authoring
    search-hint: "3ds Max scripting MaxScript Python runtime symbols macros nodes developer debugging"
    tags: "3dsmax, scripting, maxscript, python, introspection, developer"
    tools: tools.yaml
    intent: "Execute auditable Python or MaxScript snippets and inspect 3ds Max runtime symbols for debugging."
    search_aliases: ["scripting", "scripting"]
    recall_context:
      app_type: "3dsmax"
      domain: "scripting"
      workflow_stage: "authoring"
      task_category: "diagnose"
    preconditions:
      - type: software
        name: "3ds Max"
        version: ">=2024"
    side_effects:
      creates: false
      modifies: true
      deletes: false
      exports: false
      imports: false
      file_output: false
      render: false
      targets: ["host_runtime"]
    produces: ["runtime_symbols", "macro_list", "node_reference"]
---

# 3ds Max Scripting And Developer Introspection

Run explicit, auditable Python or MaxScript snippets and inspect host runtime
surfaces through structured envelopes. Prefer typed 3ds Max tools first; use
these helpers when debugging, checking host state, or exploring a missing API
surface.
