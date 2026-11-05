"""Safety components for ARTIF. 1.0 (ARCHITECTURE_1.0.md §4.8).

Implemented so far: the lexical path check only (orbisent.safety.sandbox). It is
pure string checking, not a sandbox; see that module's docstring.
"""

from orbisent.safety.sandbox import check_relative_path, parent_keys, path_key

__all__ = ["check_relative_path", "parent_keys", "path_key"]
