"""Permission levels, argument specifications and tool descriptors (data only).

A ToolInfo says what a tool is called, how risky it is and which arguments it
takes. It has no callable fields, so holding a ToolInfo never lets anyone run
the tool. The PlanValidator checks plan steps against these descriptors.
"""

import enum
from dataclasses import dataclass

from orbisent.types import check_plan_text, plan_tuple


class Permission(str, enum.Enum):
    """How risky a tool is, from least to most (ARCHITECTURE_1.0.md §4.5)."""

    READ = "read"           # Read inside the workspace.
    CREATE = "create"       # Create new files and folders inside the workspace.
    MODIFY = "modify"       # Change existing files (no tools yet).
    DELETE = "delete"       # Remove anything (no tools yet).
    EXTERNAL = "external"   # Network, messages, other apps (no tools yet).


# The permissions ARTIF. 1.0 allows (§4.8.2). Callers pass them to the validator explicitly.
ARTIF_1_0_PERMISSIONS = frozenset({Permission.READ, Permission.CREATE})

# Argument kinds:
#   target_path  a workspace-relative path the step creates (at most one per tool)
#   source_path  a workspace-relative path the step reads or lists
#   text         text content, e.g. a file's contents
ARG_KINDS = ("target_path", "source_path", "text")


@dataclass(frozen=True)
class ArgSpec:
    """One argument a tool takes. `max_length` counts UTF-8 bytes."""

    name: str
    kind: str
    required: bool
    max_length: int

    def __post_init__(self):
        check_plan_text(self.name, "ArgSpec.name")
        if not self.name:
            raise ValueError("ArgSpec.name must not be empty")
        if self.kind not in ARG_KINDS:
            raise ValueError(f"ArgSpec.kind must be one of {ARG_KINDS}, not {self.kind!r}")
        if type(self.required) is not bool:
            raise TypeError("ArgSpec.required must be a bool")
        if type(self.max_length) is not int or self.max_length < 1:
            raise ValueError("ArgSpec.max_length must be a positive int")


@dataclass(frozen=True)
class ToolInfo:
    """A description of a tool. Data only: it can't be used to run anything."""

    name: str
    description: str
    permission: Permission
    args: tuple
    produces_content: bool   # True if the tool writes text content (so content checks apply).

    def __post_init__(self):
        check_plan_text(self.name, "ToolInfo.name")
        check_plan_text(self.description, "ToolInfo.description")
        if not self.name or not self.description:
            raise ValueError("ToolInfo.name and ToolInfo.description must not be empty")
        if type(self.permission) is not Permission:
            raise TypeError(f"ToolInfo.permission must be a Permission, not {type(self.permission).__name__}")

        def arg_spec(item, name):
            if type(item) is not ArgSpec:
                raise TypeError(f"{name} must be an ArgSpec, not {type(item).__name__}")
            return item

        object.__setattr__(self, "args", plan_tuple(self.args, "ToolInfo.args", arg_spec))
        if type(self.produces_content) is not bool:
            raise TypeError("ToolInfo.produces_content must be a bool")
        names = [arg.name for arg in self.args]
        if len(set(names)) != len(names):
            raise ValueError(f"tool {self.name!r} has duplicate argument names")
        if sum(arg.kind == "target_path" for arg in self.args) > 1:
            raise ValueError(f"tool {self.name!r} has more than one target_path argument")

    def arg(self, kind):
        """The first argument of this kind, or None."""
        return next((arg for arg in self.args if arg.kind == kind), None)
