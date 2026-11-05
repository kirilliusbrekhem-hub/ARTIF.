"""Tests for the plan data types (ARTIF. 1.0 Planning Foundation, ARCHITECTURE_1.0.md §12).

Run from the repository root:
    python3 -m unittest -v
"""

import copy
import dataclasses
import decimal
import math
import os
import pickle
import random
import subprocess
import sys
import unittest
from pathlib import Path

from orbisent.tools import ARTIF_1_0_PERMISSIONS, FILESYSTEM_TOOLS, ArgSpec, Permission, ToolInfo
from orbisent.types import (
    ISSUE_CODES,
    MAX_PLAN_INT,
    CheckSpec,
    FrozenMap,
    GoalSpec,
    MissingInput,
    Plan,
    PlanGoal,
    PlanningResult,
    PlanStep,
    ResolvedParameter,
    ValidationIssue,
    confidence_to_permille,
)
from tests import support

ROOT = Path(__file__).resolve().parent.parent


class ConfidenceTest(unittest.TestCase):
    """U3: GoalSpec.confidence (float) becomes whole thousandths, ROUND_HALF_UP, deterministically."""

    BOUNDARIES = [(0.0, 0), (0, 0), (0.0004, 0), (0.00049, 0), (0.0005, 1), (0.0015, 2), (0.0025, 3),
                  (0.001, 1), (0.5, 500), (0.85, 850), (0.9994, 999), (0.9995, 1000), (0.99949, 999),
                  (1.0, 1000), (1, 1000), (1e-300, 0), (5e-324, 0)]

    def test_boundary_values(self):
        for value, expected in self.BOUNDARIES:
            with self.subTest(value=value):
                self.assertEqual(confidence_to_permille(value), expected)
                self.assertIs(type(confidence_to_permille(value)), int)

    def test_half_up_not_bankers_rounding(self):
        # Python's round() would give 0 and 2 for these (round-half-to-even on the float product).
        self.assertEqual(round(0.0005 * 1000), 0)
        self.assertEqual(confidence_to_permille(0.0005), 1)
        self.assertEqual(confidence_to_permille(0.0025), 3)

    def test_rejected_values(self):
        for value in (True, False, "0.5", None, decimal.Decimal("0.5"), 1 + 0j):
            with self.subTest(value=value), self.assertRaises(TypeError):
                confidence_to_permille(value)
        for value in (-0.0, -1e-9, 1.0000001, 2, -1, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                confidence_to_permille(value)

    def test_independent_of_the_global_decimal_context(self):
        with decimal.localcontext(decimal.Context(prec=1, rounding=decimal.ROUND_DOWN)):
            self.assertEqual(confidence_to_permille(0.85), 850)
            self.assertEqual(confidence_to_permille(0.0005), 1)

    def test_random_values_stay_between_floor_and_ceil(self):
        rng = random.Random(20261105)
        for _ in range(10_000):
            value = rng.random()
            result = confidence_to_permille(value)
            self.assertTrue(math.floor(value * 1000) <= result <= math.ceil(value * 1000), value)
            self.assertEqual(result, confidence_to_permille(value))

    def test_goal_spec_keeps_its_float(self):
        spec = GoalSpec.decided("g", "t", "rules", confidence=0.85, original_text="g")
        self.assertIs(type(spec.confidence), float)
        self.assertEqual(PlanGoal.from_goal_spec(spec).confidence_permille, 850)


class PlanGoalTest(unittest.TestCase):
    def test_from_goal_spec_carries_text_and_original(self):
        original = "  Prépare   my project\n"
        spec = GoalSpec.decided("Prépare my project", "prepare_project", "rules", original_text=original)
        plan_goal = PlanGoal.from_goal_spec(spec)
        self.assertEqual((plan_goal.text, plan_goal.original_text, plan_goal.task_type, plan_goal.source,
                          plan_goal.confidence_permille),
                         ("Prépare my project", original, "prepare_project", "rules", 1000))

    def test_tiny_confidence_is_representable_as_zero(self):
        spec = GoalSpec.decided("g", "t", "rules", confidence=0.0004, original_text="g")
        self.assertEqual(PlanGoal.from_goal_spec(spec).confidence_permille, 0)  # Not raised to 1.

    def test_unclear_goals_and_wrong_types_are_refused(self):
        with self.assertRaises(ValueError):
            PlanGoal.from_goal_spec(GoalSpec.unclear("g", "rules", ["why"], original_text="g"))
        with self.assertRaises(TypeError):
            PlanGoal.from_goal_spec("prepare_project")

    def test_text_that_breaks_plan_rules_is_refused(self):
        spec = GoalSpec.decided("bell\x07here", "t", "rules", original_text="bell\x07here")
        with self.assertRaises(ValueError):
            PlanGoal.from_goal_spec(spec)

    def test_original_text_is_exempt_from_nfc_and_control_rules_but_not_surrogates(self):
        PlanGoal("g", "g\r\n\x1b[0m é", "t", "rules", 1000)  # Exact input: allowed.
        with self.assertRaises(ValueError):
            PlanGoal("g", "bad \udcff", "t", "rules", 1000)
        with self.assertRaises(ValueError):
            PlanGoal("é", "é", "t", "rules", 1000)     # `text` must be NFC.

    def test_fields(self):
        for kwargs in ({"task_type": ""}, {"source": ""}):
            values = dict(text="g", original_text="g", task_type="t", source="rules", confidence_permille=1)
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PlanGoal(**values)
        for permille in (1.0, True, "1", None):
            with self.subTest(permille=permille), self.assertRaises(TypeError):
                PlanGoal("g", "g", "t", "rules", permille)
        with self.assertRaises(ValueError):
            PlanGoal("g", "g", "t", "rules", MAX_PLAN_INT + 1)


class PlanValueRulesTest(unittest.TestCase):
    """Canonical readiness: only exact str/int/bool/None, tuples and FrozenMap; nothing coerced."""

    def make_step(self, **changes):
        values = dict(id="s1", tool="fs.create_folder", args={"path": "demo"}, description="d", expected="e")
        values.update(changes)
        return PlanStep(**values)

    def test_floats_are_never_allowed(self):
        with self.assertRaises(TypeError):
            self.make_step(args={"path": "demo", "x": 1.5})
        with self.assertRaises(TypeError):
            CheckSpec("min_count", {"pattern": "checkbox", "minimum": 5.0})
        with self.assertRaises(TypeError):
            ResolvedParameter("n", 1.5, "user")
        with self.assertRaises(TypeError):
            Plan(support.goal(), (), (), (), "template:prepare_project", format_version=1.0)

    def test_int_range_and_bool_are_exact(self):
        CheckSpec("k", {"n": MAX_PLAN_INT})
        CheckSpec("k", {"n": -MAX_PLAN_INT})
        for value in (MAX_PLAN_INT + 1, -MAX_PLAN_INT - 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CheckSpec("k", {"n": value})
        self.assertIs(CheckSpec("k", {"b": True}).params["b"], True)
        with self.assertRaises(TypeError):
            Plan(support.goal(), (), (), (), "template:prepare_project", format_version=True)

    def test_strings_must_be_nfc_without_controls(self):
        for bad in ("é", "nul\x00", "esc\x1b", "cr\r", "del\x7f", "c1\x85", "lone \ud800"):
            with self.subTest(bad=ascii(bad)), self.assertRaises(ValueError):
                self.make_step(description=bad)
            with self.subTest(arg=ascii(bad)), self.assertRaises(ValueError):
                self.make_step(args={"path": bad})
        self.make_step(description="tab\there", args={"path": "demo", "content": "line 1\nline 2\n"})

    def test_subclasses_and_other_types_are_rejected(self):
        class MyStr(str):
            pass

        for bad in (MyStr("x"), b"x", 3, None):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                self.make_step(id=bad)
        with self.assertRaises(TypeError):
            self.make_step(args={MyStr("path"): "demo"})

    def test_sequences_must_be_lists_or_tuples(self):
        for bad in ({"s1"}, frozenset({"s1"}), "s1", (x for x in ["s1"]), {"s1": 1}):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(TypeError):
                self.make_step(depends_on=bad)
        source = ["s0"]
        built = self.make_step(depends_on=source)
        source.append("changed")
        self.assertEqual(built.depends_on, ("s0",))
        self.assertIs(type(built.depends_on), tuple)

    def test_nested_values_are_immutable_copies(self):
        args = {"path": "demo"}
        built = self.make_step(args=args)
        args["path"] = "changed"
        self.assertEqual(built.args["path"], "demo")
        self.assertIsInstance(built.args, FrozenMap)
        with self.assertRaises(TypeError):
            built.args["path"] = "x"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            built.args = FrozenMap({})
        params = {"text": "# "}
        check = CheckSpec("title_starts_with", params)
        params["text"] = "changed"
        self.assertEqual(check.params["text"], "# ")

    def test_element_types_are_checked(self):
        with self.assertRaises(TypeError):
            self.make_step(checks=[{"kind": "not_empty"}])
        with self.assertRaises(TypeError):
            Plan(support.goal(), (), ("not a step",), (), "template:prepare_project")
        with self.assertRaises(TypeError):
            Plan(support.goal(), [{"name": "x"}], (), (), "template:prepare_project")
        with self.assertRaises(TypeError):
            Plan("goal", (), (), (), "template:prepare_project")

    def test_all_plan_types_are_frozen(self):
        built = support.valid_plan()
        for obj, name in ((built, "source"), (built.goal, "text"), (built.steps[0], "id"),
                          (built.steps[1].checks[0], "kind")):
            with self.subTest(type=type(obj).__name__), self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(obj, name, "x")


class ResolvedParameterTest(unittest.TestCase):
    def test_values_and_provenance(self):
        for value in ("bakery", 3, True):
            ResolvedParameter("n", value, "user")
        with self.assertRaises(TypeError):
            ResolvedParameter("n", None, "user")
        with self.assertRaises(ValueError):
            ResolvedParameter("n", "v", "guessed")
        with self.assertRaises(ValueError):
            ResolvedParameter("", "v", "user")
        derived = ResolvedParameter("project_name", "my-project", "derived", rule="folder_name_from_text.v1",
                                    rule_input="My Project", input_source="goal.text")
        self.assertEqual((derived.rule, derived.rule_input, derived.input_source),
                         ("folder_name_from_text.v1", "My Project", "goal.text"))


class ResultAndIssueTypesTest(unittest.TestCase):
    def missing(self):
        return MissingInput("project_name", "What should the folder be called?", "letters would be lost",
                            "проект checklist", ("п", "р"))

    def test_planning_result_shapes(self):
        issue = ValidationIssue("bad_goal", None, "m")
        valid = [PlanningResult.planned(support.valid_plan()), PlanningResult.needs_input([self.missing()]),
                 PlanningResult.refused(reasons=["no template"]), PlanningResult.refused(issues=[issue])]
        self.assertEqual([r.status for r in valid], ["planned", "needs_input", "refused", "refused"])
        invalid = [dict(status="planned"), dict(status="planned", plan=support.valid_plan(), reasons=("x",)),
                   dict(status="needs_input"), dict(status="needs_input", missing=(self.missing(),), reasons=("x",)),
                   dict(status="needs_input", plan=support.valid_plan(), missing=(self.missing(),)),
                   dict(status="refused"), dict(status="refused", missing=(self.missing(),), reasons=("x",)),
                   dict(status="refused", plan=support.valid_plan(), reasons=("x",)), dict(status="done")]
        for kwargs in invalid:
            with self.subTest(kwargs=sorted(kwargs)), self.assertRaises(ValueError):
                PlanningResult(**kwargs)

    def test_missing_input_preserves_the_original_exactly(self):
        missing = self.missing()
        self.assertEqual(missing.original, "проект checklist")
        MissingInput("n", "q", "r", "  é \t!!!\n", ("́",))  # Exact input, non-NFC: allowed.
        with self.assertRaises(ValueError):
            MissingInput("n", "", "r", "x")
        with self.assertRaises(ValueError):
            MissingInput("n", "q", "r", "bad \udcff")

    def test_issue_codes_are_exactly_the_revision_3_list(self):
        self.assertEqual(ISSUE_CODES, frozenset({
            "unsupported_format", "no_steps", "too_many_steps", "bad_source", "parameter_order",
            "duplicate_parameter", "bad_provenance", "unknown_rule", "plan_too_large", "bad_step_id",
            "duplicate_step_id", "bad_description", "expected_mismatch", "unknown_tool", "permission_denied",
            "missing_arg", "unexpected_arg", "bad_arg", "content_too_large", "path_invalid", "duplicate_target",
            "unknown_dependency", "self_dependency", "forward_dependency", "dependency_order",
            "missing_dependency", "order_violation", "bad_check", "check_on_non_content_step",
            "too_many_checks", "bad_acceptance", "bad_goal"}))
        with self.assertRaises(ValueError):
            ValidationIssue("made_up", None, "m")


class EqualityNotIdentityTest(unittest.TestCase):
    """== is the determinism check; Python's hash() is never a persistent identity."""

    def test_equal_plans_compare_equal_and_survive_copies(self):
        first, second = support.valid_plan(), support.valid_plan()
        self.assertEqual(first, second)
        self.assertEqual(copy.deepcopy(first), first)
        self.assertEqual(pickle.loads(pickle.dumps(first)), first)
        self.assertEqual(dataclasses.replace(first, source="template:prepare_project"), first)
        self.assertNotEqual(dataclasses.replace(first, source="model:x"), first)

    def test_hash_is_in_memory_only(self):
        self.assertEqual(len({support.valid_plan(), support.valid_plan()}), 1)  # Usable in sets.
        for name in ("identity", "plan_hash", "digest", "canonical_bytes", "fingerprint"):
            self.assertFalse(hasattr(Plan, name), name)  # Persistent identity is not in this PR.

    def test_hash_changes_between_processes_but_repr_does_not(self):
        script = ("import sys; sys.path.insert(0, %r); from tests import support; p = support.valid_plan(); "
                  "print(hash(p.steps[0].args)); print(repr(p))") % str(ROOT)
        outputs = []
        for seed in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
            run = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env,
                                 cwd=str(ROOT), check=True)
            outputs.append(run.stdout.splitlines())
        self.assertNotEqual(outputs[0][0], outputs[1][0])   # hash(): different per process.
        self.assertEqual(outputs[0][1], outputs[1][1])      # The data itself: identical.


class ToolDescriptorTest(unittest.TestCase):
    def test_builtin_descriptors(self):
        names = [tool.name for tool in FILESYSTEM_TOOLS]
        self.assertEqual(names, ["fs.create_folder", "fs.create_file", "fs.read_file", "fs.list_dir"])
        permissions = {tool.name: tool.permission for tool in FILESYSTEM_TOOLS}
        self.assertEqual(permissions, {"fs.create_folder": Permission.CREATE, "fs.create_file": Permission.CREATE,
                                       "fs.read_file": Permission.READ, "fs.list_dir": Permission.READ})
        self.assertEqual([t.name for t in FILESYSTEM_TOOLS if t.produces_content], ["fs.create_file"])
        self.assertEqual(ARTIF_1_0_PERMISSIONS, frozenset({Permission.READ, Permission.CREATE}))

    def test_descriptors_hold_no_code(self):
        for tool in FILESYSTEM_TOOLS:
            values = [getattr(tool, f.name) for f in dataclasses.fields(tool)]
            values += [getattr(arg, f.name) for arg in tool.args for f in dataclasses.fields(arg)]
            for value in values:
                with self.subTest(tool=tool.name, value=value):
                    self.assertFalse(callable(value) and not isinstance(value, (str, Permission)))

    def test_descriptor_validation(self):
        path = ArgSpec("path", "target_path", True, 10)
        with self.assertRaises(ValueError):
            ToolInfo("t", "d", Permission.READ, (path, ArgSpec("other", "target_path", True, 10)), False)
        with self.assertRaises(ValueError):
            ToolInfo("t", "d", Permission.READ, (path, path), False)
        with self.assertRaises(TypeError):
            ToolInfo("t", "d", "read", (path,), False)
        with self.assertRaises(TypeError):
            ToolInfo("t", "d", Permission.READ, (lambda: None,), False)
        for kwargs in (dict(kind="url"), dict(max_length=0), dict(name="")):
            values = dict(name="a", kind="text", required=True, max_length=1)
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ArgSpec(**values)
        with self.assertRaises(TypeError):
            ArgSpec("a", "text", 1, 1)


if __name__ == "__main__":
    unittest.main()
