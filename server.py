import logging
import msgpack
import socket
import os
import random
import shutil
import re
from pathlib import Path

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from mcp.server.fastmcp import FastMCP, Context


logger = logging.getLogger(__name__)
logger.setLevel(level=logging.DEBUG)


class NvimConnection:
    """A minimal, synchronous connection to Neovim avoiding event loops"""

    def __init__(self, socket_name: str = "neovim.socket"):
        self.sock = None
        self.project_dir = ""  # Store the project directory
        self.socket_path = ""
        self.socket_name = socket_name
        self.request_id = random.randint(1, 10000)
        # Add a flag to track if diff mode is active and store original buffer reference
        self.diff_active = False
        self.diff_buffer_id = None

    def set_socket_path(self, path: str):
        self.project_dir = path  # Store the project directory
        self.socket_path = str(Path(path) / self.socket_name)
        if os.path.exists(self.socket_path):
            return path

        raise FileNotFoundError(
            "No Neovim socket found. Start Neovim with --listen option."
        )

    def connect(self):
        """Connect to the Neovim socket"""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)
        self.sock.settimeout(1)  # Set a timeout of 5 seconds
        return self

    def close(self):
        """Close the connection"""
        if not self.socket_path:
            raise Exception("You must initialize the path")

        if self.sock:
            self.sock.close()
            self.sock = None

    def _send_request(self, method, params):
        """Send a msgpack-rpc request"""
        if not self.sock:
            self.connect()

        req_id = self.request_id
        self.request_id += 1

        # msgpack-rpc message format: [type, msgid, method, params]
        request = msgpack.packb([0, req_id, method, params])

        logger.debug("Sending message: %s", [0, req_id, method, params])
        self.sock.sendall(request)

        # Read response
        data = b""
        chunk_idx = 0
        while True:
            logger.debug("Reading chunk %d", chunk_idx)
            chunk_idx += 1
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                logger.error("Socket recv timed out")
                break
            logger.debug(f"Received chunk {chunk}")
            if not chunk:
                break
            data += chunk

        try:
            # Try to unpack - if it works, we have a complete message
            full_response = msgpack.unpackb(data, raw=False)
            response = full_response[3]
            return response
        except msgpack.exceptions.OutOfData:
            # Need more data
            logger.debug("Need more data, waiting for more chunks")
            raise
            # finally:
            #     logger.debug("Response from neovim: %s", full_response)

    def execute_command(self, cmd):
        """Execute a Vim command"""
        return self._send_request("nvim_command", [cmd])

    def eval_expr(self, expr):
        """Evaluate a Vim expression"""
        return self._send_request("nvim_eval", [expr])

    def get_current_buffer(self) -> int:
        """Get the current buffer number"""
        buffer_id = self._send_request("nvim_get_current_buf", [])
        if isinstance(buffer_id, msgpack.ExtType):
            buffer_id = int.from_bytes(buffer_id.data, byteorder="big")
        return buffer_id

    def get_buffer_lines(self, buffer_id, start, end):
        """Get lines from a buffer"""
        return self._send_request("nvim_buf_get_lines", [buffer_id, start, end, True])

    def set_buffer_lines(self, buffer_id, start, end, lines):
        """Set lines in a buffer"""
        return self._send_request(
            "nvim_buf_set_lines", [buffer_id, start, end, True, lines]
        )

    def get_cursor(self):
        """Get the current cursor position"""
        # Get the current window
        win = self._send_request("nvim_get_current_win", [])
        # Get cursor position (returns [row, col])
        return self._send_request("nvim_win_get_cursor", [win])

    def set_cursor(self, row, col):
        """Set the cursor position"""
        win = self._send_request("nvim_get_current_win", [])
        return self._send_request("nvim_win_set_cursor", [win, [row, col]])

    def get_visual_selection(self):
        """
        Get the line range of the current visual selection with the unusual format.
        Returns a tuple of (start_line, end_line) in 0-indexed format.
        """
        try:
            # Get position of the beginning of visual selection (mark '<)
            cursor_pos = self._send_request("nvim_call_function", ["getpos", ["."]])

            # Get position of the end of visual selection (mark '>)
            visual_end_pos = self._send_request("nvim_call_function", ["getpos", ["v"]])

            # Extract line numbers (0-indexed for API consistency)
            # In this unusual format, line numbers are at [3][1]
            start_line = cursor_pos[1] - 1  # Convert from 1-indexed to 0-indexed
            end_line = visual_end_pos[1] - 1  # End is exclusive for API purposes

            if start_line > end_line:
                return (end_line, start_line)

            return (start_line, end_line)
        except Exception as e:
            raise RuntimeError(f"Error getting visual selection: {str(e)}")


@dataclass
class AppContext:
    nvim: NvimConnection


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle with type-safe context"""
    # Initialize on startup
    nvim = NvimConnection()
    logger.debug("Creating lifespan objects")
    try:
        # nvim = attach("socket", path="/tmp/neovim")
        yield AppContext(nvim=nvim)
    except Exception as c:
        logger.exception("Failed")
    finally:
        # Cleanup on shutdown
        nvim.close()


server = FastMCP("neovim", lifespan=app_lifespan)


@server.tool()
def reset_diff_state(ctx: Context) -> str:
    """Reset and clean up any active diff views.

    This tool ensures that all diff views are closed and the diff state is reset,
    useful for cleaning up after multiple update_with_context calls.

    Returns:
        A message indicating the action taken
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    # Turn off diff mode in all windows
    nvim.execute_command("diffoff!")

    # Close all other windows except the current one
    nvim.execute_command("only")

    # Reset the diff state
    nvim.diff_active = False
    nvim.diff_buffer_id = None

    return "Diff state reset and all diff windows closed"


@server.tool()
def initialize_neovim_connection(ctx: Context, path: str) -> str:
    """Initialize the project path.

    You must use this tool if you see an error indicating that you must initialize the path or that the socket is not connected.

    The path must be provided to you by the user as PROJECT_PATH. If not, just skip using any other neovim tools until it si provided to you.
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    nvim.set_socket_path(path)
    return f"Neovim configured correctly at {path}"


@server.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting randomly chosen from five options"""
    greetings = [
        f"Hello {name}!",
        f"Hi there, {name}!",
        f"Greetings, {name}!",
        f"Welcome, {name}!",
        f"Nice to meet you, {name}!",
    ]
    return random.choice(greetings)


@server.tool()
def read_buffer(ctx: Context):
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buffer_id = nvim.get_current_buffer()
    logger.info(f"Reading buffer {buffer_id}")
    result = nvim.get_buffer_lines(buffer_id, 0, -1)
    result = "\n".join(result)
    logger.debug(f"Buffer content: {result}")
    return result


@server.tool()
def read_selected_text(ctx: Context) -> str:
    """Get the currently selected lines in the visible buffer.

    The result is the text that is selected.
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    start, end = nvim.get_visual_selection()
    if start is None or end is None:
        return ""
    buffer_id = nvim.get_current_buffer()
    logger.debug(f"Reading selection from buffer id {buffer_id} {start}:{end}")
    result = nvim.get_buffer_lines(buffer_id, start, end)
    result = "\n".join(result)
    logger.debug("This is the result from read_selected_text: %s", result)
    return result


@server.tool()
def get_current_buffer_id(ctx: Context) -> int:
    """Get the current buffer ID."""
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buffer_id = nvim.get_current_buffer()
    logger.debug(f"Current buffer ID: {buffer_id}")
    return buffer_id


# @server.tool()
# def toggle_diff_mode(ctx: Context, enable: bool = True) -> str:
#     """Toggle diff mode between current buffer and its previous state.

#     Args:
#         enable: True to enable diff mode, False to disable

#     Returns:
#         A message indicating the action taken
#     """
#     nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

#     if enable:
#         # Create a snapshot of current buffer
#         buffer_id = nvim.get_current_buffer()
#         current_lines = nvim.get_buffer_lines(buffer_id, 0, -1)

#         # Create a new buffer for comparison
#         nvim.execute_command("vnew")
#         temp_buffer_id = nvim.get_current_buffer()
#         nvim.set_buffer_lines(temp_buffer_id, 0, -1, current_lines)

#         # Enable diff mode
#         nvim.execute_command("diffthis")
#         nvim.execute_command("wincmd p")  # Go back to previous window
#         nvim.execute_command("diffthis")

#         # Set the flag and store the diff buffer ID
#         nvim.diff_active = True
#         nvim.diff_buffer_id = temp_buffer_id

#         return "Diff mode enabled. Left buffer shows current state."
#     else:
#         # Disable diff mode
#         nvim.execute_command("diffoff!")
#         nvim.diff_active = False
#         nvim.diff_buffer_id = None
#         return "Diff mode disabled."


@server.tool()
def list_buffers(ctx: Context) -> list[dict]:
    """List all available buffers in the current Neovim session.

    Returns:
        A list of dictionaries containing buffer information including:
        - id: The buffer identifier
        - name: The buffer name/path
        - modified: Whether the buffer has unsaved changes
        - current: Whether this is the active buffer
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    # Get current buffer for comparison
    current_buffer = nvim.get_current_buffer()

    # Get a list of all buffers using Vim's built-in functions
    buffer_list = nvim.eval_expr("getbufinfo()")
    logger.debug(f"Raw buffer list: {buffer_list}")

    # Process the buffer information into a cleaner format
    result = []
    for buf in buffer_list:
        # Extract the buffer ID - handle ExtType objects if needed
        buf_id = buf.get("bufnr")

        # Check if this buffer is the current one
        is_current = buf_id == current_buffer

        # Get buffer information
        name = buf.get("name", "")
        modified = buf.get("changed", 0) == 1
        listed = buf.get("listed", 0) == 1

        # Only include listed buffers (skip internal ones)
        if listed:
            result.append(
                {
                    "id": buf_id,
                    "name": name,
                    "modified": modified,
                    "current": is_current,
                }
            )

    return result


@server.tool()
def get_project_tree(ctx: Context) -> str:
    """Execute git ls-tree command to show the file tree from git.

    Executes 'git ls-tree -r -l HEAD' and returns the formatted output.
    The command runs in the directory of the current buffer.

    Returns:
        String output of the git ls-tree command showing file modes, types, SHAs, sizes and paths
    """
    import subprocess

    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Use the project directory that's already stored in the connection
        if not nvim.project_dir:
            return "Error: Project directory not initialized. Use initialize_neovim_connection first."

        cwd = nvim.project_dir
        logger.debug(f"Running git command in project directory: {cwd}")

        # Execute the git ls-tree command in the project directory
        process = subprocess.Popen(
            ["git", "ls-tree", "-r", "-l", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            cwd=cwd,
        )

        # Get the output and any error messages
        stdout, stderr = process.communicate()

        # Check if the command was successful
        if process.returncode != 0:
            logger.error(f"Git command failed: {stderr}")
            return f"Error executing git command in {cwd}: {stderr}"

        # Return the command output
        return stdout.strip()
    except Exception as e:
        logger.exception("Error executing git command")
        return f"Exception while executing git command: {str(e)}"


@server.tool()
def create_directory(ctx: Context, path: str) -> str:
    """Create a new directory or ensure it exists in the project.

    Args:
        path: Relative or absolute path to the directory to create

    Returns:
        A message indicating success or that the directory already exists
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Determine if the path is absolute or relative
        if os.path.isabs(path):
            dir_path = Path(path)
        else:
            # Use the project directory as base for relative paths
            if not nvim.project_dir:
                return "Error: Project directory not initialized. Use initialize_neovim_connection first."
            dir_path = Path(nvim.project_dir) / path

        # Create the directory and any parent directories
        dir_path.mkdir(parents=True, exist_ok=True)

        return f"Directory created/verified at: {dir_path}"
    except Exception as e:
        logger.exception("Error creating directory")
        return f"Error creating directory: {str(e)}"


@server.tool()
def create_file(ctx: Context, path: str, content: str = "") -> str:
    """Create a new file with optional content in the project.

    Args:
        path: Relative or absolute path to the file to create
        content: Optional content to write to the file

    Returns:
        A message indicating success or failure
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Determine if the path is absolute or relative
        if os.path.isabs(path):
            file_path = Path(path)
        else:
            # Use the project directory as base for relative paths
            if not nvim.project_dir:
                return "Error: Project directory not initialized. Use initialize_neovim_connection first."
            file_path = Path(nvim.project_dir) / path

        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write content to the file
        file_path.write_text(content)

        return f"File created at: {file_path}"
    except Exception as e:
        logger.exception("Error creating file")
        return f"Error creating file: {str(e)}"


@server.tool()
def delete_file(ctx: Context, path: str, force: bool = False) -> str:
    """Delete a file from the project.

    Args:
        path: Relative or absolute path to the file to delete
        force: If True, don't raise an error if the file doesn't exist

    Returns:
        A message indicating success or failure
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Determine if the path is absolute or relative
        if os.path.isabs(path):
            file_path = Path(path)
        else:
            # Use the project directory as base for relative paths
            if not nvim.project_dir:
                return "Error: Project directory not initialized. Use initialize_neovim_connection first."
            file_path = Path(nvim.project_dir) / path

        if not str(file_path.absolute()).startswith(nvim.project_dir):
            return f"Error: The file to delete must be under {nvim.project_dir}"

        # Check if file exists
        if not file_path.exists():
            if force:
                return f"File {file_path} does not exist, no action taken."
            else:
                return f"Error: File {file_path} does not exist."

        # Delete the file
        file_path.unlink()

        return f"File deleted: {file_path}"
    except Exception as e:
        logger.exception("Error deleting file")
        return f"Error deleting file: {str(e)}"


@server.tool()
def delete_directory(ctx: Context, path: str, recursive: bool = False) -> str:
    """Delete a directory from the project.

    Args:
        path: Relative or absolute path to the directory to delete
        recursive: If True, recursively delete the directory and its contents

    Returns:
        A message indicating success or failure
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Determine if the path is absolute or relative
        if os.path.isabs(path):
            dir_path = Path(path)
        else:
            # Use the project directory as base for relative paths
            if not nvim.project_dir:
                return "Error: Project directory not initialized. Use initialize_neovim_connection first."
            dir_path = Path(nvim.project_dir) / path

        if not str(dir_path.absolute()).startswith(nvim.project_dir):
            return f"Error: The directory to delete must be under {nvim.project_dir}"

        # Check if directory exists
        if not dir_path.exists():
            return f"Error: Directory {dir_path} does not exist."

        # Delete the directory
        if recursive:
            shutil.rmtree(dir_path)
        else:
            dir_path.rmdir()  # Will fail if directory is not empty

        return f"Directory deleted: {dir_path}"
    except Exception as e:
        logger.exception("Error deleting directory")
        return f"Error deleting directory: {str(e)}"


@server.tool()
def list_directory_contents(ctx: Context, path: str = ".") -> list | str:
    """List contents of a directory in the project.

    Args:
        path: Relative or absolute path to the directory to list

    Returns:
        A list of dictionaries with file/directory information or a string containing describing any errors ocurred during execution.
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Determine if the path is absolute or relative
        if os.path.isabs(path):
            dir_path = Path(path)
        else:
            # Use the project directory as base for relative paths
            if not nvim.project_dir:
                return "Error: Project directory not initialized. Use initialize_neovim_connection first."
            dir_path = Path(nvim.project_dir) / path

        # Check if directory exists
        if not dir_path.exists():
            return f"Error: Directory {dir_path} does not exist."

        # List contents of the directory
        contents = []
        for item in dir_path.iterdir():
            contents.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )

        return contents
    except Exception as e:
        logger.exception("Error listing directory contents")
        return f"Error listing directory contents: {str(e)}"


@server.tool()
def read_file_content(ctx: Context, path: str) -> str:
    """Read content of a file in the project.

    Args:
        path: Relative or absolute path to the file to read

    Returns:
        The content of the file as a string
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Determine if the path is absolute or relative
        if os.path.isabs(path):
            file_path = Path(path)
        else:
            # Use the project directory as base for relative paths
            if not nvim.project_dir:
                return "Error: Project directory not initialized. Use initialize_neovim_connection first."
            file_path = Path(nvim.project_dir) / path

        # Check if file exists
        if not file_path.exists():
            return f"Error: File {file_path} does not exist."

        # Read the file content
        content = file_path.read_text()

        return content
    except Exception as e:
        logger.exception("Error reading file content")
        return f"Error reading file content: {str(e)}"


@server.tool()
def open_file_in_neovim(ctx: Context, path: str) -> str:
    """Open a file in the Neovim instance.

    Args:
        path: Relative or absolute path to the file to open

    Returns:
        A message indicating success or failure
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Determine if the path is absolute or relative
        if os.path.isabs(path):
            file_path = Path(path)
        else:
            # Use the project directory as base for relative paths
            if not nvim.project_dir:
                return "Error: Project directory not initialized. Use initialize_neovim_connection first."
            file_path = Path(nvim.project_dir) / path

        # Check if file exists
        if not file_path.exists():
            return f"Error: File {file_path} does not exist."

        # Open the file in Neovim
        nvim.execute_command(f"edit {file_path}")

        return f"File opened in Neovim: {file_path}"
    except Exception as e:
        logger.exception("Error opening file in Neovim")
        return f"Error opening file in Neovim: {str(e)}"


@server.tool()
def update_with_context(
    ctx: Context, before_context: str, content: str, after_context: str
) -> str:
    """Update text using context before and after the target location.

    IMPORTANT: The before_context and after_context strings are preserved in the output.
    Only the text between them is replaced with the new content. These strings serve both
    as search anchors and as boundaries that remain unchanged during the edit.

    Make sure to include any new lines, tabs and spaces that make the result valid.

    You must call this tool as many times as blocks of code you want to update.

    Args:
        before_context: Text immediately before the target location (preserved in output)
        content: New content to insert between the context boundaries
        after_context: Text immediately after the target location (preserved in output)

    Returns:
        A message describing the update
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buffer_id = nvim.get_current_buffer()

    # Get current content
    current_lines = nvim.get_buffer_lines(buffer_id, 0, -1)
    current_content = "\n".join(current_lines)

    # Store original content for the first call only
    original_lines = ""
    if not nvim.diff_active:
        original_lines = current_lines
    else:
        # For subsequent calls, we'll keep the original buffer intact
        # and just update the current buffer
        pass

    # Perform the replacement
    new_content = substitute_within_context(
        before_context, after_context, current_content, content
    )

    if new_content == current_content:
        return "No changes made - replacement text identical to original"

    # Update the buffer
    new_lines = new_content.split("\n")
    nvim.set_buffer_lines(buffer_id, 0, -1, new_lines)

    # Handle diff view based on whether diff is already active
    if not nvim.diff_active:
        # First call - create a diff view
        nvim.execute_command("vnew")  # Create a new vertical split
        temp_buffer_id = nvim.get_current_buffer()
        nvim.set_buffer_lines(temp_buffer_id, 0, -1, original_lines)
        nvim.execute_command("diffthis")  # Mark this buffer for diff

        # Switch back to original buffer and mark for diff
        nvim.execute_command("wincmd p")  # Go back to previous window
        nvim.execute_command("diffthis")  # Mark original buffer for diff

        # Update the diff flag and store buffer ID
        nvim.diff_active = True
        nvim.diff_buffer_id = temp_buffer_id

        return f"Updated content between '{before_context}' and '{after_context}' with diff view enabled"
    else:
        # Subsequent calls - diff is already active, just update the current buffer
        nvim.execute_command("diffupdate")  # Update the diff highlighting
        return f"Updated content between '{before_context}' and '{after_context}', diff view updated"


def substitute_within_context(
    before_context: str, after_context: str, current_content: str, new_content: str
) -> str:
    """Replace content between two context markers in a string.

    This function performs a targeted replacement within a larger text by using
    surrounding context as anchors. It preserves the context markers themselves
    and only replaces the content between them.

    The function works by:
    1. Creating a regex pattern using the escaped before and after contexts
    2. Finding all text between these contexts using a non-greedy match
    3. Replacing only that content while preserving the context markers

    Args:
        before_context: Text that appears immediately before the content to replace
        after_context: Text that appears immediately after the content to replace
        current_content: The full text to search within
        new_content: The new content to insert between the context markers

    Returns:
        The modified text with content between contexts replaced, or an error message
        if the context markers couldn't be found
    """
    # Handle edge case where both contexts are empty
    if before_context == "" and after_context == "":
        return new_content

    # Find the target location using context
    before_escaped = re.escape(before_context)
    after_escaped = re.escape(after_context)
    target_pattern = f"{before_escaped}(.*?){after_escaped}"

    if not re.search(target_pattern, current_content, re.DOTALL):
        logger.error(f"Pattern not found: {target_pattern}")
        return f"Error: Could not find text between '{before_context}' and '{after_context}'"

    new_content = re.sub(
        target_pattern,
        f"{before_context}{new_content}{after_context}",
        current_content,
        flags=re.DOTALL,
    )

    return new_content
