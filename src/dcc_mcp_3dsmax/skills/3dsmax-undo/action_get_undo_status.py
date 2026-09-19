"""Report the undo/redo entry points this 3ds Max host exposes."""

from __future__ import annotations

from typing import Any, Dict

from dcc_mcp_3dsmax._undo_utils import (
    GRANULARITY_DESCRIPTIONS,
    scene_fingerprint,
    undo_capabilities,
    undo_success,
)
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main() -> Dict[str, Any]:
    """Probe undo/redo capability and return the current scene fingerprint.

    3ds Max exposes no API for the depth of the history stack, so this tool
    reports *capability*, not stack content. The returned fingerprint lets a
    caller compare the scene before and after a destructive tool and decide for
    itself whether an undo is worth attempting.
    """
    rt = get_runtime()
    data = undo_capabilities(rt)
    data["fingerprint"] = scene_fingerprint(rt)
    data["granularity_vocabulary"] = dict(GRANULARITY_DESCRIPTIONS)
    message = (
        "Undo is available through {}".format(", ".join(data["undo_channels"]))
        if data["undo_supported"]
        else "This 3ds Max host exposes no undo entry point"
    )
    return undo_success(message, **data)
