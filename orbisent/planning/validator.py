"""The PlanValidator: the single gatekeeper for every plan (ARCHITECTURE_1.0.md §12.5).

Every plan, whatever produced it (a template now, a model later), must pass this
validator before anyone is asked to approve it. The validator:

    - is pure: it reads only the plan and the configuration it was given. It never
      touches the file system, the network or the clock, and it can't run tools
      (it only ever sees ToolInfo descriptors, which hold no code);
    - is deterministic: the same plan and configuration always give the same issues,
      in the same order;
    - never raises for a bad plan: it returns ValidationIssue objects with stable codes.
      It raises only when it is itself misconfigured or not given a Plan.

Issue order: plan-level issues, then each step in plan order, then acceptance checks.
"""

import re
from dataclasses import dataclass
from types import MappingProxyType

from orbisent.safety.sandbox import check_relative_path, parent_keys, path_key
from orbisent.tools.base import Permission, ToolInfo
from orbisent.tools.filesystem import FILESYSTEM_TOOLS, MAX_CONTENT_BYTES
from orbisent.types import (
    CheckSpec,
    Plan,
    PlanningResult,
    PlanStep,
    ValidationIssue,
    check_plan_text,
    normalize_goal_text,
)

FORMAT_VERSION = 1

STEP_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")

# Check kinds and the exact parameters each one takes. Values are parameter "types":
#   line     a non-empty, single-line str
#   pattern  one of COUNT_PATTERNS (a name, never a regular expression)
#   count    an int from 1 to MAX_MIN_COUNT
CHECK_KINDS = MappingProxyType({
    "not_empty": MappingProxyType({}),
    "title_starts_with": MappingProxyType({"text": "line"}),
    "contains": MappingProxyType({"text": "line"}),
    "min_count": MappingProxyType({"pattern": "pattern", "minimum": "count"}),
})

# Named counting patterns and how they are described (the same words as Prototype 0.3).
COUNT_PATTERNS = MappingProxyType({
    "checkbox": "checkbox items",
    "numbered_step": "numbered steps",
    "section": "'## ' sections",
})
MAX_MIN_COUNT = 10_000

# The only descriptors a validator may be configured with: the built-in ones. A catalog is
# any subset of them. Anything else (a renamed or altered descriptor) is a configuration error.
BUILTIN_TOOLS = MappingProxyType({tool.name: tool for tool in FILESYSTEM_TOOLS})


@dataclass(frozen=True)
class ValidationLimits:
    """Size limits (§12.5). The defaults are the ARTIF. 1.0 limits."""

    max_steps: int = 50
    max_content_bytes: int = MAX_CONTENT_BYTES      # One file's content, UTF-8.
    max_plan_bytes: int = 1024 * 1024               # All text in the plan, UTF-8.
    max_checks_per_step: int = 20
    max_description_chars: int = 200

    def __post_init__(self):
        for name in ("max_steps", "max_content_bytes", "max_plan_bytes", "max_checks_per_step",
                     "max_description_chars"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"ValidationLimits.{name} must be a positive int")


def controlled_catalog(catalog):
    """Check a catalog and return it as a read-only {name: ToolInfo} mapping.

    A catalog is a list or tuple of ToolInfo. Every entry must be one of the built-in
    descriptors exactly, with no duplicates. Raises ValueError or TypeError otherwise.
    """
    if type(catalog) not in (list, tuple):
        raise TypeError(f"catalog must be a list or tuple of ToolInfo, not {type(catalog).__name__}")
    tools = {}
    for tool in catalog:
        if type(tool) is not ToolInfo:
            raise TypeError(f"catalog entries must be ToolInfo, not {type(tool).__name__}")
        if tool.name in tools:
            raise ValueError(f"catalog lists {tool.name!r} twice")
        if BUILTIN_TOOLS.get(tool.name) != tool:
            raise ValueError(f"{tool.name!r} is not a built-in tool descriptor; only those are allowed")
        tools[tool.name] = tool
    return MappingProxyType(tools)


def describe_expected(step, catalog):
    """The exact "Expected:" text for a step, generated from its tool and checks.

    `catalog` is a list or tuple of ToolInfo (see controlled_catalog). The step must be
    structurally valid (known tool, string path, valid checks); otherwise ValueError.
    The wording matches Prototype 0.3's describe_checks.
    """
    if type(step) is not PlanStep:
        raise TypeError(f"expected a PlanStep, not {type(step).__name__}")
    tools = controlled_catalog(catalog)
    if step.tool not in tools:
        raise ValueError(f"tool {step.tool!r} is not in the catalog")
    path = step.args.get("path")
    if type(path) is not str:
        raise ValueError("the step has no string 'path' argument")

    if step.tool == "fs.create_folder":
        return f"{path} exists and is a folder"
    if step.tool == "fs.read_file":
        return f"{path} exists and is a file that can be read"
    if step.tool == "fs.list_dir":
        return f"{path} exists and is a folder that can be listed"

    # fs.create_file: the file's name, then each check, in order.
    name = path.rsplit("/", 1)[-1]
    if not step.checks:
        return f"{name} exists and is a file"
    parts = [f"{name} exists"]
    contained = []
    for check in step.checks:
        if _check_problems(check):
            raise ValueError("the step has an invalid check")
        if check.kind == "contains":
            contained.append(repr(check.params["text"]))
            continue
        if contained:  # A run of "contains" checks is described together, as in 0.3.
            parts.append("it contains " + ", ".join(contained))
            contained = []
        if check.kind == "not_empty":
            parts.append("it is not empty")
        elif check.kind == "title_starts_with":
            parts.append(f"its first line starts with {check.params['text']!r}")
        else:
            parts.append(f"at least {check.params['minimum']} {COUNT_PATTERNS[check.params['pattern']]}")
    if contained:
        parts.append("it contains " + ", ".join(contained))
    return "; ".join(parts)


def _check_problems(check, extra=()):
    """Problems with one CheckSpec's kind and parameters, ignoring the parameter names in `extra`."""
    if check.kind not in CHECK_KINDS:
        return [f"unknown check kind {ascii(check.kind)}"]
    wanted = CHECK_KINDS[check.kind]
    given = {key: value for key, value in check.params.items() if key not in extra}
    problems = []
    for key in sorted(set(wanted) - set(given)):
        problems.append(f"{check.kind} needs a {ascii(key)} parameter")
    for key in sorted(set(given) - set(wanted)):
        problems.append(f"{check.kind} takes no {ascii(key)} parameter")
    for key, kind in wanted.items():
        if key not in given:
            continue
        value = given[key]
        if kind == "line" and not (type(value) is str and value.strip() and "\n" not in value):
            problems.append(f"{check.kind}.{key} must be a non-empty single line of text")
        elif kind == "pattern" and value not in COUNT_PATTERNS:
            problems.append(f"{check.kind}.{key} must be one of {', '.join(COUNT_PATTERNS)}")
        elif kind == "count" and not (type(value) is int and 1 <= value <= MAX_MIN_COUNT):
            problems.append(f"{check.kind}.{key} must be an int from 1 to {MAX_MIN_COUNT}")
    return problems


def _text_bytes(plan):
    """The total UTF-8 size of every string in the plan (used for plan_too_large)."""
    total = 0

    def add(value):
        nonlocal total
        if type(value) is str:
            total += len(value.encode("utf-8"))

    goal = plan.goal
    for value in (goal.text, goal.original_text, goal.task_type, goal.source, plan.source):
        add(value)
    for parameter in plan.parameters:
        for value in (parameter.name, parameter.value, parameter.rule, parameter.rule_input,
                      parameter.input_source):
            add(value)
    checks = list(plan.acceptance)
    for step in plan.steps:
        for value in (step.id, step.tool, step.description, step.expected, *step.depends_on):
            add(value)
        for key, value in step.args.items():
            add(key)
            add(value)
        checks.extend(step.checks)
    for check in checks:
        add(check.kind)
        for key, value in check.params.items():
            add(key)
            add(value)
    return total


class PlanValidator:
    """Checks plans against the catalog, the allowed permissions, known rules and limits."""

    def __init__(self, catalog, allowed_permissions, known_rules=frozenset(), limits=None):
        self._tools = controlled_catalog(catalog)
        if type(allowed_permissions) not in (set, frozenset):
            raise TypeError("allowed_permissions must be a set or frozenset of Permission")
        if not all(type(permission) is Permission for permission in allowed_permissions):
            raise TypeError("allowed_permissions must contain only Permission values")
        self._allowed = frozenset(allowed_permissions)
        if type(known_rules) not in (set, frozenset):
            raise TypeError("known_rules must be a set or frozenset of rule ids")
        for rule in known_rules:
            check_plan_text(rule, "known rule id")
            if not rule:
                raise ValueError("known rule ids must not be empty")
        self._rules = frozenset(known_rules)
        limits = ValidationLimits() if limits is None else limits
        if type(limits) is not ValidationLimits:
            raise TypeError("limits must be a ValidationLimits")
        self._limits = limits

    @property
    def catalog(self):
        return tuple(self._tools.values())

    @property
    def allowed_permissions(self):
        return self._allowed

    @property
    def known_rules(self):
        return self._rules

    @property
    def limits(self):
        return self._limits

    def result(self, plan):
        """PlanningResult.planned(plan) if the plan is valid, otherwise refused with every issue."""
        issues = self.validate(plan)
        return PlanningResult.refused(issues=issues) if issues else PlanningResult.planned(plan)

    def validate(self, plan):
        """Every problem with the plan, as a tuple of ValidationIssue. () means it is valid."""
        if type(plan) is not Plan:
            raise TypeError(f"expected a Plan, not {type(plan).__name__}")
        issues = []

        def report(code, step_id, message):
            issues.append(ValidationIssue(code, step_id, message))

        self._plan_level(plan, report)
        positions = {}  # Step id -> position of its first occurrence.
        for index, step in enumerate(plan.steps):
            positions.setdefault(step.id, index)
        targets = self._targets(plan)
        producers = {}  # Path key -> position of the first step that targets it.
        for index, key in enumerate(targets):
            if key is not None:
                producers.setdefault(key, index)
        reachable = []  # For each position: positions it depends on, directly or indirectly.
        for index, step in enumerate(plan.steps):
            reachable.append(self._step(plan, index, step, positions, targets, producers, reachable, report))
        self._acceptance(plan, producers, report)
        return tuple(issues)

    # -- plan level ----------------------------------------------------------

    def _plan_level(self, plan, report):
        if plan.format_version != FORMAT_VERSION:
            report("unsupported_format", None,
                   f"format_version {plan.format_version} is not supported (only {FORMAT_VERSION})")
        if not plan.steps:
            report("no_steps", None, "the plan has no steps")
        elif len(plan.steps) > self._limits.max_steps:
            report("too_many_steps", None,
                   f"the plan has {len(plan.steps)} steps; at most {self._limits.max_steps} are allowed")

        prefix, _, rest = plan.source.partition(":")
        if prefix == "template":
            if rest != plan.goal.task_type:
                report("bad_source", None,
                       f"source {ascii(plan.source)} must be 'template:{plan.goal.task_type}' for this goal")
        elif not (prefix == "model" and rest):
            report("bad_source", None, f"source {ascii(plan.source)} must be 'template:<task>' or 'model:<id>'")

        goal = plan.goal
        if not 1 <= goal.confidence_permille <= 1000:
            report("bad_goal", None,
                   f"confidence_permille {goal.confidence_permille} must be from 1 to 1000 for a decided goal")
        if goal.text != normalize_goal_text(goal.original_text):
            report("bad_goal", None, "goal text must equal the normalised form of the original text")

        seen, previous = set(), None
        for parameter in plan.parameters:
            name = parameter.name
            if name in seen:
                report("duplicate_parameter", None, f"parameter {ascii(name)} is listed more than once")
            elif previous is not None and name < previous:
                report("parameter_order", None, f"parameter {ascii(name)} is out of order (sort by name)")
            seen.add(name)
            previous = name
            self._provenance(parameter, report)

        size = _text_bytes(plan)
        if size > self._limits.max_plan_bytes:
            report("plan_too_large", None,
                   f"the plan holds {size} bytes of text; at most {self._limits.max_plan_bytes} are allowed")

    def _provenance(self, parameter, report):
        name, kind = ascii(parameter.name), parameter.provenance
        rule, rule_input, source = parameter.rule, parameter.rule_input, parameter.input_source
        if kind == "derived":
            if not rule or rule_input is None:
                report("bad_provenance", None, f"derived parameter {name} must name its rule and rule_input")
            elif rule not in self._rules:
                report("unknown_rule", None, f"parameter {name} uses unknown rule {ascii(rule)}")
        elif kind == "context":
            if not source or rule is not None or rule_input is not None:
                report("bad_provenance", None,
                       f"context parameter {name} must have an input_source and no rule or rule_input")
        elif rule is not None or rule_input is not None:  # "user" and "default"
            report("bad_provenance", None, f"{kind} parameter {name} must not have a rule or rule_input")

    # -- steps ---------------------------------------------------------------

    def _targets(self, plan):
        """For each step: the path key of its target, or None if it has no valid target."""
        keys = []
        for step in plan.steps:
            tool = self._tools.get(step.tool)
            arg = tool.arg("target_path") if tool else None
            value = step.args.get(arg.name) if arg else None
            valid = (type(value) is str and len(value.encode("utf-8")) <= arg.max_length
                     and not check_relative_path(value)) if arg else False
            keys.append(path_key(value) if valid else None)
        return keys

    def _step(self, plan, index, step, positions, targets, producers, reachable, report):
        """Report this step's issues and return the positions it depends on, directly or indirectly."""
        sid = step.id
        if not STEP_ID_PATTERN.fullmatch(step.id):
            report("bad_step_id", sid, f"step id {ascii(sid)} must match [a-z][a-z0-9_-]{{0,31}}")
        if positions[sid] != index:
            report("duplicate_step_id", sid, f"step id {ascii(sid)} is used by an earlier step")

        description = step.description
        if not description.strip() or "\n" in description \
                or len(description) > self._limits.max_description_chars:
            report("bad_description", sid, "description must be one non-empty line of at most "
                                           f"{self._limits.max_description_chars} characters")

        tool = self._tools.get(step.tool)
        describable = tool is not None
        if tool is None:
            report("unknown_tool", sid, f"tool {ascii(step.tool)} is not in the catalog")
        else:
            if tool.permission not in self._allowed:
                report("permission_denied", sid,
                       f"tool {ascii(tool.name)} needs the {tool.permission.value} permission, which is not allowed")
            describable = self._args(step, tool, report) and describable

        if targets[index] is not None and producers[targets[index]] != index:
            first = plan.steps[producers[targets[index]]]
            report("duplicate_target", sid, f"step {ascii(first.id)} already targets the same path")

        if len(step.checks) > self._limits.max_checks_per_step:
            report("too_many_checks", sid,
                   f"the step has {len(step.checks)} checks; at most {self._limits.max_checks_per_step} are allowed")
        if step.checks and tool is not None and not tool.produces_content:
            report("check_on_non_content_step", sid, f"tool {ascii(tool.name)} produces no content to check")
            describable = False
        for number, check in enumerate(step.checks, start=1):
            problems = _check_problems(check)
            if problems:
                report("bad_check", sid, f"check {number}: " + "; ".join(problems))
                describable = False

        if describable and step.expected != describe_expected(step, self.catalog):
            report("expected_mismatch", sid, "expected must equal the description generated from the "
                                             f"tool and checks: {ascii(describe_expected(step, self.catalog))}")

        return self._dependencies(plan, index, step, positions, targets, producers, reachable, report)

    def _args(self, step, tool, report):
        """Report argument issues. Returns True if every path argument is a usable string."""
        sid, usable = step.id, True
        names = {arg.name for arg in tool.args}
        for arg in tool.args:
            if arg.name not in step.args:
                if arg.required:
                    report("missing_arg", sid, f"tool {ascii(tool.name)} needs argument {ascii(arg.name)}")
                    usable = usable and arg.kind == "text"
                continue
            value = step.args[arg.name]
            if type(value) is not str:
                report("bad_arg", sid, f"argument {ascii(arg.name)} must be a string")
                usable = usable and arg.kind == "text"
                continue
            size = len(value.encode("utf-8"))
            if arg.kind == "text":
                limit = min(arg.max_length, self._limits.max_content_bytes)
                if size > limit:
                    report("content_too_large", sid,
                           f"argument {ascii(arg.name)} holds {size} bytes; at most {limit} are allowed")
            elif size > arg.max_length:
                report("bad_arg", sid, f"argument {ascii(arg.name)} is longer than {arg.max_length} UTF-8 bytes")
                usable = False
            else:
                problems = check_relative_path(value)
                if problems:
                    report("path_invalid", sid, f"argument {ascii(arg.name)}: " + "; ".join(problems))
                    usable = False
        for name in sorted(set(step.args) - names):
            report("unexpected_arg", sid, f"tool {ascii(tool.name)} takes no argument {ascii(name)}")
        return usable

    def _dependencies(self, plan, index, step, positions, targets, producers, reachable, report):
        sid = step.id
        direct = []
        for dep in step.depends_on:
            if dep == sid:
                report("self_dependency", sid, "a step can't depend on itself")
            elif dep not in positions:
                report("unknown_dependency", sid, f"depends on unknown step {ascii(dep)}")
            elif positions[dep] > index:
                report("forward_dependency", sid, f"depends on later step {ascii(dep)}; only earlier steps are allowed")
            else:
                direct.append(positions[dep])
        if len(set(step.depends_on)) != len(step.depends_on) or direct != sorted(set(direct)):
            report("dependency_order", sid, "depends_on must list each step once, in plan order")

        closure = set(direct)
        for position in direct:
            closure |= reachable[position]

        # Completeness: every earlier step that creates a folder this step works inside (or, for a
        # source path, the path itself) must be a dependency; one created later is an order violation.
        tool = self._tools.get(step.tool)
        needed = []
        for kind in ("target_path", "source_path"):
            arg = tool.arg(kind) if tool else None
            value = step.args.get(arg.name) if arg else None
            if arg is None or type(value) is not str or len(value.encode("utf-8")) > arg.max_length \
                    or check_relative_path(value):
                continue
            needed.extend(parent_keys(value))
            if kind == "source_path":
                needed.append(path_key(value))
        reported = set()
        for key in needed:
            producer = producers.get(key)
            if producer is None or producer == index or producer in reported:
                continue
            reported.add(producer)
            path = "/".join(key)
            other = plan.steps[producer].id
            if producer > index:
                report("order_violation", sid, f"uses {ascii(path)}, which later step {ascii(other)} creates")
            elif producer not in closure:
                report("missing_dependency", sid,
                       f"uses {ascii(path)}, which step {ascii(other)} creates, but doesn't depend on it")
        return frozenset(closure)

    # -- acceptance ----------------------------------------------------------

    def _acceptance(self, plan, producers, report):
        for number, check in enumerate(plan.acceptance, start=1):
            target = check.params.get("target")
            if type(target) is not str or check_relative_path(target) or path_key(target) not in producers:
                report("bad_acceptance", None,
                       f"acceptance check {number} must name a 'target' path that a step creates")
            problems = _check_problems(check, extra=("target",))
            if problems:
                report("bad_check", None, f"acceptance check {number}: " + "; ".join(problems))
