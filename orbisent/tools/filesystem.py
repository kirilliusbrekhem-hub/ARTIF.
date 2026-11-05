"""Descriptors for the four ARTIF. 1.0 file-system tools (ARCHITECTURE_1.0.md §4.5).

Descriptors only. There is no implementation here: nothing in this module reads,
writes or lists anything. The real tools, with the no-follow sandbox walk of
§4.8.1, are added with the execution engine.
"""

from orbisent.tools.base import ArgSpec, Permission, ToolInfo

MAX_PATH_BYTES = 1024          # Whole path, UTF-8 (§12.5).
MAX_CONTENT_BYTES = 64 * 1024  # One file's content, UTF-8 (§12.5).

FS_CREATE_FOLDER = ToolInfo(
    name="fs.create_folder",
    description="Create a folder inside the workspace; an existing folder is left as it is.",
    permission=Permission.CREATE,
    args=(ArgSpec("path", "target_path", True, MAX_PATH_BYTES),),
    produces_content=False,
)

FS_CREATE_FILE = ToolInfo(
    name="fs.create_file",
    description="Create a new file inside the workspace; an existing file is never overwritten.",
    permission=Permission.CREATE,
    args=(ArgSpec("path", "target_path", True, MAX_PATH_BYTES),
          ArgSpec("content", "text", True, MAX_CONTENT_BYTES)),
    produces_content=True,
)

FS_READ_FILE = ToolInfo(
    name="fs.read_file",
    description="Read a UTF-8 text file inside the workspace.",
    permission=Permission.READ,
    args=(ArgSpec("path", "source_path", True, MAX_PATH_BYTES),),
    produces_content=False,
)

FS_LIST_DIR = ToolInfo(
    name="fs.list_dir",
    description="List the entries of one folder inside the workspace (not recursively).",
    permission=Permission.READ,
    args=(ArgSpec("path", "source_path", True, MAX_PATH_BYTES),),
    produces_content=False,
)

FILESYSTEM_TOOLS = (FS_CREATE_FOLDER, FS_CREATE_FILE, FS_READ_FILE, FS_LIST_DIR)
