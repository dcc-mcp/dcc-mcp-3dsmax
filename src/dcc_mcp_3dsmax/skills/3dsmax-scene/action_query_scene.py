"""One entry point for the six read-side scene questions.

Six separate actions forced an agent to guess which one answers its question and
then pay for the wrong guess. ``query_scene`` replaces the guessing with one
call whose ``mode`` says what is being asked:

``overview``
    counts only - node count, selection count, and a per-class histogram.
``filter``
    the node list, narrowed by name substring.
``class``
    the node list, narrowed by class name.
``property``
    the node list with one property read per node, narrowed by its value.
``selection``
    the nodes currently selected.
``delta``
    what changed against a snapshot the caller captured earlier.

Every mode returns ``warnings``, and every mode keeps the difference between
"the answer is empty" and "this host could not answer" visible.
"""

# Import future modules
from __future__ import annotations

# Import built-in modules
from typing import Any, Dict, Optional

# Import local modules
from dcc_mcp_3dsmax._scene_query import SceneQueryError, run_scene_query
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    mode: str = "overview",
    name_filter: Optional[str] = None,
    class_name: Optional[str] = None,
    class_match: str = "exact",
    property_name: Optional[str] = None,
    property_value: Any = None,
    include_hidden: bool = True,
    baseline: Any = None,
    include_snapshot: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """Answer one scene question: overview, filter, class, property, selection, or delta."""
    rt = get_runtime()
    try:
        data = run_scene_query(
            rt,
            mode=mode,
            name_filter=name_filter,
            class_name=class_name,
            class_match=class_match,
            property_name=property_name,
            property_value=property_value,
            include_hidden=include_hidden,
            baseline=baseline,
            include_snapshot=include_snapshot,
            limit=limit,
        )
    except SceneQueryError as exc:
        return {
            "success": False,
            "message": str(exc),
            "data": {"failure_reason": "invalid_request", "mode": mode, "nodes": [], "warnings": []},
        }

    if mode == "overview":
        message = "Scene holds {} node(s) across {} class(es)".format(
            data["summary"]["node_count"], data["summary"]["class_count"]
        )
    elif mode == "delta":
        message = "{} added, {} removed, {} renamed, {} changed against the baseline".format(
            data["added_count"], data["removed_count"], data["renamed_count"], data["changed_count"]
        )
    elif mode == "property":
        message = "{} node(s) match property {}".format(data["count"], data["property"])
    else:
        message = "{} node(s) matched mode {}".format(data["count"], mode)
    return {"success": True, "message": message, "data": data}
