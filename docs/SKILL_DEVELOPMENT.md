# Skill Development Guide for dcc-mcp-3dsmax

This guide explains how to create skills for `dcc-mcp-3dsmax`.

## Table of Contents

1. [Overview](#overview)
2. [Skill Structure](#skill-structure)
3. [SKILL.md Format](#skillmd-format)
4. [Writing Action Scripts](#writing-action-scripts)
5. [Testing Skills](#testing-skills)
6. [Examples](#examples)

## Overview

Skills in `dcc-mcp-3dsmax` are directories containing:
- `SKILL.md` - Metadata (name, description, actions)
- `action_*.py` - Action scripts that implement the skill's functionality

Skills are automatically discovered and loaded by the server at startup.

## Skill Structure

```
src/dcc_mcp_3dsmax/skills/
├── 3dsmax-scene/              # Skill directory
│   ├── SKILL.md              # Skill metadata
│   └── action_get_scene_info.py  # Action script
├── 3dsmax-modeling/
│   ├── SKILL.md
│   ├── action_create_box.py
│   ├── action_create_sphere.py
│   └── ...
└── ...
```

## SKILL.md Format

`SKILL.md` uses a custom format to define skill metadata and actions:

```markdown
dcc-mcp-3dsmax - <Skill Name>
=============================================

<Brief description of the skill>

Actions
-------

### action_<name>

<Action description>

**Parameters**

| Name   | Type  | Default | Description        |
|--------|--------|---------|--------------------|
| param1  | string | "default"| Parameter description |

**Returns**

`dict` with `success`, `data`.

Examples
--------

.. code-block:: python

    # Example usage

Elicitation
-----------

- ``param1`` — description of elicitation behavior.

Affinity
--------

Declares ``affinity: main`` (must run on 3ds Max main thread).
```

## Writing Action Scripts

Action scripts should:

1. Import from `dcc_mcp_3dsmax.api`
2. Define a `main(**params)` function
3. Use the `@with_max` decorator for 3ds Max operations
4. Return a plain dictionary

### Basic Template

```python
"""Action description."""

# Import future modules
from __future__ import annotations

# Import local modules
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(param1: str = "default_value") -> dict:
    """Action description.

    Parameters
    ----------
    param1 : str
        Example parameter.

    Returns
    -------
    dict
        The action response.
    """
    # Access 3ds Max runtime
    rt = get_runtime()

    # Perform 3ds Max operations
    # ...

    return {
        "success": True,
        "message": "Operation completed successfully",
        "data": {
            "result": "value",
        },
    }
```

### Using API Helpers

```python
from dcc_mcp_3dsmax.api import require_param, max_success, max_error

def main(node_name: str = None, optional_param: str = None) -> dict:
    # Require a parameter (returns error if missing)
    params = {"node_name": node_name, "optional_param": optional_param}
    resolved_node_name = require_param(params, "node_name")

    # Return success
    return max_success("Operation successful", node_name=resolved_node_name)

    # Return error
    # return max_error("Operation failed", reason="...")

```

## Testing Skills

Create test files in the `tests/` directory:

```python
"""Tests for action_create_box."""

# Import future modules
from __future__ import annotations

# Import third-party modules
import pytest

# Import local modules
from dcc_mcp_3dsmax.skills.modeling.action_create_box import main


class TestCreateBox:
    """Tests for the create_box action."""

    def test_create_box_default(self):
        """Test creating a box with default parameters."""
        response = main()
        assert response["success"]
        assert "node_name" in response["data"]

    def test_create_box_custom(self):
        """Test creating a box with custom parameters."""
        response = main(width=200.0, height=150.0, depth=100.0)
        assert response["success"]
```

## Examples

### Example 1: Scene Info Skill

`SKILL.md`:

```markdown
dcc-mcp-3dsmax - 3ds Max Scene Info
=============================================

Get information about the current 3ds Max scene.

Actions
-------

### action_get_scene_info

Get scene information.

**Parameters**

None

**Returns**

`dict` with `success`, `node_count`, `scene_name`.

Affinity
--------

Declares ``affinity: main``.
```

`action_get_scene_info.py`:

```python
"""Get scene information."""

# Import future modules
from __future__ import annotations

# Import local modules
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main() -> dict:
    """Get scene information.

    Returns
    -------
    dict
        The action response.
    """
    rt = get_runtime()

    # Get all nodes
    nodes = list(rt.objects)
    node_count = len(nodes)

    # Get selected nodes
    selection = list(rt.selection)
    selection_count = len(selection)

    return {
        "success": True,
        "message": f"Scene has {node_count} nodes, {selection_count} selected",
        "data": {
            "node_count": node_count,
            "selection_count": selection_count,
            "scene_name": str(rt.sceneFileName) if rt.sceneFileName else None,
        },
    }
```

### Example 2: Create Material Skill

See `src/dcc_mcp_3dsmax/skills/3dsmax-materials/` for a complete example.

## Best Practices

1. **Always use `@with_max` decorator** for actions that call `pymxs.runtime`
2. **Validate parameters** using `require_param()`
3. **Return meaningful messages** in response dictionaries
4. **Handle errors gracefully** and return `success=False`
5. **Use type hints** for better code clarity
6. **Write tests** for your actions
7. **Declare undo semantics** on every tool that is declared
   `destructive: true` in `tools.yaml`: an `undo` block with `supported`,
   `granularity`, and `notes`. The vocabulary and the contract are in
   [UNDO.md](UNDO.md); `tests/test_undo_skill.py` rejects a declaration that
   omits it.

## Advanced Topics

### Affinity

Declare affinity in `SKILL.md`:

- `affinity: any` - Can run on any thread
- `affinity: main` - Must run on 3ds Max main thread

### Cancellation

For long-running operations, check for cancellation:

```python
from dcc_mcp_3dsmax.dispatcher import check_3dsmax_cancelled

def long_running_task():
    for i in range(1000):
        check_3dsmax_cancelled()  # Raise CancelledError if cancelled
        # ... do work ...
```

### Progress Reporting

For long operations, report progress via `progress_token`:

```python
def main(_meta: dict = None) -> dict:
    progress_token = (_meta or {}).get("progressToken")

    for i in range(100):
        # Report progress
        if progress_token:
            # Send progress notification
            pass
        # ... do work ...
    return {"success": True, "message": "Operation completed"}
```
