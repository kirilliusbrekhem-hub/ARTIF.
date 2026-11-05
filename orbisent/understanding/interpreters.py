"""Deterministic goal interpreters (ARCHITECTURE_1.0.md §4.3).

An interpreter looks at a Request and returns one of:

    None              "This doesn't apply to me; ask the next interpreter."
    a decided GoalSpec   "The goal is this task type."
    an unclear GoalSpec  "I can't decide, and nobody should guess." (stops the chain)

The ChainedInterpreter asks its interpreters in order and returns the first
GoalSpec it gets. The default chain is ExplicitInterpreter, then RuleInterpreter,
which reproduces Prototype 0.3 exactly:

    - an explicit --task always wins over keywords;
    - otherwise exactly one keyword match decides the task type;
    - no match, or more than one, means "needs clarification". Nothing is guessed.

Because an unclear answer stops the chain, an invalid --task can never fall
through to the keyword rules.

Every interpreter refuses an empty goal in the same way, whether it is used on
its own or inside a chain. Each one checks its configuration when it is built
and keeps it read-only afterwards.

Every GoalSpec keeps the user's exact input in `original_text` (Request.text).
Its `text`, which keywords are matched against, is normalize_goal_text(original):
Unicode NFC, then whitespace collapsed. For NFC input (almost all real input)
this is exactly Prototype 0.3's clean_goal; for decomposed input, canonically
equivalent text is now classified the same way as its NFC form.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Optional, Protocol, Sequence

from orbisent.types import GoalSpec, Request, normalize_goal_text

# The task types Orbisent knows, with the keywords that point to each one.
# Copied from Prototype 0.3 (prototype/orbisent_agent.py, TASK_TYPES). The order matters:
# when a goal matches several task types, they are listed in this order.
# tests/test_understanding.py checks that this table still matches the prototype.
DEFAULT_KEYWORDS = MappingProxyType({
    "prepare_project": ("project folder", "project directory", "prepare my project", "prepare the project",
                        "prepare project", "set up my project", "set up a project", "setup project",
                        "new project", "scaffold"),
    "create_launch_checklist": ("checklist", "checklists", "check list"),
})

# The reason given for an empty goal, by every interpreter.
EMPTY_GOAL_REASON = "the goal must not be empty"

# A keyword is one or more lowercase ASCII words separated by single spaces. Anything else could never
# match the normalised goal text, so it is rejected when an interpreter is built.
_KEYWORD_PATTERN = re.compile(r"[a-z0-9]+( [a-z0-9]+)*")


def clean_goal(text):
    """Collapse all whitespace (including newlines) so the goal fits on one line (from 0.2).

    Kept for compatibility. Interpreters use normalize_goal_text(), which also applies NFC.
    """
    return " ".join(text.split())


def refuse_empty(text, source, *, original_text):
    """Return an unclear GoalSpec if the normalised goal text is empty, otherwise None."""
    if text:
        return None
    return GoalSpec.unclear(text, source, [EMPTY_GOAL_REASON], original_text=original_text)


def _words(text):
    """Lowercase the text and turn every run of non-letters/digits into one space, padded at both ends.

    "Set up a Project!" -> " set up a project ". Padding lets keywords match whole words only.
    """
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "


def _task_type_names(value, name):
    """Check a sequence of task type names: non-empty, exact strings, no duplicates."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a list or tuple of strings, not {type(value).__name__}")
    names = tuple(value)
    if not names:
        raise ValueError(f"{name} must not be empty")
    for item in names:
        if type(item) is not str:
            raise TypeError(f"{name} must contain only strings, not {type(item).__name__}")
        if not item:
            raise ValueError(f"{name} must not contain an empty name")
    if len(set(names)) != len(names):
        raise ValueError(f"{name} contains duplicates")
    return names


class GoalInterpreter(Protocol):
    def interpret(self, request: Request) -> Optional[GoalSpec]: ...


class ExplicitInterpreter:
    """Uses the task type the user chose with --task.

    Returns None when no task type was given, so the next interpreter can try.
    An empty goal or an unknown task type is refused with an unclear GoalSpec,
    which stops the chain.
    """

    source = "explicit"

    def __init__(self, task_types: Sequence[str] = tuple(DEFAULT_KEYWORDS)):
        self._task_types = _task_type_names(task_types, "task_types")

    @property
    def task_types(self):
        return self._task_types

    def interpret(self, request):
        if request.explicit_task is None:
            return None
        text = normalize_goal_text(request.text)
        empty = refuse_empty(text, self.source, original_text=request.text)
        if empty:
            return empty
        if request.explicit_task not in self._task_types:
            known = ", ".join(self._task_types)
            return GoalSpec.unclear(text, self.source,
                                    [f"unknown task type {request.explicit_task!r} (known: {known})"],
                                    original_text=request.text)
        return GoalSpec.decided(text, request.explicit_task, self.source, original_text=request.text)


class RuleInterpreter:
    """Prototype 0.3's keyword matcher (classify_goal), unchanged in behaviour.

    Keywords must match whole words: "checklist" matches "a checklist" but not
    "checklisting". Exactly one matching task type decides; none or several give
    an unclear GoalSpec.
    """

    source = "rules"

    def __init__(self, keywords: Mapping = DEFAULT_KEYWORDS):
        if not isinstance(keywords, Mapping):
            raise TypeError(f"keywords must be a mapping, not {type(keywords).__name__}")
        _task_type_names(list(keywords), "keyword task types")
        table, owner = {}, {}
        for task_type, words in keywords.items():
            if isinstance(words, (str, bytes)) or not isinstance(words, Sequence):
                raise TypeError(f"keywords for {task_type!r} must be a list or tuple of strings, "
                                f"not {type(words).__name__}")
            words = tuple(words)
            if not words:
                raise ValueError(f"task type {task_type!r} has no keywords, so it could never be chosen")
            for keyword in words:
                if type(keyword) is not str:
                    raise TypeError(f"keywords for {task_type!r} must be strings, not {type(keyword).__name__}")
                if not _KEYWORD_PATTERN.fullmatch(keyword):
                    raise ValueError(f"keyword {keyword!r} for {task_type!r} can never match: use lowercase "
                                     "letters and digits, with single spaces between words")
                if keyword in owner:
                    where = "twice" if owner[keyword] == task_type else f"also for {owner[keyword]!r}"
                    raise ValueError(f"keyword {keyword!r} for {task_type!r} is listed {where}")
                owner[keyword] = task_type
            table[task_type] = words
        self._keywords = MappingProxyType(table)

    @property
    def keywords(self):
        return self._keywords

    def matches(self, text):
        """Every task type whose keywords appear in the text, in table order (0.3 classify_goal)."""
        words = _words(text)
        return [task_type for task_type, keywords in self._keywords.items()
                if any(f" {keyword} " in words for keyword in keywords)]

    def interpret(self, request):
        text = normalize_goal_text(request.text)
        empty = refuse_empty(text, self.source, original_text=request.text)
        if empty:
            return empty
        found = self.matches(text)
        if len(found) == 1:
            return GoalSpec.decided(text, found[0], self.source, original_text=request.text)
        if found:
            return GoalSpec.unclear(text, self.source,
                                    [f"it matches more than one task type ({', '.join(found)})"],
                                    candidates=found, original_text=request.text)
        return GoalSpec.unclear(text, self.source, ["it does not match any task type Orbisent knows"],
                                original_text=request.text)


class ChainedInterpreter:
    """Asks interpreters in order and returns the first GoalSpec, decided or unclear.

    An empty goal is refused before any interpreter runs, as in Prototype 0.3.
    If every interpreter passes, the result is an unclear GoalSpec: never a guess.
    """

    source = "chain"

    def __init__(self, interpreters: Sequence[GoalInterpreter]):
        if isinstance(interpreters, (str, bytes)) or not isinstance(interpreters, Sequence):
            raise TypeError(f"interpreters must be a list or tuple, not {type(interpreters).__name__}")
        for interpreter in interpreters:
            if not callable(getattr(interpreter, "interpret", None)):
                raise TypeError(f"{type(interpreter).__name__} has no interpret() method")
        self._interpreters = tuple(interpreters)

    @property
    def interpreters(self):
        return self._interpreters

    def interpret(self, request):
        text = normalize_goal_text(request.text)
        empty = refuse_empty(text, self.source, original_text=request.text)
        if empty:
            return empty
        for interpreter in self._interpreters:
            result = interpreter.interpret(request)
            if result is not None:
                if not isinstance(result, GoalSpec):
                    raise TypeError(f"{type(interpreter).__name__} returned {type(result).__name__}, "
                                    "not a GoalSpec or None")
                return result
        return GoalSpec.unclear(text, self.source, ["no interpreter could classify the goal"],
                                original_text=request.text)


def default_interpreter():
    """The 1.0 default chain: --task first, then keyword rules. No AI model."""
    return ChainedInterpreter([ExplicitInterpreter(), RuleInterpreter()])
