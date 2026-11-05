"""Tests for orbisent.types (standard library only).

Run from the repository root:
    python3 -m unittest discover -s tests -v
"""

import copy
import dataclasses
import enum
import pickle
import unittest

import unicodedata

from orbisent.types import FrozenMap, GoalSpec, Request, normalize_goal_text


class RequestTest(unittest.TestCase):
    def test_defaults(self):
        request = Request("Prepare my project folder")
        self.assertEqual((request.text, request.explicit_task, dict(request.options)),
                         ("Prepare my project folder", None, {}))

    def test_is_frozen(self):
        request = Request("goal")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.text = "other"

    def test_options_are_a_read_only_copy(self):
        options = {"name": "bakery"}
        request = Request("goal", options=options)
        options["name"] = "changed"
        self.assertEqual(request.options["name"], "bakery")
        with self.assertRaises(TypeError):
            request.options["name"] = "changed"

    def test_rejects_wrong_types(self):
        for kwargs in ({"text": None}, {"text": b"bytes"}, {"text": "g", "explicit_task": 3},
                       {"text": "g", "options": ["not", "a", "mapping"]},
                       {"text": "g", "options": {1: "non-string key"}},
                       {"text": "g", "options": {"nested": ["list"]}},
                       {"text": "g", "options": {"float": 1.5}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                Request(**kwargs)

    def test_scalar_option_values_are_allowed(self):
        request = Request("g", options={"s": "x", "i": 1, "b": True, "n": None})
        self.assertEqual(dict(request.options), {"s": "x", "i": 1, "b": True, "n": None})

    def test_equality(self):
        self.assertEqual(Request("g", "prepare_project", {"a": 1}), Request("g", "prepare_project", {"a": 1}))
        self.assertNotEqual(Request("g"), Request("h"))


class GoalSpecTest(unittest.TestCase):
    def test_decided(self):
        spec = GoalSpec.decided("Prepare my project folder", "prepare_project", "rules", original_text="Prepare my project folder")
        self.assertFalse(spec.needs_clarification)
        self.assertEqual((spec.task_type, spec.source, spec.confidence), ("prepare_project", "rules", 1.0))
        self.assertEqual((spec.ambiguities, spec.candidates, dict(spec.parameters)), ((), (), {}))

    def test_unclear(self):
        spec = GoalSpec.unclear("goal", "rules", ["reason one"], candidates=["a", "b"], original_text="goal")
        self.assertTrue(spec.needs_clarification)
        self.assertEqual((spec.task_type, spec.confidence), (None, 0.0))
        self.assertEqual(spec.ambiguities, ("reason one",))
        self.assertEqual(spec.candidates, ("a", "b"))

    def test_sequences_become_tuples_and_mappings_become_read_only(self):
        reasons = ["why"]
        spec = GoalSpec.unclear("goal", "rules", reasons, original_text="goal")
        reasons.append("later change")
        self.assertEqual(spec.ambiguities, ("why",))
        decided = GoalSpec.decided("goal", "t", "explicit", parameters={"project_name": "bakery"}, original_text="goal")
        with self.assertRaises(TypeError):
            decided.parameters["project_name"] = "other"

    def test_is_frozen(self):
        spec = GoalSpec.decided("goal", "t", "rules", original_text="goal")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec.task_type = "other"

    def test_unclear_must_explain_why(self):
        with self.assertRaises(ValueError):
            GoalSpec.unclear("goal", "rules", [], original_text="goal")

    def test_inconsistent_shapes_are_rejected(self):
        with self.assertRaises(ValueError):  # Unclear but confident.
            GoalSpec(text="g", original_text="g", task_type=None, source="rules", confidence=0.5, ambiguities=("x",))
        with self.assertRaises(ValueError):  # Decided but with zero confidence.
            GoalSpec(text="g", original_text="g", task_type="t", source="rules", confidence=0.0)
        with self.assertRaises(ValueError):  # Decided but still ambiguous.
            GoalSpec(text="g", original_text="g", task_type="t", source="rules", confidence=1.0, ambiguities=("x",))
        with self.assertRaises(ValueError):  # Decided but with candidates.
            GoalSpec(text="g", original_text="g", task_type="t", source="rules", confidence=1.0, candidates=("a", "b"))

    def test_rejects_wrong_types_and_ranges(self):
        with self.assertRaises(ValueError):
            GoalSpec.decided("g", "t", "rules", confidence=1.5, original_text="g")
        for kwargs in ({"text": None, "original_text": "g", "task_type": "t", "source": "rules", "confidence": 1.0},
                       {"text": "g", "original_text": "g", "task_type": "", "source": "rules", "confidence": 1.0},
                       {"text": "g", "original_text": "g", "task_type": "t", "source": "", "confidence": 1.0},
                       {"text": "g", "original_text": "g", "task_type": "t", "source": "rules", "confidence": True},
                       {"text": "g", "original_text": "g", "task_type": "t", "source": "rules", "confidence": "1"},
                       {"text": "g", "original_text": "g", "task_type": None, "source": "rules", "ambiguities": "a string"},
                       {"text": "g", "original_text": "g", "task_type": None, "source": "rules", "ambiguities": (1,)},
                       {"text": "g", "original_text": "g", "task_type": "t", "source": "rules", "confidence": 1.0,
                        "parameters": {"k": object()}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                GoalSpec(**kwargs)


class FrozenMapTest(unittest.TestCase):
    """N1: an immutable mapping that can be hashed, copied, deep-copied and pickled."""

    def test_reads_like_a_dict(self):
        m = FrozenMap({"a": 1, "b": "x"})
        self.assertEqual((m["a"], len(m), list(m), dict(m)), (1, 2, ["a", "b"], {"a": 1, "b": "x"}))
        self.assertIn("a", m)
        self.assertEqual(m.get("missing", "default"), "default")
        self.assertEqual(FrozenMap(), {})
        self.assertEqual(FrozenMap(None), {})
        self.assertEqual(repr(FrozenMap({"a": 1})), "FrozenMap({'a': 1})")

    def test_cannot_be_changed(self):
        m = FrozenMap({"a": 1})
        with self.assertRaises(TypeError):
            m["a"] = 2
        with self.assertRaises(TypeError):
            del m["a"]
        for attribute in ("_data", "_hash", "anything"):
            with self.subTest(attribute=attribute), self.assertRaises(AttributeError):
                setattr(m, attribute, {})
        with self.assertRaises(AttributeError):
            del m._data
        self.assertFalse(hasattr(m, "__dict__"))  # __slots__: no hidden place to add attributes.
        self.assertEqual(dict(m), {"a": 1})

    def test_is_a_copy(self):
        source = {"a": 1}
        m = FrozenMap(source)
        source["a"] = 2
        source["b"] = 3
        self.assertEqual(dict(m), {"a": 1})

    def test_equality_and_hash_ignore_order(self):
        first, second = FrozenMap({"a": 1, "b": 2}), FrozenMap({"b": 2, "a": 1})
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(first, {"a": 1, "b": 2})
        self.assertNotEqual(first, FrozenMap({"a": 1}))
        self.assertNotEqual(first, [("a", 1), ("b", 2)])  # Not a mapping.
        self.assertEqual(len({first, second, FrozenMap({"a": 1})}), 2)

    def test_copy_deepcopy_and_pickle(self):
        m = FrozenMap({"s": "x", "i": 7, "b": False, "n": None})
        for label, clone in (("copy", copy.copy(m)), ("deepcopy", copy.deepcopy(m))):
            with self.subTest(label):
                self.assertEqual(clone, m)
                self.assertIsInstance(clone, FrozenMap)
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with self.subTest(protocol=protocol):
                restored = pickle.loads(pickle.dumps(m, protocol=protocol))
                self.assertEqual((restored, type(restored), hash(restored)), (m, FrozenMap, hash(m)))

    def test_rejects_anything_but_exact_scalar_values(self):
        class MyStr(str):
            pass

        Colour = enum.IntEnum("Colour", "RED")
        for data in ({1: "x"}, {MyStr("k"): "x"}, {"k": 1.5}, {"k": ["list"]}, {"k": {"nested": 1}},
                     {"k": ("tuple",)}, {"k": b"bytes"}, {"k": Colour.RED}, {"k": MyStr("v")}, {"k": object()}):
            with self.subTest(data=data), self.assertRaises(TypeError):
                FrozenMap(data)
        for not_a_mapping in ([("k", "v")], "text", 3):
            with self.subTest(value=not_a_mapping), self.assertRaises(TypeError):
                FrozenMap(not_a_mapping)

    def test_error_messages_name_the_field(self):
        with self.assertRaisesRegex(TypeError, r"^parameters\['k'\]"):
            FrozenMap({"k": 1.5}, name="parameters")


class StandardOperationsTest(unittest.TestCase):
    """N1: Request and GoalSpec work with hash, sets, copy, deepcopy, pickle, asdict and replace."""

    def examples(self):
        return [
            Request("Prepare my project folder"),
            Request("goal", "prepare_project", {"name": "bakery", "yes": True}),
            GoalSpec.decided("goal", "prepare_project", "rules", parameters={"project_name": "bakery"}, original_text="goal"),
            GoalSpec.unclear("goal", "rules", ["it matches more than one task type (a, b)"], candidates=["a", "b"], original_text="goal"),
        ]

    def test_hashable_and_usable_in_sets(self):
        for value in self.examples():
            with self.subTest(value=value):
                twin = copy.deepcopy(value)
                self.assertEqual(hash(value), hash(twin))
                self.assertEqual(len({value, twin}), 1)
                self.assertEqual({value: "ok"}[twin], "ok")

    def test_copy_deepcopy_and_pickle(self):
        for value in self.examples():
            for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
                with self.subTest(value=value, protocol=protocol):
                    restored = pickle.loads(pickle.dumps(value, protocol=protocol))
                    self.assertEqual(restored, value)
                    self.assertEqual(hash(restored), hash(value))
            with self.subTest(value=value):
                self.assertEqual(copy.copy(value), value)
                self.assertEqual(copy.deepcopy(value), value)

    def test_asdict_and_replace(self):
        spec = GoalSpec.decided("goal", "t", "rules", parameters={"k": "v"}, original_text="goal")
        as_dict = dataclasses.asdict(spec)
        self.assertEqual(as_dict["parameters"], {"k": "v"})
        self.assertEqual(as_dict["ambiguities"], ())
        changed = dataclasses.replace(spec, text="other", original_text="other")
        self.assertEqual((changed.text, changed.parameters), ("other", {"k": "v"}))
        self.assertIsInstance(changed.parameters, FrozenMap)

    def test_fields_hold_frozen_maps(self):
        self.assertIsInstance(Request("g", options={"a": 1}).options, FrozenMap)
        self.assertIsInstance(Request("g").options, FrozenMap)
        self.assertIsInstance(GoalSpec.decided("g", "t", "rules", original_text="g").parameters, FrozenMap)

    def test_string_subclasses_are_rejected(self):
        class MyStr(str):
            pass

        with self.assertRaises(TypeError):
            Request(MyStr("goal"))
        with self.assertRaises(TypeError):
            GoalSpec.unclear("g", "rules", [MyStr("why")], original_text="g")


class OriginalTextTest(unittest.TestCase):
    """Q1: GoalSpec keeps the user's exact input; `text` is its NFC-normalised form."""

    COMPOSED = "Pr\u00e9pare caf\u00e9"        # "Prépare café" with precomposed é.
    DECOMPOSED = "Pre\u0301pare cafe\u0301"    # The same text with e + combining acute accent.

    def test_normalize_is_nfc_then_whitespace(self):
        self.assertEqual(normalize_goal_text("  a \n\t b  "), "a b")
        self.assertEqual(normalize_goal_text(self.DECOMPOSED), self.COMPOSED)
        self.assertEqual(normalize_goal_text(self.COMPOSED), self.COMPOSED)
        self.assertEqual(normalize_goal_text(""), "")
        self.assertEqual(normalize_goal_text(" \u3000\u2003 "), "")  # Unicode spaces count as whitespace.

    def test_nfc_only_never_nfkc(self):
        # NFKC would turn these into different, "compatible" characters; NFC keeps them.
        for text in ("\ufb01le", "x\u00b2", "\u2460", "\uff21", "\u212b"):
            with self.subTest(text=text):
                self.assertEqual(normalize_goal_text(text), unicodedata.normalize("NFC", text))
        self.assertEqual(normalize_goal_text("\ufb01le"), "\ufb01le")         # Not "file".
        self.assertNotEqual(normalize_goal_text("\ufb01le"), unicodedata.normalize("NFKC", "\ufb01le"))

    def test_normalisation_is_deterministic_and_idempotent(self):
        for text in (self.DECOMPOSED, self.COMPOSED, "  mixed \u0041\u030a  spacing\n", "\u212b"):
            with self.subTest(text=text):
                once = normalize_goal_text(text)
                self.assertEqual(normalize_goal_text(once), once)
                self.assertEqual(normalize_goal_text(text), once)
                self.assertTrue(unicodedata.is_normalized("NFC", once))

    def test_original_survives_exactly(self):
        original = "  " + self.DECOMPOSED + "\n\tmore  "
        spec = GoalSpec.decided(normalize_goal_text(original), "t", "rules", original_text=original)
        self.assertEqual(spec.original_text, original)        # Spacing and decomposed form kept.
        self.assertEqual(spec.text, self.COMPOSED + " more")
        self.assertNotEqual(spec.original_text, spec.text)

    def test_composed_and_decomposed_give_equal_text_but_different_originals(self):
        first = GoalSpec.decided(self.COMPOSED, "t", "rules", original_text=self.COMPOSED)
        second = GoalSpec.decided(self.COMPOSED, "t", "rules", original_text=self.DECOMPOSED)
        self.assertEqual(first.text, second.text)
        self.assertNotEqual(first, second)  # The original input is part of the value.

    def test_text_must_match_the_original(self):
        for text, original in (("other", "goal"), (self.DECOMPOSED, self.DECOMPOSED), ("a  b", "a  b"),
                               ("", "x"), ("x", "")):
            with self.subTest(text=text, original=original), self.assertRaises(ValueError):
                GoalSpec.decided(text, "t", "rules", original_text=original)
        with self.assertRaises(ValueError):
            dataclasses.replace(GoalSpec.decided("goal", "t", "rules", original_text="goal"), text="other")

    def test_empty_and_whitespace_only_originals(self):
        for original in ("", "   ", "\n\t", "\u3000"):
            with self.subTest(original=original):
                spec = GoalSpec.unclear("", "rules", ["the goal must not be empty"], original_text=original)
                self.assertEqual((spec.text, spec.original_text), ("", original))

    def test_original_text_is_required_and_must_be_a_string(self):
        with self.assertRaises(TypeError):
            GoalSpec(text="g", task_type="t", source="rules", confidence=1.0)
        for bad in (None, b"g", 1):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                GoalSpec(text="g", original_text=bad, task_type="t", source="rules", confidence=1.0)
        with self.assertRaises(TypeError):
            normalize_goal_text(None)

    def test_original_survives_pickle_and_copy(self):
        spec = GoalSpec.decided(self.COMPOSED, "t", "rules", original_text="  " + self.DECOMPOSED)
        for clone in (copy.deepcopy(spec), pickle.loads(pickle.dumps(spec))):
            self.assertEqual(clone.original_text, "  " + self.DECOMPOSED)


if __name__ == "__main__":
    unittest.main()
