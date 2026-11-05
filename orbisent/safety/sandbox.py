"""Lexical path checks for plan paths (ARCHITECTURE_1.0.md §4.8.1 step 1, §12.5).

These functions look at a path *string* only. They never touch the file system:
no resolve(), no stat(), no symlink checks. That makes them safe to run while
planning, but it also means they are NOT a sandbox. They can't see symlinks, hard
links, mount points or races; the descriptor-based no-follow walk of §4.8.1, built
with the execution engine, is what protects the file system at run time.

A valid plan path is workspace-relative and uses "/" as its only separator.
"""

import unicodedata

MAX_COMPONENT_BYTES = 255
MAX_PATH_BYTES = 1024

# Windows device names, reserved with or without an extension ("CON", "con.txt").
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{n}" for n in range(1, 10)} | {f"LPT{n}" for n in range(1, 10)}
)


def check_relative_path(path):
    """Return the problems with a plan path, as a tuple of messages. () means it is valid.

    Rules, checked in this order:
      - it is a non-empty str, valid Unicode (no lone surrogates) and NFC-normalised;
      - it contains no NUL or other control character, no backslash and no colon
        (so no drive letters, UNC paths or alternate data streams);
      - it does not start with "/" (relative only) and is at most 1024 UTF-8 bytes;
      - every "/"-separated part is non-empty, not "." or "..", at most 255 UTF-8 bytes,
        does not end with a dot or a space, and is not a Windows-reserved name
        (CON, PRN, AUX, NUL, COM1-COM9, LPT1-LPT9, with or without an extension).
    """
    if type(path) is not str:
        return (f"path must be a string, not {type(path).__name__}",)
    if not path:
        return ("path must not be empty",)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in path):
        return ("path contains a lone surrogate, which is not valid Unicode",)

    problems = []
    if unicodedata.normalize("NFC", path) != path:
        problems.append("path must be NFC-normalised Unicode")
    if any(unicodedata.category(char) == "Cc" for char in path):
        problems.append("path contains a NUL or other control character")
    if "\\" in path:
        problems.append("path must use '/' as its separator, not '\\'")
    if ":" in path:
        problems.append("path must not contain ':' (drive letters and streams are not allowed)")
    if path.startswith("/"):
        problems.append("path must be relative, not absolute")
    if len(path.encode("utf-8")) > MAX_PATH_BYTES:
        problems.append(f"path is longer than {MAX_PATH_BYTES} UTF-8 bytes")

    parts = path[1:].split("/") if path.startswith("/") else path.split("/")
    for index, part in enumerate(parts, start=1):
        if not part:
            problems.append(f"part {index} is empty (no leading, trailing or double '/')")
        elif part in (".", ".."):
            problems.append(f"part {index} is {part!r}, which is not allowed")
        else:
            if len(part.encode("utf-8")) > MAX_COMPONENT_BYTES:
                problems.append(f"part {index} is longer than {MAX_COMPONENT_BYTES} UTF-8 bytes")
            if part.endswith((".", " ")):
                problems.append(f"part {index} ends with a dot or a space")
            if part.split(".", 1)[0].upper() in _RESERVED_NAMES:
                problems.append(f"part {index} ({part!r}) is a reserved Windows device name")
    return tuple(problems)


def path_key(path):
    """The key used to detect duplicate targets: NFC, then case-folded, part by part.

    Two paths with the same key would refer to the same file on a case-insensitive
    file system (macOS and Windows usually are), so a plan may not target both.
    Only call this on a path that check_relative_path() accepted.
    """
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.split("/"))


def parent_keys(path):
    """The keys of every folder that contains `path`, outermost first ("a/b/c" -> a, a/b)."""
    key = path_key(path)
    return [key[:length] for length in range(1, len(key))]
