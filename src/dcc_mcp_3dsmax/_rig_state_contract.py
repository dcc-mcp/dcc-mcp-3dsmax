"""Versioned rig-state assertion surface."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from dcc_mcp_3dsmax._rigging_utils import rig_state_summary, rig_success
from dcc_mcp_3dsmax._skin_weights import get_skin_weights

RIG_STATE_SCHEMA = "dcc-mcp/rig-state@1"


def rig_state(runtime: Any, nodes: Sequence[Any]) -> Dict[str, Any]:
    """Return backward-compatible node rows plus typed joint and Skin summaries."""
    node_rows = [rig_state_summary(node) for node in nodes]
    joint_rows = [row["node"] for row in node_rows if _is_joint(row)]
    skins = []
    for node, row in zip(nodes, node_rows):
        if not row["skinning"]["has_skin"]:
            continue
        identity = row["node"]
        object_id = identity.get("object_id")
        weights = get_skin_weights(
            runtime,
            node_name=identity.get("node_name") if object_id is None else None,
            handle=object_id,
        )
        skin_row = {
            "node": row["node"],
            "skin_modifier": row["skinning"]["skin_modifier"],
            "bone_count": row["skinning"]["bone_count"],
            "vertex_count": None,
            "unnormalized_vertices": None,
            "normalization_verified": False,
        }
        if weights.get("success"):
            payload = weights["data"]["weights"]
            skin_row.update(
                {
                    "vertex_count": payload["vertex_count"],
                    "unnormalized_vertices": payload["unnormalized_vertex_count"],
                    "normalization_verified": True,
                }
            )
        else:
            skin_row["failure_reason"] = weights.get("message")
        skins.append(skin_row)
    constraints: List[Dict[str, Any]] = []
    for row in node_rows:
        for constraint in row["constraints"]:
            constraints.append({"node": row["node"], "constraint": constraint})
    return rig_success(
        "Listed typed rig state",
        schema=RIG_STATE_SCHEMA,
        nodes=node_rows,
        count=len(node_rows),
        joints={"count": len(joint_rows), "nodes": joint_rows},
        skins=skins,
        constraints=constraints,
    )


def _is_joint(row: Dict[str, Any]) -> bool:
    identity = row["node"]
    text = "{} {}".format(identity.get("node_name", ""), identity.get("class_name", "")).lower()
    return "bone" in text or "joint" in text
