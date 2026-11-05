"""Tests for orbisent.understanding, including exact parity with Prototype 0.3.

Run from the repository root:
    python3 -m unittest discover -s tests -v

The parity tests import prototype/orbisent_agent.py read-only, as the reference
implementation. They never call anything that touches the file system.
"""

import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from orbisent.types import GoalSpec, Request
from orbisent.understanding import (
    DEFAULT_KEYWORDS,
    EMPTY_GOAL_REASON,
    ChainedInterpreter,
    ExplicitInterpreter,
    RuleInterpreter,
    clean_goal,
    default_interpreter,
)

PROTOTYPE_DIR = Path(__file__).resolve().parent.parent / "prototype"
if str(PROTOTYPE_DIR) not in sys.path:
    sys.path.append(str(PROTOTYPE_DIR))  # Appended, so prototype modules can't shadow anything else.
import orbisent_agent as prototype  # noqa: E402  (Prototype 0.3, the reference)

# Goals used to compare 1.0 with 0.3. They include every goal used in the prototype's own tests and
# documentation, plus edge cases: case, punctuation, whitespace, plurals, partial words and Unicode.
PARITY_GOALS = [
    "Prepare my project folder for launch",
    "Create a launch checklist for my app",
    "Launch my bakery website",
    "Make me a sandwich",
    "Prepare my project folder and a launch checklist",
    "Prepare my project folder and a checklist",
    "Set up a project for my band",
    "I need a CHECKLIST!",
    "Write about checklisting",
    "Prepare my project folder",
    "Create a launch checklist",
    "Launch my bakery",
    "Goal",
    "x",
    "checklists for everyone",
    "a check list",
    "a check-list",
    "a check\nlist",
    "  prepare   the\tproject  ",
    "PREPARE PROJECT",
    "prepare-project",
    "prepare_project",
    "setup project",
    "set up my project",
    "setupproject",
    "newproject",
    "new project",
    "a new projects",
    "scaffold",
    "scaffolding",
    "project directory",
    "project-directory/",
    "my project folder's checklist",
    "Prépare my project folder",
    "чек-лист checklist",
    "проект",
    "checklist" * 3,
    "🚀 checklist 🚀",
    "…",
    "",
    "   ",
]


def prototype_result(goal):
    """What Prototype 0.3 decides for a goal: (task_type or None, matches), or 'empty' if it refuses it."""
    cleaned = prototype.clean_goal(goal)
    if not cleaned:
        return "empty"
    matches = prototype.classify_goal(cleaned)
    return (matches[0] if len(matches) == 1 else None, matches)


class PrototypeParityTest(unittest.TestCase):
    def test_keyword_table_matches_the_prototype(self):
        expected = {task_id: task.keywords for task_id, task in prototype.TASK_TYPES.items()}
        self.assertEqual(DEFAULT_KEYWORDS, expected)
        self.assertEqual(list(DEFAULT_KEYWORDS), list(prototype.TASK_TYPES))  # Same order.

    def test_clean_goal_matches_the_prototype(self):
        for goal in PARITY_GOALS:
            with self.subTest(goal=goal):
                self.assertEqual(clean_goal(goal), prototype.clean_goal(goal))

    def test_rule_matches_equal_classify_goal(self):
        rules = RuleInterpreter()
        for goal in PARITY_GOALS:
            with self.subTest(goal=goal):
                self.assertEqual(rules.matches(clean_goal(goal)), prototype.classify_goal(clean_goal(goal)))

    def test_default_chain_decides_exactly_like_the_prototype(self):
        interpreter = default_interpreter()
        for goal in PARITY_GOALS:
            with self.subTest(goal=goal):
                spec = interpreter.interpret(Request(goal))
                expected = prototype_result(goal)
                if expected == "empty":
                    self.assertTrue(spec.needs_clarification)
                    self.assertEqual(spec.ambiguities, ("the goal must not be empty",))
                    continue
                task_type, matches = expected
                self.assertEqual(spec.task_type, task_type)
                self.assertEqual(spec.text, prototype.clean_goal(goal))
                if task_type is None:
                    self.assertEqual(list(spec.candidates), matches if len(matches) > 1 else [])

    def test_explicit_task_wins_like_the_prototype(self):
        # 0.3: "--task create_launch_checklist" on a prepare_project goal creates the checklist.
        spec = default_interpreter().interpret(Request("Prepare my project folder", "create_launch_checklist"))
        self.assertEqual((spec.task_type, spec.source), ("create_launch_checklist", "explicit"))
        spec = default_interpreter().interpret(Request("Launch my bakery", "prepare_project"))
        self.assertEqual((spec.task_type, spec.source), ("prepare_project", "explicit"))

    def test_explanations_match_the_prototype_wording(self):
        interpreter = default_interpreter()
        unknown = interpreter.interpret(Request("Make me a sandwich"))
        self.assertEqual(unknown.ambiguities, ("it does not match any task type Orbisent knows",))
        ambiguous = interpreter.interpret(Request("Prepare my project folder and a checklist"))
        self.assertEqual(ambiguous.ambiguities,
                         ("it matches more than one task type (prepare_project, create_launch_checklist)",))
        self.assertEqual(ambiguous.candidates, ("prepare_project", "create_launch_checklist"))


class ExplicitInterpreterTest(unittest.TestCase):
    def test_passes_when_no_task_given(self):
        self.assertIsNone(ExplicitInterpreter().interpret(Request("Prepare my project folder")))

    def test_known_task(self):
        spec = ExplicitInterpreter().interpret(Request("  my \n goal ", "prepare_project"))
        self.assertEqual((spec.text, spec.task_type, spec.source, spec.confidence),
                         ("my goal", "prepare_project", "explicit", 1.0))

    def test_unknown_task_is_refused(self):
        spec = ExplicitInterpreter().interpret(Request("Prepare my project folder", "delete_everything"))
        self.assertTrue(spec.needs_clarification)
        self.assertEqual(spec.ambiguities,
                         ("unknown task type 'delete_everything' (known: prepare_project, create_launch_checklist)",))

    def test_task_names_are_exact(self):
        for name in ("Prepare_Project", " prepare_project", "prepare project", ""):
            with self.subTest(name=name):
                self.assertTrue(ExplicitInterpreter().interpret(Request("g", name)).needs_clarification)

    def test_custom_task_types(self):
        interpreter = ExplicitInterpreter(["only_this"])
        self.assertEqual(interpreter.interpret(Request("g", "only_this")).task_type, "only_this")
        self.assertTrue(interpreter.interpret(Request("g", "prepare_project")).needs_clarification)


class RuleInterpreterTest(unittest.TestCase):
    def test_clear_goals(self):
        rules = RuleInterpreter()
        self.assertEqual(rules.interpret(Request("Set up a project for my band")).task_type, "prepare_project")
        spec = rules.interpret(Request("I need a CHECKLIST!"))
        self.assertEqual((spec.task_type, spec.source, spec.confidence), ("create_launch_checklist", "rules", 1.0))

    def test_ignores_explicit_task(self):
        # The rule interpreter only reads the text; choosing between explicit and rules is the chain's job.
        spec = RuleInterpreter().interpret(Request("Create a launch checklist", "prepare_project"))
        self.assertEqual(spec.task_type, "create_launch_checklist")

    def test_never_guesses(self):
        for goal in ("Make me a sandwich", "Prepare my project folder and a checklist", "checklisting"):
            with self.subTest(goal=goal):
                spec = RuleInterpreter().interpret(Request(goal))
                self.assertIsNone(spec.task_type)
                self.assertEqual(spec.confidence, 0.0)
                self.assertTrue(spec.ambiguities)

    def test_custom_keywords(self):
        rules = RuleInterpreter({"greet": ("say hello",)})
        self.assertEqual(rules.interpret(Request("Please SAY hello!")).task_type, "greet")
        self.assertTrue(rules.interpret(Request("hello")).needs_clarification)

    def test_keywords_that_can_never_match_are_rejected(self):
        for bad in ("Checklist", "check  list", " checklist", "check-list", "", "chéck"):
            with self.subTest(keyword=bad), self.assertRaises(ValueError):
                RuleInterpreter({"t": (bad,)})
        with self.assertRaises(TypeError):
            RuleInterpreter({"t": "checklist"})  # A string, not a sequence of strings.
        with self.assertRaises(TypeError):  # Not a string at all: a type error, still rejected.
            RuleInterpreter({"t": (None,)})

    def test_keyword_table_is_copied(self):
        table = {"t": ["word"]}
        rules = RuleInterpreter(table)
        table["t"].append("other")
        self.assertEqual(rules.keywords, {"t": ("word",)})


class Spy:
    """A fake interpreter that records calls and returns a fixed answer."""

    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def interpret(self, request):
        self.calls += 1
        return self.answer


class ChainedInterpreterTest(unittest.TestCase):
    def test_first_answer_wins_and_later_interpreters_are_not_asked(self):
        first = Spy(GoalSpec.decided("g", "a", "first", original_text="g"))
        second = Spy(GoalSpec.decided("g", "b", "second", original_text="g"))
        self.assertEqual(ChainedInterpreter([first, second]).interpret(Request("g")).task_type, "a")
        self.assertEqual((first.calls, second.calls), (1, 0))

    def test_none_passes_to_the_next(self):
        passing, deciding = Spy(None), Spy(GoalSpec.decided("g", "b", "second", original_text="g"))
        self.assertEqual(ChainedInterpreter([passing, deciding]).interpret(Request("g")).task_type, "b")
        self.assertEqual(passing.calls, 1)

    def test_unclear_answer_stops_the_chain(self):
        unclear, later = Spy(GoalSpec.unclear("g", "first", ["no"], original_text="g")), Spy(GoalSpec.decided("g", "b", "s", original_text="g"))
        spec = ChainedInterpreter([unclear, later]).interpret(Request("g"))
        self.assertTrue(spec.needs_clarification)
        self.assertEqual(later.calls, 0)

    def test_invalid_explicit_task_never_falls_through_to_rules(self):
        rules = Spy(GoalSpec.decided("g", "prepare_project", "rules", original_text="g"))
        spec = ChainedInterpreter([ExplicitInterpreter(), rules]).interpret(
            Request("Prepare my project folder", "nope"))
        self.assertTrue(spec.needs_clarification)
        self.assertEqual(rules.calls, 0)

    def test_everyone_passing_gives_unclear_not_a_guess(self):
        spec = ChainedInterpreter([Spy(None), Spy(None)]).interpret(Request("g"))
        self.assertEqual((spec.task_type, spec.source, spec.ambiguities),
                         (None, "chain", ("no interpreter could classify the goal",)))
        self.assertTrue(ChainedInterpreter([]).interpret(Request("g")).needs_clarification)

    def test_empty_goal_is_refused_before_any_interpreter_runs(self):
        spy = Spy(GoalSpec.decided("g", "a", "s", original_text="g"))
        for text in ("", "   ", "\n\t"):
            with self.subTest(text=text):
                spec = ChainedInterpreter([spy]).interpret(Request(text, "prepare_project"))
                self.assertEqual(spec.ambiguities, ("the goal must not be empty",))
        self.assertEqual(spy.calls, 0)

    def test_wrong_return_type_is_an_error(self):
        with self.assertRaises(TypeError):
            ChainedInterpreter([Spy("prepare_project")]).interpret(Request("g"))


class DeterminismAndIsolationTest(unittest.TestCase):
    def test_same_request_gives_equal_results(self):
        interpreter = default_interpreter()
        for goal in PARITY_GOALS:
            with self.subTest(goal=goal):
                self.assertEqual(interpreter.interpret(Request(goal)), interpreter.interpret(Request(goal)))

    def test_no_file_or_network_access(self):
        # Goal understanding is pure computation: opening files or sockets would be a bug.
        blocked = mock.Mock(side_effect=AssertionError("unexpected I/O"))
        with mock.patch("builtins.open", blocked), mock.patch("socket.socket", blocked), \
                mock.patch("os.open", blocked):
            interpreter = default_interpreter()
            for goal in PARITY_GOALS:
                interpreter.interpret(Request(goal))
                interpreter.interpret(Request(goal, "prepare_project"))
        blocked.assert_not_called()


class InitValidationTest(unittest.TestCase):
    """N2: interpreters check their configuration when built and keep it read-only."""

    def test_explicit_rejects_bad_task_types(self):
        type_errors = ["prepare_project", b"prepare_project", None, 3, [None], [1], ["ok", None],
                       {"prepare_project"}]  # A set has no order, so it's rejected too.
        for value in type_errors:
            with self.subTest(value=value), self.assertRaises(TypeError):
                ExplicitInterpreter(value)
        for value in ([], (), [""], ["a", "a"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ExplicitInterpreter(value)

    def test_explicit_task_types_are_read_only(self):
        source = ["a", "b"]
        interpreter = ExplicitInterpreter(source)
        source.append("c")
        self.assertEqual(interpreter.task_types, ("a", "b"))
        with self.assertRaises(AttributeError):
            interpreter.task_types = ("x",)

    def test_rule_rejects_bad_tables(self):
        type_errors = [["not", "a", "mapping"], "checklist", None,
                       {1: ("word",)},                  # Task type name isn't a string.
                       {"t": "word"},                   # A single string instead of a sequence.
                       {"t": {"word"}},                 # A set: no order.
                       {"t": (None,)}, {"t": (b"word",)}]
        for value in type_errors:
            with self.subTest(value=value), self.assertRaises(TypeError):
                RuleInterpreter(value)
        value_errors = [{},                             # Nothing to classify.
                        {"": ("word",)},                # Empty task type name.
                        {"t": ()},                      # A task type that could never be chosen.
                        {"t": ("word", "word")},        # Duplicate keyword.
                        {"a": ("word",), "b": ("word",)},  # Same keyword for two task types: always ambiguous.
                        {"t": ("Word",)}]
        for value in value_errors:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RuleInterpreter(value)

    def test_rule_keywords_are_read_only(self):
        rules = RuleInterpreter()
        with self.assertRaises(TypeError):
            rules.keywords["x"] = ("y",)
        with self.assertRaises(AttributeError):
            rules.keywords = {}
        with self.assertRaises(TypeError):
            DEFAULT_KEYWORDS["x"] = ("y",)
        self.assertEqual(list(rules.keywords), ["prepare_project", "create_launch_checklist"])

    def test_chain_rejects_bad_interpreters(self):
        for value in ("not a list", None, [object()], [Spy(None), "rules"]):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ChainedInterpreter(value)
        chain = ChainedInterpreter([Spy(None)])
        with self.assertRaises(AttributeError):
            chain.interpreters = ()

    def test_default_configuration_is_valid(self):
        default_interpreter()  # Must not raise.
        ExplicitInterpreter()
        RuleInterpreter()


class EmptyGoalTest(unittest.TestCase):
    """N3: every interpreter refuses an empty goal the same way, alone or in a chain."""

    EMPTY = ("", " ", "  \n\t ")

    def assert_refused(self, spec, source):
        self.assertTrue(spec.needs_clarification)
        self.assertEqual((spec.text, spec.source, spec.ambiguities), ("", source, (EMPTY_GOAL_REASON,)))

    def test_explicit_alone(self):
        for text in self.EMPTY:
            with self.subTest(text=text):
                self.assert_refused(ExplicitInterpreter().interpret(Request(text, "prepare_project")), "explicit")
                # An unknown task with an empty goal still reports the empty goal first, like the chain.
                self.assert_refused(ExplicitInterpreter().interpret(Request(text, "nope")), "explicit")
                # Without --task, the explicit interpreter doesn't apply at all.
                self.assertIsNone(ExplicitInterpreter().interpret(Request(text)))

    def test_rules_alone(self):
        for text in self.EMPTY:
            with self.subTest(text=text):
                self.assert_refused(RuleInterpreter().interpret(Request(text)), "rules")

    def test_chain(self):
        for text in self.EMPTY:
            for task in (None, "prepare_project", "nope"):
                with self.subTest(text=text, task=task):
                    self.assert_refused(default_interpreter().interpret(Request(text, task)), "chain")

    def test_same_wording_everywhere(self):
        reasons = {
            ExplicitInterpreter().interpret(Request("", "prepare_project")).ambiguities,
            RuleInterpreter().interpret(Request("")).ambiguities,
            ChainedInterpreter([]).interpret(Request("")).ambiguities,
        }
        self.assertEqual(reasons, {("the goal must not be empty",)})


class DiscoveryTest(unittest.TestCase):
    """N5: plain `python3 -m unittest` from the repository root finds these tests."""

    def test_root_discovery_finds_the_tests_package(self):
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "tests" / "__init__.py").is_file())
        suite = unittest.defaultTestLoader.discover(str(root), top_level_dir=str(root))

        def ids(test):
            if isinstance(test, unittest.TestSuite):
                for child in test:
                    yield from ids(child)
            else:
                yield test.id()

        found = list(ids(suite))
        self.assertTrue(any(i.startswith("tests.test_types.") for i in found))
        self.assertTrue(any(i.startswith("tests.test_understanding.") for i in found))
        self.assertFalse([i for i in found if "_FailedTest" in i or "ModuleImportFailure" in i])


class OriginalTextBoundaryTest(unittest.TestCase):
    """Q1: every interpreter keeps Request.text exactly in GoalSpec.original_text."""

    ORIGINALS = ["  Prepare   my\nproject folder  ", "Create a launch\tchecklist", "Make me a sandwich",
                 "Prepare my project folder and a checklist", "Pre\u0301pare my project folder",
                 "", "   ", "\u3000", "checklist \U0001f680"]

    def interpreters(self):
        return [ExplicitInterpreter(), RuleInterpreter(), default_interpreter(), ChainedInterpreter([])]

    def test_every_result_keeps_the_exact_original(self):
        for original in self.ORIGINALS:
            for task in (None, "prepare_project", "not_a_task"):
                for interpreter in self.interpreters():
                    with self.subTest(original=original, task=task, interpreter=type(interpreter).__name__):
                        spec = interpreter.interpret(Request(original, task))
                        if spec is None:  # ExplicitInterpreter passes when there's no --task.
                            continue
                        self.assertEqual(spec.original_text, original)
                        self.assertEqual(spec.text, unicodedata.normalize("NFC", " ".join(original.split()))
                                         if original.split() else "")

    def test_text_is_nfc(self):
        spec = default_interpreter().interpret(Request("Pre\u0301pare my project folder"))
        self.assertTrue(unicodedata.is_normalized("NFC", spec.text))
        self.assertEqual(spec.text, "Pr\u00e9pare my project folder")

    def test_decomposed_input_is_classified_like_its_nfc_form(self):
        interpreter = default_interpreter()
        for goal in PARITY_GOALS:
            decomposed = unicodedata.normalize("NFD", goal)
            with self.subTest(goal=goal):
                from_nfd = interpreter.interpret(Request(decomposed))
                from_nfc = interpreter.interpret(Request(goal))
                self.assertEqual((from_nfd.task_type, from_nfd.text, from_nfd.ambiguities, from_nfd.candidates),
                                 (from_nfc.task_type, from_nfc.text, from_nfc.ambiguities, from_nfc.candidates))
                self.assertEqual(from_nfd.original_text, decomposed)

    def test_parity_corpus_is_nfc(self):
        # The 0.3 parity results above stay exact because every corpus goal is already NFC.
        self.assertEqual([g for g in PARITY_GOALS if not unicodedata.is_normalized("NFC", g)], [])


if __name__ == "__main__":
    unittest.main()
