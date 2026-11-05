"""Tool descriptors for ARTIF. 1.0 (ARCHITECTURE_1.0.md §4.5 and §12.3).

This package currently holds *descriptions* of tools only: their names, permission
levels and arguments. Planning and validation use them as data. Nothing here can
run a tool; real tool implementations arrive with the execution engine, later.
"""

from orbisent.tools.base import ARG_KINDS, ARTIF_1_0_PERMISSIONS, ArgSpec, Permission, ToolInfo
from orbisent.tools.filesystem import (
    FILESYSTEM_TOOLS,
    FS_CREATE_FILE,
    FS_CREATE_FOLDER,
    FS_LIST_DIR,
    FS_READ_FILE,
)

__all__ = [
    "ARG_KINDS",
    "ARTIF_1_0_PERMISSIONS",
    "ArgSpec",
    "FILESYSTEM_TOOLS",
    "FS_CREATE_FILE",
    "FS_CREATE_FOLDER",
    "FS_LIST_DIR",
    "FS_READ_FILE",
    "Permission",
    "ToolInfo",
]
