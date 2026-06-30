"""Registration phases for MaxMcpServer builtin actions.

Uses the shared phase framework from :mod:`dcc_mcp_core._registration`.
"""

from __future__ import annotations

from typing import Sequence

from dcc_mcp_core._registration import (
    RegistrationPhase,
    get_standard_phases,
)


def default_registration_phases() -> Sequence[RegistrationPhase]:
    """Return the ordered list of phases used by 3ds Max."""
    # Use the core standard phases to ensure 3ds Max gets all the unified
    # integrations (introspect, feedback, readiness, etc).
    return get_standard_phases()
