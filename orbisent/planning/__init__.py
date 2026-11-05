"""Planning for ARTIF. 1.0 (ARCHITECTURE_1.0.md §4.4 and §12).

Implemented so far (Planning Foundation, PR 1): the PlanValidator and the exact
expected-outcome descriptions. Nothing here generates plans yet: there is no
planner, template or derivation engine in this package.
"""

from orbisent.planning.validator import (
    BUILTIN_TOOLS,
    CHECK_KINDS,
    COUNT_PATTERNS,
    FORMAT_VERSION,
    PlanValidator,
    ValidationLimits,
    controlled_catalog,
    describe_expected,
)

__all__ = [
    "BUILTIN_TOOLS",
    "CHECK_KINDS",
    "COUNT_PATTERNS",
    "FORMAT_VERSION",
    "PlanValidator",
    "ValidationLimits",
    "controlled_catalog",
    "describe_expected",
]
