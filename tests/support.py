"""Test helpers: a guard that makes any side effect fail while code under test runs.

    with forbid_side_effects() as attempts:
        validator.validate(plan)
    assert attempts == []

Inside the block, three layers catch access to the outside world:

1. An audit hook (sys.addaudithook). CPython raises audit events for opening files,
   listing or changing directories, sockets, subprocesses, os.system and more,
   however the call is reached (builtins.open, io.open, os.open, pathlib, shutil, a
   name imported under another name...). Any event whose name starts with one of
   FORBIDDEN_EVENT_PREFIXES is recorded and refused.
2. Patches for operations that raise no audit event: os.stat/lstat/access/getcwd and
   the os.path existence checks (pathlib's exists() and resolve() use these), and the
   clocks in the time module.
3. A patched datetime.date / datetime.datetime whose today()/now()/utcnow() refuse.
   Code that did `from datetime import date` or `from time import time` at import time
   holds its own reference to the real object. forbid_side_effects(modules=[...]) scans
   the namespaces of the listed modules and replaces every such reference (to a patched
   os/time function or to datetime.date/datetime.datetime) for the duration of the block.
   Nothing is re-imported, so class identities never change.

Every attempt is recorded in the list the block returns, even if the code under test
catches the exception, so a swallowed error can't hide an access.

The audit hook is installed once for the whole test process (hooks can't be removed);
it does nothing outside a forbid_side_effects() block.
"""

import contextlib
import datetime
import os
import sys
import threading
import time
from unittest import mock

FORBIDDEN_EVENT_PREFIXES = (
    "open", "os.", "shutil.", "socket.", "subprocess.", "glob.", "tempfile.", "ctypes.",
    "urllib.", "http.client.", "ftplib.", "smtplib.", "poplib.", "imaplib.", "nntplib.",
    "telnetlib.", "webbrowser.", "pty.", "fcntl.", "msvcrt.", "winreg.", "sqlite3.", "mmap.",
)


class SideEffectError(RuntimeError):
    """Raised when code tries to reach the outside world inside forbid_side_effects()."""


_state = threading.local()


def _armed():
    return getattr(_state, "attempts", None)


def _audit(event, args):
    attempts = _armed()
    if attempts is None or getattr(_state, "inside_hook", False):
        return
    if event.startswith(FORBIDDEN_EVENT_PREFIXES):
        _state.inside_hook = True
        try:
            attempts.append(event)
        finally:
            _state.inside_hook = False
        raise SideEffectError(f"side effect attempted: {event}")


sys.addaudithook(_audit)


def _refuse(name):
    def refused(*args, **kwargs):
        attempts = _armed()
        if attempts is not None:
            attempts.append(name)
        raise SideEffectError(f"side effect attempted: {name}")
    return refused


class _NoClockDate(datetime.date):
    @classmethod
    def today(cls):
        return _refuse("datetime.date.today")()


class _NoClockDateTime(datetime.datetime):
    @classmethod
    def today(cls):
        return _refuse("datetime.datetime.today")()

    @classmethod
    def now(cls, tz=None):
        return _refuse("datetime.datetime.now")()

    @classmethod
    def utcnow(cls):
        return _refuse("datetime.datetime.utcnow")()


_PATCHED = [
    (os, "stat"), (os, "lstat"), (os, "access"), (os, "getcwd"), (os, "getcwdb"), (os, "scandir"),
    (os, "listdir"), (os.path, "exists"), (os.path, "lexists"), (os.path, "isfile"), (os.path, "isdir"),
    (os.path, "islink"), (os.path, "realpath"), (os.path, "getsize"),
    (time, "time"), (time, "time_ns"), (time, "monotonic"), (time, "monotonic_ns"),
    (time, "perf_counter"), (time, "perf_counter_ns"), (time, "localtime"), (time, "gmtime"),
    (time, "process_time"), (time, "sleep"),
]


@contextlib.contextmanager
def forbid_side_effects(modules=()):
    """Refuse file, network, process and clock access inside the block; yield the attempts list.

    `modules` lists modules under test whose own imported names should be guarded too
    (see point 3 above).
    """
    attempts = []
    replacements = {}  # id(original object) -> replacement
    with contextlib.ExitStack() as stack:
        for module, name in _PATCHED:
            original = getattr(module, name)
            replacement = _refuse(f"{module.__name__}.{name}")
            replacements[id(original)] = replacement
            stack.enter_context(mock.patch.object(module, name, replacement))
        replacements[id(datetime.date)] = _NoClockDate
        replacements[id(datetime.datetime)] = _NoClockDateTime
        for module in modules:
            for name, value in list(vars(module).items()):
                if id(value) in replacements and not isinstance(value, type(module)):
                    stack.enter_context(mock.patch.object(module, name, replacements[id(value)]))
        stack.enter_context(mock.patch.object(datetime, "date", _NoClockDate))
        stack.enter_context(mock.patch.object(datetime, "datetime", _NoClockDateTime))
        _state.attempts = attempts
        try:
            yield attempts
        finally:
            _state.attempts = None


# ---------------------------------------------------------------------------
# Plan builders shared by the planning tests
# ---------------------------------------------------------------------------

import dataclasses  # noqa: E402

from orbisent.planning.validator import describe_expected  # noqa: E402
from orbisent.tools.filesystem import FILESYSTEM_TOOLS  # noqa: E402
from orbisent.types import CheckSpec, GoalSpec, Plan, PlanGoal, PlanStep, normalize_goal_text  # noqa: E402


def goal(original="Prepare my project folder", task_type="prepare_project", confidence=1.0):
    """A PlanGoal built the official way: GoalSpec -> PlanGoal.from_goal_spec()."""
    spec = GoalSpec.decided(normalize_goal_text(original), task_type, "rules", confidence=confidence,
                            original_text=original)
    return PlanGoal.from_goal_spec(spec)


def step(step_id, tool, args, *, depends_on=(), checks=(), description=None, expected=None):
    """A PlanStep whose expected text is generated, unless `expected` is given."""
    if description is None:
        description = f"Run {tool} for {step_id}"
    built = PlanStep(step_id, tool, args, description, "x",
                     tuple(depends_on), tuple(checks))
    if expected is None:
        try:
            expected = describe_expected(built, FILESYSTEM_TOOLS)
        except ValueError:
            expected = "x"
    return dataclasses.replace(built, expected=expected)


def plan(steps, *, parameters=(), acceptance=(), source="template:prepare_project", plan_goal=None,
         format_version=1):
    return Plan(plan_goal or goal(), tuple(parameters), tuple(steps), tuple(acceptance), source, format_version)


README_CHECKS = (CheckSpec("title_starts_with", {"text": "# "}), CheckSpec("contains", {"text": "**Goal:**"}),
                 CheckSpec("contains", {"text": "## Files"}))
TASKS_CHECKS = (CheckSpec("title_starts_with", {"text": "# Tasks"}), CheckSpec("contains", {"text": "Goal:"}),
                CheckSpec("min_count", {"pattern": "checkbox", "minimum": 5}))


def valid_steps():
    """Four steps that pass every rule: a folder, two files inside it, and a read of one of them."""
    return [
        step("s1", "fs.create_folder", {"path": "demo"}, description="Create the project folder demo"),
        step("s2", "fs.create_file", {"path": "demo/README.md", "content": "# demo\n\n**Goal:** g\n\n## Files\n"},
             depends_on=["s1"], checks=README_CHECKS, description="Write README.md"),
        step("s3", "fs.create_file", {"path": "demo/TASKS.md", "content": "# Tasks\n\nGoal: g\n" + "- [ ] x\n" * 5},
             depends_on=["s1"], checks=TASKS_CHECKS, description="Write TASKS.md"),
        step("s4", "fs.read_file", {"path": "demo/README.md"}, depends_on=["s2"], description="Read README.md back"),
    ]


def valid_plan(**kwargs):
    return plan(valid_steps(), **kwargs)
