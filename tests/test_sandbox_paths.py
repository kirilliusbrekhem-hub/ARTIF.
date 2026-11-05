"""Tests for the lexical path check (ARCHITECTURE_1.0.md §4.8.1 step 1, §12.5).

These checks look at path strings only. They are not a sandbox: they can't see
symlinks, hard links or races. The run-time no-follow walk is a later component.

Run from the repository root:
    python3 -m unittest -v
"""

import unittest

import orbisent.safety.sandbox as sandbox_module
from orbisent.safety.sandbox import MAX_COMPONENT_BYTES, MAX_PATH_BYTES, check_relative_path, parent_keys, path_key
from tests import support


class ValidPathTest(unittest.TestCase):
    VALID = ["demo", "demo/README.md", "a/b/c/d.txt", "my-project/TASKS.md", "x.tar.gz", "caf\u00e9/men\u00fa.md",
             "\u043f\u0440\u043e\u0435\u043a\u0442/file", "a" * MAX_COMPONENT_BYTES, ".hidden", "...x", "con-tract",
             "console.md", "COM10", "LPT0", "name with spaces/ok", " leading-space", "a/.b/c"]

    def test_valid_paths(self):
        for path in self.VALID:
            with self.subTest(path=path):
                self.assertEqual(check_relative_path(path), ())


class InvalidPathTest(unittest.TestCase):
    def assert_invalid(self, path, fragment):
        problems = check_relative_path(path)
        self.assertTrue(problems, f"{path!r} should be invalid")
        self.assertTrue(any(fragment in p for p in problems), f"{path!r}: {problems}")

    def test_traversal(self):
        for path in ("..", "../x", "a/../b", "a/..", ".", "./a", "a/./b", "a/b/../../.."):
            with self.subTest(path=path):
                self.assert_invalid(path, "not allowed")

    def test_absolute_paths(self):
        for path in ("/etc/passwd", "/", "//server/share"):
            with self.subTest(path=path):
                self.assert_invalid(path, "relative")

    def test_drive_letters_unc_and_streams(self):
        for path in ("C:/x", "c:x", "a/b:stream", "C:\\Windows"):
            with self.subTest(path=path):
                self.assert_invalid(path, "':'")

    def test_invalid_separators(self):
        for path in ("a\\b", "\\\\server\\share", "a/b\\..\\c"):
            with self.subTest(path=path):
                self.assert_invalid(path, "'/' as its separator")

    def test_empty_parts(self):
        for path in ("a//b", "a/", "a/b/", "/"):
            with self.subTest(path=path):
                self.assert_invalid(path, "empty")
        self.assertEqual(check_relative_path(""), ("path must not be empty",))

    def test_control_characters(self):
        for char in ("\x00", "\x01", "\t", "\n", "\r", "\x1b", "\x7f", "\x85", "\x9f"):
            with self.subTest(char=ascii(char)):
                self.assert_invalid(f"a{char}b", "control character")

    def test_windows_reserved_names(self):
        for path in ("CON", "con", "Con.txt", "prn", "AUX.tar.gz", "nul", "a/NUL/b", "COM1", "com9.log",
                     "LPT1", "lpt9.txt"):
            with self.subTest(path=path):
                self.assert_invalid(path, "reserved Windows device name")

    def test_trailing_dot_or_space(self):
        for path in ("name.", "dir./x", "name ", "a/b /c", "x.."):
            with self.subTest(path=path):
                self.assert_invalid(path, "ends with a dot or a space")

    def test_component_length_in_utf8_bytes(self):
        self.assertEqual(check_relative_path("a" * MAX_COMPONENT_BYTES), ())
        self.assert_invalid("a" * (MAX_COMPONENT_BYTES + 1), "longer than 255")
        # 128 two-byte characters = 256 bytes: too long, although only 128 characters.
        self.assert_invalid("\u00e9" * 128, "longer than 255")
        self.assertEqual(check_relative_path("\u00e9" * 127), ())

    def test_whole_path_length_in_utf8_bytes(self):
        exact = "/".join(["a" * 255] * 3 + ["a" * 254, "b"])  # 3*255 + 254 + 1 + 4 separators = 1024.
        self.assertEqual(len(exact.encode()), MAX_PATH_BYTES)
        self.assertEqual(check_relative_path(exact), ())
        self.assert_invalid(exact + "b", f"longer than {MAX_PATH_BYTES}")

    def test_unicode_normalisation_and_validity(self):
        self.assert_invalid("cafe\u0301", "NFC")
        self.assertEqual(check_relative_path("caf\u00e9"), ())
        self.assertEqual(check_relative_path("bad\ud800"), ("path contains a lone surrogate, which is not valid Unicode",))

    def test_non_strings(self):
        for value in (None, b"demo", 3, ["demo"]):
            with self.subTest(value=value):
                self.assertTrue(check_relative_path(value)[0].startswith("path must be a string"))

    def test_every_problem_is_reported(self):
        problems = check_relative_path("/CON./a\\b")
        self.assertEqual(len(problems), 4)  # Absolute, backslash, trailing dot, reserved name.


class PathKeyTest(unittest.TestCase):
    def test_duplicate_detection_key(self):
        self.assertEqual(path_key("Demo/README.md"), path_key("demo/readme.md"))
        self.assertEqual(path_key("STRASSE"), path_key("stra\u00dfe"))  # Case-folding, not just lower().
        self.assertEqual(path_key("caf\u00e9"), path_key("caf\u00c9"))
        self.assertNotEqual(path_key("a/b"), path_key("a-b"))
        self.assertEqual(path_key("a/b/c"), ("a", "b", "c"))

    def test_parent_keys(self):
        self.assertEqual(parent_keys("a/b/c"), [("a",), ("a", "b")])
        self.assertEqual(parent_keys("a"), [])
        self.assertEqual(parent_keys("A/b"), [("a",)])


class NoFileSystemAccessTest(unittest.TestCase):
    """Behavioural: path checks never touch the disk, even for paths that exist."""

    PATHS = ["README.md", "orbisent/types.py", "../README.md", "/etc/passwd", "/", "prototype", "tests",
             "a" * 2000, "nonexistent/x", "C:\\Windows\\System32", "x\x00y", "CON"]

    def test_checks_run_without_any_side_effect(self):
        with support.forbid_side_effects(modules=[sandbox_module]) as attempts:
            results = [(sandbox_module.check_relative_path(p), sandbox_module.path_key("a/B"),
                        sandbox_module.parent_keys("a/b/c")) for p in self.PATHS]
        self.assertEqual(attempts, [])
        self.assertEqual(results[0][0], ())          # "README.md" is valid as a string...
        self.assertTrue(results[3][0])               # ...and "/etc/passwd" is not, without looking at either.

    def test_the_guard_would_have_caught_a_lookup(self):
        import os.path
        with support.forbid_side_effects(modules=[sandbox_module]) as attempts:
            with self.assertRaises(support.SideEffectError):
                os.path.exists("README.md")
        self.assertEqual(attempts, ["posixpath.exists"])


if __name__ == "__main__":
    unittest.main()
