"""Focused contracts for renderer activation in pymxs runtimes."""

from __future__ import annotations

import types

from dcc_mcp_3dsmax._render_advanced import configure_renderer, set_renderer


class _ArnoldRenderer:
    def __init__(self) -> None:
        self.AA_samples = 3


class _Runtime:
    def __init__(self) -> None:
        self.renderers = types.SimpleNamespace(current=None)
        self.Arnold = _ArnoldRenderer
        self.renderWidth = 960
        self.renderHeight = 540


def test_set_renderer_uses_maxtoa_arnold_class_and_renderers_current():
    runtime = _Runtime()

    result = set_renderer(runtime, "arnold")

    assert result["success"] is True
    assert isinstance(runtime.renderers.current, _ArnoldRenderer)
    assert result["data"]["settings"]["renderer"] == "_ArnoldRenderer"


def test_configure_renderer_reads_renderers_current():
    runtime = _Runtime()
    runtime.renderers.current = _ArnoldRenderer()

    result = configure_renderer(runtime, settings={"AA_samples": 6})

    assert result["success"] is True
    assert runtime.renderers.current.AA_samples == 6
