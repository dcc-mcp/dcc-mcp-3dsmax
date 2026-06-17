"""Tests for the bundled 3ds Max UV and atlas skill."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-uv-atlas"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Bitmap:
    def __init__(self, filename: str) -> None:
        self.filename = filename


class _Material:
    def __init__(self) -> None:
        self.name = "HeroMaterial"
        self.diffuseMap = _Bitmap("textures/hero_basecolor.png")
        self.bumpMap = _Bitmap("textures/hero_normal.png")


class _Modifier:
    def __init__(self, name: str) -> None:
        self.name = name
        self.operations = []

    def unwrap(self):
        self.operations.append(("unwrap", None))

    def pack(self, padding):
        self.operations.append(("pack", padding))

    def normalize(self):
        self.operations.append(("normalize", None))


class _FakeNode:
    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self.handle = handle
        self.isHidden = False
        self.parent = None
        self.modifiers = []
        self.material = _Material()
        self.uv_channels = {
            1: {"uv_count": 8, "face_count": 6, "shells": [{"id": 1, "face_count": 6}], "shell_count": 1}
        }
        self.uv_overlaps = {1: [{"faces": [1, 2]}]}


class _FakePolyOp:
    def setMapSupport(self, node, channel, enabled):  # noqa: N802 - mirrors pymxs runtime naming.
        if enabled:
            node.uv_channels.setdefault(int(channel), {"uv_count": 0, "face_count": 0, "shells": []})
        else:
            node.uv_channels.pop(int(channel), None)

    def copyMapChannel(self, node, source_channel, target_channel):  # noqa: N802 - mirrors pymxs runtime naming.
        node.uv_channels[int(target_channel)] = dict(node.uv_channels[int(source_channel)])

    def getNumMaps(self, node):  # noqa: N802 - mirrors pymxs runtime naming.
        return len(node.uv_channels)


class _FakeRuntime:
    def __init__(self) -> None:
        self.hero = _FakeNode("hero_mesh", 42)
        self.objects = [self.hero]
        self.selection = [self.hero]
        self.polyOp = _FakePolyOp()

    def getNodeByName(self, name):  # noqa: N802 - mirrors pymxs runtime naming.
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def addModifier(self, node, modifier):  # noqa: N802 - mirrors pymxs runtime naming.
        node.modifiers.append(modifier)

    def UVWMap(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("UVWMap")

    def Unwrap_UVW(self):  # noqa: N802 - mirrors pymxs runtime naming.
        return _Modifier("Unwrap_UVW")


def _install_fake_pymxs(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def test_uv_read_tools_return_channels_shells_overlaps_and_atlas(monkeypatch):
    _install_fake_pymxs(monkeypatch)

    channels = _load_action("action_list_uv_channels.py").main(node_names=["hero_mesh"])
    shells = _load_action("action_get_uv_shell_summary.py").main(node_names=["hero_mesh"], channel=1)
    overlaps = _load_action("action_detect_uv_overlaps.py").main(node_names=["hero_mesh"], channel=1)
    atlas = _load_action("action_prepare_texture_atlas.py").main(node_names=["hero_mesh"])

    assert channels["success"] is True
    assert channels["data"]["nodes"][0]["channels"][0]["channel"] == 1
    assert shells["data"]["nodes"][0]["shell_count"] == 1
    assert overlaps["data"]["nodes"][0]["overlap_count"] == 1
    assert atlas["data"]["bitmap_count"] == 2
    assert "textures/hero_basecolor.png" in atlas["data"]["bitmap_paths"]


def test_uv_tools_report_no_selection_and_invalid_channels(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)
    runtime.selection = []

    no_selection = _load_action("action_list_uv_channels.py").main(use_selection=True)
    invalid = _load_action("action_create_uv_channel.py").main(node_names=["hero_mesh"], channel=0)

    assert no_selection["success"] is False
    assert "selection is empty" in no_selection["message"]
    assert invalid["success"] is False
    assert "between 1 and 99" in invalid["message"]


def test_uv_channel_mutations_update_fake_runtime_through_executor(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    from dcc_mcp_3dsmax._executor import run_skill_script

    created = _load_action("action_create_uv_channel.py").main(node_names=["hero_mesh"], channel=2)
    copied = run_skill_script(
        str(SKILL_DIR / "action_copy_uv_channel.py"),
        {"node_names": ["hero_mesh"], "source_channel": 1, "target_channel": 3},
    )

    assert created["success"] is True
    assert 2 in runtime.hero.uv_channels
    assert copied["success"] is True
    assert runtime.hero.uv_channels[3]["uv_count"] == 8

    deleted = _load_action("action_delete_uv_channel.py").main(node_names=["hero_mesh"], channel=2)

    assert deleted["success"] is True
    assert 2 not in runtime.hero.uv_channels


def test_uv_projection_unwrap_pack_and_normalize_add_modifiers(monkeypatch):
    runtime = _install_fake_pymxs(monkeypatch)

    projected = _load_action("action_apply_uv_projection.py").main(
        node_names=["hero_mesh"],
        channel=4,
        projection="box",
        length=100.0,
        width=100.0,
        height=100.0,
    )
    unwrapped = _load_action("action_unwrap_uvs.py").main(node_names=["hero_mesh"], channel=4)
    packed = _load_action("action_pack_uvs.py").main(node_names=["hero_mesh"], channel=4, padding=0.05)
    normalized = _load_action("action_normalize_uvs.py").main(node_names=["hero_mesh"], channel=4)

    assert projected["success"] is True
    assert unwrapped["success"] is True
    assert packed["success"] is True
    assert normalized["success"] is True
    assert [modifier.name for modifier in runtime.hero.modifiers] == [
        "UVWMap",
        "Unwrap_UVW",
        "Unwrap_UVW",
        "Unwrap_UVW",
    ]
    assert runtime.hero.modifiers[0].mapChannel == 4
    assert runtime.hero.modifiers[0].maptype == "box"
    assert ("pack", 0.05) in runtime.hero.modifiers[2].operations
    assert ("normalize", None) in runtime.hero.modifiers[3].operations
