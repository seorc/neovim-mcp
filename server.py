import logging
import msgpack
import socket
import os
import random

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from mcp.server.fastmcp import FastMCP, Context


logger = logging.getLogger(__name__)
logger.setLevel(level=logging.DEBUG)


class NvimConnection:
    """A minimal, synchronous connection to Neovim avoiding event loops"""

    def __init__(self, socket_path=None):
        self.socket_path = socket_path or self._find_socket()
        self.sock = None
        self.request_id = random.randint(1, 10000)

    def _find_socket(self):
        """Find a valid Neovim socket path"""
        # Try environment variable first
        socket_path = os.environ.get("NVIM_LISTEN_ADDRESS")
        if socket_path and os.path.exists(socket_path):
            return socket_path

        # Common socket locations
        paths = ["/tmp/nvim.sock", "/tmp/nvimsocket", "/tmp/nvim/nvim.sock"]

        for path in paths:
            if os.path.exists(path):
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
            print(f"Debug - start_pos: {cursor_pos}")
            print(f"Debug - end_pos: {visual_end_pos}")
            raise RuntimeError(f"Error getting visual selection: {str(e)}")


@dataclass
class AppContext:
    nvim: NvimConnection


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle with type-safe context"""
    # Initialize on startup
    nvim = NvimConnection(socket_path="/tmp/neovim")
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
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


@server.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello {name}!"


@server.tool()
def insert_response(ctx: Context, message: list[str]):
    "Write a message to neovim"
    logger.debug(f"Writing message to neovim: {message}")
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buff_id = nvim.get_current_buffer()
    logger.debug(f"Current buffet is {buff_id}")
    nvim.set_buffer_lines(buff_id[0], 0, 0, message)


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


@server.tool()
def insert_content_at_position(
    ctx: Context, line: int, column: int, content: str
) -> str:
    """Insert content at a specific position in the current buffer.

    Args:
        line: Line number (0-indexed) where content should be inserted
        column: Column number (0-indexed) where content should be inserted
        content: Text content to insert

    Returns:
        A message indicating success and showing what was inserted
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buffer_id = nvim.get_current_buffer()

    # Get the current line
    current_line = nvim.get_buffer_lines(buffer_id, line, line + 1)[0]

    # Insert the content at the specified column
    new_line = current_line[:column] + content + current_line[column:]

    # Update the buffer
    nvim.set_buffer_lines(buffer_id, line, line + 1, [new_line])

    # Create a diff highlighting using Neovim's built-in diffing capability
    # First, save the original state in a temp buffer
    nvim.execute_command("vnew")  # Create a new vertical split
    temp_buffer_id = nvim.get_current_buffer()
    nvim.set_buffer_lines(temp_buffer_id, 0, 0, [current_line])
    nvim.execute_command("diffthis")  # Mark this buffer for diff

    # Switch back to original buffer and mark for diff
    nvim.execute_command("wincmd p")  # Go back to previous window
    nvim.execute_command("diffthis")  # Mark original buffer for diff

    # Set cursor to the end of inserted content
    nvim.set_cursor(line + 1, column + len(content))

    return f"Inserted '{content}' at line {line + 1}, column {column + 1}. Diff view enabled."


@server.tool()
def insert_lines_at_position(ctx: Context, line: int, content: list[str]) -> str:
    """Insert multiple lines at a specific line position in the current buffer.

    Args:
        line: Line number (0-indexed) where lines should be inserted
        content: List of text lines to insert

    Returns:
        A message indicating success and showing what was inserted
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buffer_id = nvim.get_current_buffer()

    # Get original content for diffing
    original_lines = nvim.get_buffer_lines(buffer_id, 0, -1)

    # Insert the lines at the specified position
    nvim.set_buffer_lines(buffer_id, line, line, content)

    # Create a diff highlighting using Neovim's built-in diffing capability
    # First, save the original state in a temp buffer
    nvim.execute_command("vnew")  # Create a new vertical split
    temp_buffer_id = nvim.get_current_buffer()
    nvim.set_buffer_lines(temp_buffer_id, 0, -1, original_lines)
    nvim.execute_command("diffthis")  # Mark this buffer for diff

    # Switch back to original buffer and mark for diff
    nvim.execute_command("wincmd p")  # Go back to previous window
    nvim.execute_command("diffthis")  # Mark original buffer for diff

    # Set cursor to the beginning of inserted content
    nvim.set_cursor(line + 1, 0)

    return f"Inserted {len(content)} lines at line {line + 1}. Diff view enabled."


@server.tool()
def replace_text_range(
    ctx: Context,
    start_line: int,
    start_col: int,
    end_line: int,
    end_col: int,
    content: str,
) -> str:
    """Replace text in a specific range with new content.

    Args:
        start_line: Starting line (0-indexed)
        start_col: Starting column (0-indexed)
        end_line: Ending line (0-indexed)
        end_col: Ending column (0-indexed)
        content: New content to insert

    Returns:
        A message indicating success and showing diff
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buffer_id = nvim.get_current_buffer()

    # Get original content for diffing
    original_lines = nvim.get_buffer_lines(buffer_id, 0, -1)

    # Get the affected lines
    affected_lines = nvim.get_buffer_lines(buffer_id, start_line, end_line + 1)

    # Handle single line case
    if start_line == end_line:
        new_line = affected_lines[0][:start_col] + content + affected_lines[0][end_col:]
        nvim.set_buffer_lines(buffer_id, start_line, start_line + 1, [new_line])
    else:
        # Handle multi-line case
        first_line = affected_lines[0][:start_col] + content.split("\n")[0]
        last_line = affected_lines[-1][end_col:]

        content_lines = content.split("\n")
        if len(content_lines) > 1:
            middle_lines = content_lines[1:-1]
            new_lines = [first_line] + middle_lines + [content_lines[-1] + last_line]
        else:
            new_lines = [first_line + last_line]

        nvim.set_buffer_lines(buffer_id, start_line, end_line + 1, new_lines)

    # Create a diff highlighting using Neovim's built-in diffing capability
    nvim.execute_command("vnew")  # Create a new vertical split
    temp_buffer_id = nvim.get_current_buffer()
    nvim.set_buffer_lines(temp_buffer_id, 0, -1, original_lines)
    nvim.execute_command("diffthis")  # Mark this buffer for diff

    # Switch back to original buffer and mark for diff
    nvim.execute_command("wincmd p")  # Go back to previous window
    nvim.execute_command("diffthis")  # Mark original buffer for diff

    return f"Replaced text from line {start_line + 1}, col {start_col + 1} to line {end_line + 1}, col {end_col + 1}. Diff view enabled."


@server.tool()
def toggle_diff_mode(ctx: Context, enable: bool = True) -> str:
    """Toggle diff mode between current buffer and its previous state.

    Args:
        enable: True to enable diff mode, False to disable

    Returns:
        A message indicating the action taken
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    if enable:
        # Create a snapshot of current buffer
        buffer_id = nvim.get_current_buffer()
        current_lines = nvim.get_buffer_lines(buffer_id, 0, -1)

        # Create a new buffer for comparison
        nvim.execute_command("vnew")
        temp_buffer_id = nvim.get_current_buffer()
        nvim.set_buffer_lines(temp_buffer_id, 0, -1, current_lines)

        # Enable diff mode
        nvim.execute_command("diffthis")
        nvim.execute_command("wincmd p")  # Go back to previous window
        nvim.execute_command("diffthis")

        return "Diff mode enabled. Left buffer shows current state."
    else:
        # Disable diff mode
        nvim.execute_command("diffoff!")
        return "Diff mode disabled."


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
    import os
    import pathlib

    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim

    try:
        # Get the directory of the current buffer
        buffer_id = nvim.get_current_buffer()
        buffer_name = nvim.eval_expr(f"bufname({buffer_id})")

        if not buffer_name:
            logger.warning("Current buffer has no name or path")
            # Default to current working directory
            cwd = os.getcwd()
        else:
            # Get the directory containing the buffer file
            buffer_path = pathlib.Path(buffer_name)
            # If it's an absolute path, use its directory
            if buffer_path.is_absolute():
                cwd = str(buffer_path.parent)
            else:
                # For relative paths, join with current directory
                abs_path = pathlib.Path(os.getcwd()) / buffer_path
                cwd = str(abs_path.parent)

        logger.debug(f"Running git command in directory: {cwd}")

        # Execute the git ls-tree command in the buffer's directory
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
