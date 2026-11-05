"""Shared data types for Orbisent 1.0 (ARCHITECTURE_1.0.md §5).

Every type here is immutable:
    - dataclasses are frozen, so fields can't be reassigned;
    - sequences are stored as tuples;
    - mappings are stored as FrozenMap: a private copy that can't be changed.

Because of that, these types can be compared, hashed, copied, deep-copied,
pickled and passed to dataclasses.asdict().

Mapping values are limited to JSON-compatible scalars of exactly these types:
str, int, bool and None. Floats and subclasses (such as IntEnum) are rejected.
That keeps them safe to print, compare and, later, hash (§4.8.4).

Only the types used by the components implemented so far live here. The other
types listed in §5 are added together with the components that use them.

Plan data (ARTIF. 1.0 Planning Foundation, ARCHITECTURE_1.0.md §12)
-------------------------------------------------------------------
CheckSpec, PlanGoal, ResolvedParameter, PlanStep, Plan, MissingInput,
ValidationIssue and PlanningResult follow stricter value rules, so that a
future canonical serializer can represent them deterministically (§12.4):

    - str: exact type, valid Unicode (no lone surrogates), NFC-normalised, and
      no control characters except newline and tab. Two fields that keep the
      user's exact input are exempt from NFC and control-character rules:
      PlanGoal.original_text and MissingInput.original / .lost;
    - int: exact type (not bool), between -(2**53 - 1) and 2**53 - 1;
    - bool, and None only where a field is declared optional;
    - tuples (a list is copied into a tuple; sets, generators and strings are
      rejected because they have no defined order or are not sequences);
    - FrozenMap with str keys and the scalar values above.

Floats are never allowed in plan data. GoalSpec.confidence stays a float; it is
converted exactly once, by PlanGoal.from_goal_spec(), into an integer number of
thousandths (confidence_to_permille). Unsupported values are rejected, never
coerced.

Python's built-in hash() is for in-memory sets and dicts only. It changes
between processes and is never a persistent plan identity.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Optional, Tuple

# Exact types only: bool is listed separately because it is not "an int" for our purposes,
# and subclasses (str subclasses, IntEnum, ...) could change how values print or compare.
SCALAR_TYPES = (str, int, bool, type(None))


class FrozenMap(Mapping):
    """A small immutable mapping from strings to scalar values.

    It behaves like a read-only dict: m["key"], len(m), iteration in insertion
    order, and equality with any mapping that has the same items. Unlike a dict
    or a MappingProxyType, it is hashable, and it can be deep-copied and pickled.

        m = FrozenMap({"project_name": "bakery"})
        m["project_name"]         # "bakery"
        m["x"] = 1                # TypeError: no item assignment
    """

    __slots__ = ("_data", "_hash")

    def __init__(self, data=None, *, name="FrozenMap"):
        """Copy `data` after checking it. `name` is only used in error messages."""
        if data is None:
            data = {}
        if not isinstance(data, Mapping):
            raise TypeError(f"{name} must be a mapping, not {type(data).__name__}")
        copied = {}
        for key, value in data.items():
            if type(key) is not str:
                raise TypeError(f"{name} keys must be strings, not {type(key).__name__}")
            if type(value) not in SCALAR_TYPES:
                raise TypeError(f"{name}[{key!r}] must be str, int, bool or None, not {type(value).__name__}")
            copied[key] = value
        object.__setattr__(self, "_data", copied)
        object.__setattr__(self, "_hash", None)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __eq__(self, other):
        if not isinstance(other, Mapping):
            return NotImplemented
        return self._data == dict(other.items())

    def __hash__(self):
        # Order-independent, like equality. Computed once; safe because the data can't change.
        if self._hash is None:
            object.__setattr__(self, "_hash", hash(frozenset(self._data.items())))
        return self._hash

    def __setattr__(self, name, value):
        raise AttributeError("FrozenMap is immutable")

    def __delattr__(self, name):
        raise AttributeError("FrozenMap is immutable")

    def __reduce__(self):
        # Used by pickle, copy.copy and copy.deepcopy: rebuild from a plain dict of scalars.
        return (FrozenMap, (dict(self._data),))

    def __repr__(self):
        return f"FrozenMap({self._data!r})"


def frozen_strings(value, name):
    """Return `value` as a tuple of strings, after checking every item."""
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of strings, not a single {type(value).__name__}")
    items = tuple(value)
    for item in items:
        if type(item) is not str:
            raise TypeError(f"{name} must contain only strings, not {type(item).__name__}")
    return items


@dataclass(frozen=True)
class Request:
    """What the user asked for, exactly as it arrived (CLI -> Core)."""

    text: str                            # The goal as typed. Not cleaned here.
    explicit_task: Optional[str] = None  # The value of --task, if given.
    options: FrozenMap = field(default_factory=FrozenMap)  # Other CLI options, e.g. {"name": "bakery"}.

    def __post_init__(self):
        if type(self.text) is not str:
            raise TypeError(f"text must be a string, not {type(self.text).__name__}")
        if self.explicit_task is not None and type(self.explicit_task) is not str:
            raise TypeError(f"explicit_task must be a string or None, not {type(self.explicit_task).__name__}")
        # Frozen dataclasses block normal assignment; object.__setattr__ is the standard way to
        # normalise a field once, during construction.
        object.__setattr__(self, "options", FrozenMap(self.options, name="options"))


def normalize_goal_text(original):
    """The normalised form of a goal: Unicode NFC, then whitespace collapsed to single spaces.

    Only NFC is used (never NFKC), so no character is replaced by a "compatible"
    look-alike. Collapsing whitespace is the rule goal understanding already used
    (0.2 clean_goal). The result is always NFC.
    """
    if type(original) is not str:
        raise TypeError(f"goal text must be a string, not {type(original).__name__}")
    return " ".join(unicodedata.normalize("NFC", original).split())


@dataclass(frozen=True)
class GoalSpec:
    """A goal turned into structure, or a clear statement that it couldn't be (ARCHITECTURE_1.0.md §4.3).

    A GoalSpec is either *decided* (task_type is set) or *unclear* (task_type is None,
    with at least one reason in `ambiguities`). Use GoalSpec.decided() or
    GoalSpec.unclear() to build one.

    `original_text` is the user's input exactly as it arrived (Request.text), with its
    original spacing and Unicode form. `text` must equal normalize_goal_text(original_text),
    so the two can never drift apart.
    """

    text: str                            # NFC-normalised goal text, whitespace collapsed.
    original_text: str                   # Request.text, exactly as given.
    task_type: Optional[str]             # e.g. "prepare_project"; None means "needs clarification".
    source: str                          # "explicit" | "rules" | "chain"; later "model:<provider>".
    confidence: float = 0.0              # 1.0 for an exact rule or explicit match; 0.0 when unclear.
    parameters: FrozenMap = field(default_factory=FrozenMap)  # e.g. {"project_name": "bakery"}.
    ambiguities: Tuple[str, ...] = ()    # Why it couldn't decide; shown to the user.
    candidates: Tuple[str, ...] = ()     # Task types that matched, when more than one did.

    def __post_init__(self):
        if type(self.text) is not str:
            raise TypeError(f"text must be a string, not {type(self.text).__name__}")
        if type(self.original_text) is not str:
            raise TypeError(f"original_text must be a string, not {type(self.original_text).__name__}")
        if self.text != normalize_goal_text(self.original_text):
            raise ValueError("text must equal normalize_goal_text(original_text)")
        if self.task_type is not None and not (type(self.task_type) is str and self.task_type):
            raise TypeError("task_type must be a non-empty string or None")
        if not (type(self.source) is str and self.source):
            raise TypeError("source must be a non-empty string")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a number")
        if not 0.0 <= self.confidence <= 1.0:  # Also rejects NaN.
            raise ValueError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "parameters", FrozenMap(self.parameters, name="parameters"))
        object.__setattr__(self, "ambiguities", frozen_strings(self.ambiguities, "ambiguities"))
        object.__setattr__(self, "candidates", frozen_strings(self.candidates, "candidates"))

        # The two shapes a GoalSpec may have. Anything in between is a bug.
        if self.task_type is None:
            if not self.ambiguities:
                raise ValueError("an unclear GoalSpec must explain why (ambiguities is empty)")
            if self.confidence != 0.0:
                raise ValueError("an unclear GoalSpec must have confidence 0.0")
        else:
            if self.ambiguities or self.candidates:
                raise ValueError("a decided GoalSpec can't have ambiguities or candidates")
            if self.confidence == 0.0:
                raise ValueError("a decided GoalSpec must have confidence above 0.0")

    @property
    def needs_clarification(self):
        return self.task_type is None

    @classmethod
    def decided(cls, text, task_type, source, confidence=1.0, parameters=None, *, original_text):
        return cls(text=text, original_text=original_text, task_type=task_type, source=source,
                   confidence=confidence, parameters=parameters or {})

    @classmethod
    def unclear(cls, text, source, reasons, candidates=(), *, original_text):
        return cls(text=text, original_text=original_text, task_type=None, source=source,
                   confidence=0.0, ambiguities=tuple(reasons), candidates=tuple(candidates))


# ---------------------------------------------------------------------------
# Plan data (ARTIF. 1.0 Planning Foundation, ARCHITECTURE_1.0.md §12)
# ---------------------------------------------------------------------------

MAX_PLAN_INT = 2 ** 53 - 1  # Largest integer every JSON implementation represents exactly.

# Every code the PlanValidator can report (§12.5). ValidationIssue accepts no other code.
ISSUE_CODES = frozenset({
    "unsupported_format", "no_steps", "too_many_steps", "bad_source", "parameter_order",
    "duplicate_parameter", "bad_provenance", "unknown_rule", "plan_too_large", "bad_step_id",
    "duplicate_step_id", "bad_description", "expected_mismatch", "unknown_tool", "permission_denied",
    "missing_arg", "unexpected_arg", "bad_arg", "content_too_large", "path_invalid", "duplicate_target",
    "unknown_dependency", "self_dependency", "forward_dependency", "dependency_order",
    "missing_dependency", "order_violation", "bad_check", "check_on_non_content_step",
    "too_many_checks", "bad_acceptance", "bad_goal",
})

PROVENANCES = ("user", "derived", "default", "context")
PLANNING_STATUSES = ("planned", "needs_input", "refused")


def check_plan_text(value, name, *, exact_input=False):
    """Check a string against the plan value rules and return it unchanged.

    exact_input=True is for fields that preserve the user's input exactly: they only
    need to be valid Unicode (no lone surrogates), so they can be encoded as UTF-8.
    """
    if type(value) is not str:
        raise TypeError(f"{name} must be a string, not {type(value).__name__}")
    for char in value:
        if 0xD800 <= ord(char) <= 0xDFFF:
            raise ValueError(f"{name} contains a lone surrogate (U+{ord(char):04X}), which is not valid Unicode")
    if exact_input:
        return value
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must be NFC-normalised Unicode")
    for char in value:
        if unicodedata.category(char) == "Cc" and char not in "\n\t":
            raise ValueError(f"{name} contains a control character (U+{ord(char):04X})")
    return value


def check_plan_int(value, name):
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if not -MAX_PLAN_INT <= value <= MAX_PLAN_INT:
        raise ValueError(f"{name} must be between -(2**53 - 1) and 2**53 - 1")
    return value


def check_plan_scalar(value, name):
    """A str, int, bool or None that follows the plan value rules."""
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return check_plan_int(value, name)
    if type(value) is str:
        return check_plan_text(value, name)
    raise TypeError(f"{name} must be str, int, bool or None, not {type(value).__name__}")


def check_plan_map(value, name):
    """Copy `value` into a FrozenMap whose keys and values follow the plan value rules."""
    frozen = FrozenMap(value, name=name)
    for key, item in frozen.items():
        check_plan_text(key, f"{name} key")
        check_plan_scalar(item, f"{name}[{key!r}]")
    return frozen


def plan_tuple(value, name, check_item):
    """Copy a list or tuple into a tuple, checking every item. Other iterables are rejected."""
    if type(value) not in (list, tuple):
        raise TypeError(f"{name} must be a list or tuple, not {type(value).__name__}")
    return tuple(check_item(item, f"{name}[{index}]") for index, item in enumerate(value))


def _instance_of(cls):
    def check(item, name):
        if type(item) is not cls:
            raise TypeError(f"{name} must be a {cls.__name__}, not {type(item).__name__}")
        return item
    return check


def _optional_text(value, name):
    return None if value is None else check_plan_text(value, name)


def confidence_to_permille(confidence):
    """Convert a 0.0-1.0 confidence into whole thousandths (0-1000), deterministically.

    Rule: take the shortest decimal that round-trips the float (repr), multiply by 1000
    in exact decimal arithmetic, and round to the nearest integer with halves rounded up
    (ROUND_HALF_UP). Examples: 0.85 -> 850, 0.0005 -> 1, 0.0004 -> 0, 0.9995 -> 1000.

    The decimal context is fixed here, so a caller's global decimal settings can't change
    the result. Only an exact int or float in [0, 1] is accepted: bool, NaN, infinities,
    -0.0 and values outside the range raise. Nothing is ever raised to a minimum: a tiny
    confidence becomes 0, and the validator reports it (bad_goal).
    """
    if type(confidence) not in (int, float):
        raise TypeError(f"confidence must be an int or float, not {type(confidence).__name__}")
    if type(confidence) is float:
        if not math.isfinite(confidence):
            raise ValueError("confidence must be a finite number")
        if math.copysign(1.0, confidence) < 0:
            raise ValueError("confidence must not be negative (including -0.0)")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_UP)):
        scaled = Decimal(repr(confidence)).scaleb(3)
        return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class CheckSpec:
    """A content check, stored as data: a kind and its parameters (never code).

    Which kinds exist and which parameters each one needs is the validator's job (§12.5).
    """

    kind: str
    params: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self):
        check_plan_text(self.kind, "CheckSpec.kind")
        object.__setattr__(self, "params", check_plan_map(self.params, "CheckSpec.params"))


@dataclass(frozen=True)
class PlanGoal:
    """The goal as recorded inside a plan. Built from a decided GoalSpec by from_goal_spec()."""

    text: str                   # NFC-normalised goal text (GoalSpec.text).
    original_text: str          # The user's exact input (GoalSpec.original_text).
    task_type: str
    source: str                 # Where the goal understanding came from, e.g. "rules".
    confidence_permille: int    # GoalSpec.confidence in thousandths; see confidence_to_permille().

    def __post_init__(self):
        check_plan_text(self.text, "PlanGoal.text")
        check_plan_text(self.original_text, "PlanGoal.original_text", exact_input=True)
        check_plan_text(self.task_type, "PlanGoal.task_type")
        check_plan_text(self.source, "PlanGoal.source")
        if not self.task_type:
            raise ValueError("PlanGoal.task_type must not be empty")
        if not self.source:
            raise ValueError("PlanGoal.source must not be empty")
        check_plan_int(self.confidence_permille, "PlanGoal.confidence_permille")

    @classmethod
    def from_goal_spec(cls, spec):
        """The one place where GoalSpec.confidence (float) becomes an integer.

        Raises ValueError for an unclear GoalSpec (there is nothing to plan) and for
        text that breaks the plan value rules. A confidence that rounds to 0 is kept as 0.
        """
        if type(spec) is not GoalSpec:
            raise TypeError(f"expected a GoalSpec, not {type(spec).__name__}")
        if spec.needs_clarification:
            raise ValueError("an unclear GoalSpec can't become a PlanGoal")
        return cls(text=spec.text, original_text=spec.original_text, task_type=spec.task_type,
                   source=spec.source, confidence_permille=confidence_to_permille(spec.confidence))


@dataclass(frozen=True)
class ResolvedParameter:
    """One plan parameter, with where its value came from (§12.6).

    Consistency between provenance and rule/rule_input/input_source is checked by the
    validator (bad_provenance), so that a model-made plan gets a clear issue code.
    """

    name: str
    value: object                         # Exactly str, int or bool.
    provenance: str                       # "user" | "derived" | "default" | "context"
    rule: Optional[str] = None            # e.g. "folder_name_from_text.v1" for derived values.
    rule_input: Optional[str] = None      # What the rule was applied to.
    input_source: Optional[str] = None    # e.g. "option:name", "goal.text", "context.today".

    def __post_init__(self):
        check_plan_text(self.name, "ResolvedParameter.name")
        if not self.name:
            raise ValueError("ResolvedParameter.name must not be empty")
        if self.value is None:
            raise TypeError("ResolvedParameter.value must be str, int or bool, not None")
        check_plan_scalar(self.value, "ResolvedParameter.value")
        if self.provenance not in PROVENANCES:
            raise ValueError(f"ResolvedParameter.provenance must be one of {PROVENANCES}, not {self.provenance!r}")
        _optional_text(self.rule, "ResolvedParameter.rule")
        _optional_text(self.rule_input, "ResolvedParameter.rule_input")
        _optional_text(self.input_source, "ResolvedParameter.input_source")


@dataclass(frozen=True)
class PlanStep:
    """One step of a plan: a call to a catalogued tool, with its expected outcome and checks."""

    id: str
    tool: str
    args: FrozenMap
    description: str
    expected: str
    depends_on: Tuple[str, ...] = ()
    checks: Tuple[CheckSpec, ...] = ()

    def __post_init__(self):
        for name in ("id", "tool", "description", "expected"):
            check_plan_text(getattr(self, name), f"PlanStep.{name}")
        object.__setattr__(self, "args", check_plan_map(self.args, "PlanStep.args"))
        object.__setattr__(self, "depends_on", plan_tuple(self.depends_on, "PlanStep.depends_on", check_plan_text))
        object.__setattr__(self, "checks", plan_tuple(self.checks, "PlanStep.checks", _instance_of(CheckSpec)))


@dataclass(frozen=True)
class Plan:
    """A complete plan. Steps run one at a time, in this order (ARTIF. 1.0 is linear)."""

    goal: PlanGoal
    parameters: Tuple[ResolvedParameter, ...]
    steps: Tuple[PlanStep, ...]
    acceptance: Tuple[CheckSpec, ...]
    source: str                 # "template:<task_type>" or "model:<id>"
    format_version: int = 1

    def __post_init__(self):
        _instance_of(PlanGoal)(self.goal, "Plan.goal")
        object.__setattr__(self, "parameters",
                           plan_tuple(self.parameters, "Plan.parameters", _instance_of(ResolvedParameter)))
        object.__setattr__(self, "steps", plan_tuple(self.steps, "Plan.steps", _instance_of(PlanStep)))
        object.__setattr__(self, "acceptance", plan_tuple(self.acceptance, "Plan.acceptance", _instance_of(CheckSpec)))
        check_plan_text(self.source, "Plan.source")
        check_plan_int(self.format_version, "Plan.format_version")


@dataclass(frozen=True)
class MissingInput:
    """Something the user must provide before a plan can be made. `original` keeps their exact input."""

    name: str
    question: str
    reason: str
    original: str
    lost: Tuple[str, ...] = ()   # Characters that a derivation could not keep.

    def __post_init__(self):
        for name in ("name", "question", "reason"):
            check_plan_text(getattr(self, name), f"MissingInput.{name}")
            if not getattr(self, name):
                raise ValueError(f"MissingInput.{name} must not be empty")
        check_plan_text(self.original, "MissingInput.original", exact_input=True)
        object.__setattr__(self, "lost", plan_tuple(
            self.lost, "MissingInput.lost", lambda item, name: check_plan_text(item, name, exact_input=True)))


@dataclass(frozen=True)
class ValidationIssue:
    """One problem the PlanValidator found. `code` is one of ISSUE_CODES."""

    code: str
    step_id: Optional[str]
    message: str

    def __post_init__(self):
        if self.code not in ISSUE_CODES:
            raise ValueError(f"unknown validation issue code {self.code!r}")
        _optional_text(self.step_id, "ValidationIssue.step_id")
        check_plan_text(self.message, "ValidationIssue.message")


@dataclass(frozen=True)
class PlanningResult:
    """The outcome of planning: exactly one of three shapes.

        planned      plan is set; missing, issues and reasons are empty
        needs_input  plan is None; at least one MissingInput
        refused      plan is None; at least one reason or issue
    """

    status: str
    plan: Optional[Plan] = None
    missing: Tuple[MissingInput, ...] = ()
    issues: Tuple[ValidationIssue, ...] = ()
    reasons: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.status not in PLANNING_STATUSES:
            raise ValueError(f"status must be one of {PLANNING_STATUSES}, not {self.status!r}")
        if self.plan is not None:
            _instance_of(Plan)(self.plan, "PlanningResult.plan")
        object.__setattr__(self, "missing", plan_tuple(self.missing, "PlanningResult.missing", _instance_of(MissingInput)))
        object.__setattr__(self, "issues", plan_tuple(self.issues, "PlanningResult.issues", _instance_of(ValidationIssue)))
        object.__setattr__(self, "reasons", plan_tuple(self.reasons, "PlanningResult.reasons", check_plan_text))
        if self.status == "planned":
            if self.plan is None or self.missing or self.issues or self.reasons:
                raise ValueError("a planned result must have a plan and nothing else")
        elif self.plan is not None:
            raise ValueError(f"a {self.status} result must not have a plan")
        elif self.status == "needs_input":
            if not self.missing or self.issues or self.reasons:
                raise ValueError("a needs_input result must list what is missing, and nothing else")
        elif self.missing or not (self.issues or self.reasons):
            raise ValueError("a refused result must give at least one reason or issue, and no missing inputs")

    @classmethod
    def planned(cls, plan):
        return cls(status="planned", plan=plan)

    @classmethod
    def needs_input(cls, missing):
        return cls(status="needs_input", missing=tuple(missing))

    @classmethod
    def refused(cls, reasons=(), issues=()):
        return cls(status="refused", reasons=tuple(reasons), issues=tuple(issues))
