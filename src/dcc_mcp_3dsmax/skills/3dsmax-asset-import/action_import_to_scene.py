"""Import an asset described by an AssetDescriptor into the scene."""

from __future__ import annotations

from typing import Any, Dict, Optional

from dcc_mcp_3dsmax._asset_import_utils import import_asset_to_scene
from dcc_mcp_3dsmax.api import get_runtime, with_max


@with_max
def main(
    descriptor: dict,
    group_name: Optional[str] = None,
    name_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """Import an FBX/OBJ/3DS asset from an AssetDescriptor into the scene."""
    return import_asset_to_scene(
        get_runtime(),
        descriptor,
        group_name=group_name,
        name_prefix=name_prefix,
    )
