"""Registration phases for MaxMcpServer builtin actions.

Phases import the shared base classes from
:mod:`dcc_mcp_core._registration`.  Each phase wraps one 3ds Max-specific
integration step that was previously inlined inside
:meth:`MaxMcpServer.register_builtin_actions`.
"""

from __future__ import annotations

from typing import Sequence

from dcc_mcp_core._registration import RegistrationContext, RegistrationPhase


class CoreBuiltinActionsPhase(RegistrationPhase):
    """Discover skills via the core registration path (with ``minimal_mode``)."""

    name = "core_builtin_actions"

    def run(self, context: RegistrationContext) -> None:
        context.server._register_core_builtin_actions(context)  # noqa: SLF001


class StrictSkillScanPhase(RegistrationPhase):
    """Run strict skill validation when ``strict_scan`` is enabled."""

    name = "strict_skill_scan"

    def run(self, context: RegistrationContext) -> None:
        context.server._run_strict_skill_scan_phase(context)  # noqa: SLF001


class RecipesToolsPhase(RegistrationPhase):
    """Register ``recipes__*`` tools for skills declaring metadata recipes."""

    name = "recipes_tools"

    def run(self, context: RegistrationContext) -> None:
        context.server._register_recipes_tools(  # noqa: SLF001
            context.extra_skill_paths, context.include_bundled
        )


class SkillReferenceDocsPhase(RegistrationPhase):
    """Register ``skill_refs__*`` tools for skill-adjacent reference docs."""

    name = "skill_reference_docs"

    def run(self, context: RegistrationContext) -> None:
        context.server._register_skill_reference_docs_tools(  # noqa: SLF001
            context.extra_skill_paths, context.include_bundled
        )


class IntrospectToolsPhase(RegistrationPhase):
    """Register the four ``dcc_introspect__*`` MCP tools."""

    name = "introspect_tools"

    def run(self, context: RegistrationContext) -> None:
        context.server._register_introspect_tools()  # noqa: SLF001


class FeedbackToolPhase(RegistrationPhase):
    """Register the ``dcc_feedback__report`` MCP tool."""

    name = "feedback_tool"

    def run(self, context: RegistrationContext) -> None:
        context.server._register_feedback_tool()  # noqa: SLF001


class QtUiInspectorPhase(RegistrationPhase):
    """Register the shared ``qt_ui_inspector__*`` tools (main-thread routed)."""

    name = "qt_ui_inspector"

    def run(self, context: RegistrationContext) -> None:
        context.server._register_qt_ui_inspector()  # noqa: SLF001


class CapabilityManifestPhase(RegistrationPhase):
    """Register the ``dcc_capability_manifest`` MCP tool."""

    name = "capability_manifest"

    def run(self, context: RegistrationContext) -> None:
        context.server._register_capability_manifest_tool()  # noqa: SLF001


class ProjectToolsPhase(RegistrationPhase):
    """Register the four ``project_*`` MCP tools."""

    name = "project_tools"

    def run(self, context: RegistrationContext) -> None:
        context.server._attach_project_tools()  # noqa: SLF001


class ResourcesPhase(RegistrationPhase):
    """Publish ``scene://current`` + dynamic resource producers."""

    name = "resources"

    def run(self, context: RegistrationContext) -> None:
        context.server._attach_resources()  # noqa: SLF001


class SkillCatalogReadyPhase(RegistrationPhase):
    """Signal that the skill catalog has been populated (readiness gate)."""

    name = "skill_catalog_ready"

    def run(self, context: RegistrationContext) -> None:
        context.server._readiness.mark_skill_catalog_ready()  # noqa: SLF001


def default_registration_phases() -> Sequence[RegistrationPhase]:
    return (
        CoreBuiltinActionsPhase(),
        StrictSkillScanPhase(),
        RecipesToolsPhase(),
        SkillReferenceDocsPhase(),
        IntrospectToolsPhase(),
        FeedbackToolPhase(),
        QtUiInspectorPhase(),
        CapabilityManifestPhase(),
        ProjectToolsPhase(),
        ResourcesPhase(),
        SkillCatalogReadyPhase(),
    )
