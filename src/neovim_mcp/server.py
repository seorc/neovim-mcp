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
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, ErrorData


logger = logging.getLogger(__name__)
logger.setLevel(level=logging.DEBUG)


class NvimConnection:
    """
    A minimal, synchronous connection to Neovim via socket.

    This class provides a lightweight, synchronous interface to interact with Neovim
    without relying on event loops. It handles socket communication using msgpack-rpc
    protocol for sending commands and receiving responses.

    Attributes:
        sock: Socket connection to Neovim
        project_dir: Base directory path of the project
        socket_path: Full path to the Neovim socket
        socket_name: Name of the Neovim socket file
        request_id: Unique ID for msgpack-rpc requests
        diff_active: Flag indicating if diff mode is active
        diff_buffer_id: Reference to the buffer used in diff mode
    """

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
        """
        Set the socket path for the Neovim connection.

        This method configures the connection to use a specific socket path. It also
        stores the project directory for later use in file operations.

        Args:
            path: Path to the directory containing the Neovim socket

        Returns:
            The provided path if successful

        Raises:
            FileNotFoundError: If the socket file doesn't exist at the specified path
        """
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
        self.sock.settimeout(1)  # Set a timeout of 1 seconds
        return self

    def close(self):
        """Close the connection"""
        if not self.socket_path:
            raise Exception("You must initialize the path")

        if self.sock:
            self.sock.close()
            self.sock = None

    def _send_request(self, method, params):
        """
        Send a msgpack-rpc request to Neovim and get the response.

        This method handles the low-level communication with Neovim using the msgpack-rpc
        protocol. It sends a request with a unique ID and waits for a response.

        Args:
            method: The Neovim API method to call
            params: List of parameters for the method

        Returns:
            The response data from Neovim

        Raises:
            RuntimeError: If the socket is not connected or request is invalid
            socket.timeout: If receiving a response times out
            msgpack.exceptions.OutOfData: If the received data is incomplete
        """
        if not self.sock:
            self.connect()

        req_id = self.request_id
        self.request_id += 1

        # msgpack-rpc message format: [type, msgid, method, params]
        request = msgpack.packb([0, req_id, method, params])

        logger.debug("Sending message: %s", [0, req_id, method, params])

        if not self.sock:
            raise RuntimeError("The socket is not set")

        if not request:
            raise RuntimeError("The request is not valid")

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
        Get the line range of the current visual selection in Neovim.

        This method retrieves the starting and ending lines of the current visual
        selection in Neovim. It handles the conversion between Neovim's 1-indexed line
        numbers and the 0-indexed format used by the API. The method also ensures that
        the start line is always less than or equal to the end line, regardless of
        the selection direction.

        Returns:
            A tuple of (start_line, end_line) in 0-indexed format, sorted by position

        Raises:
            RuntimeError: If there's an error getting the visual selection
        """
        try:
            # Get position of the beginning of visual selection (mark '<)
            cursor_pos = self._send_request("nvim_call_function", ["getpos", ["."]])

            # Get position of the end of visual selection (mark '>)
            visual_end_pos = self._send_request("nvim_call_function", ["getpos", ["v"]])

            # Extract line numbers (0-indexed for API consistency)
            # In Neovim, getpos() returns [bufnum, lnum, col, off] where lnum is the line number
            start_line = cursor_pos[1] - 1  # Convert from 1-indexed to 0-indexed
            end_line = visual_end_pos[1] - 1  # End is exclusive for API purposes

            # Always return lines in ascending order
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
    """
    Manage the lifecycle of the application with proper context management.

    This async context manager initializes necessary resources when the application
    starts up and ensures proper cleanup when shutting down. It creates and manages
    the NvimConnection instance that will be available throughout the application.

    Args:
        server: The FastMCP server instance

    Yields:
        AppContext: An object containing the application's context and resources

    Raises:
        Exception: Any exceptions during startup are logged
    """
    # Initialize on startup
    nvim = NvimConnection()
    logger.debug("Creating lifespan objects")
    try:
        yield AppContext(nvim=nvim)
    except Exception:
        logger.exception("Failed")
    finally:
        # Cleanup on shutdown
        nvim.close()


server = FastMCP("neovim", lifespan=app_lifespan)


@server.tool()
def initialize_neovim_connection(ctx: Context, path: str) -> str:
    """Initialize the project path.

    You must use this tool if you see an error indicating that you must initialize the path or that the socket is not connected.

    The path must be provided to you by the user as PROJECT_PATH. If not, just skip using any other neovim tools until it si provided to you.
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    nvim.set_socket_path(path)
    nvim.connect()
    return f"Neovim configured correctly at {path}"


@server.tool()
def read_buffer(ctx: Context):
    """
    Read the entire content of the current buffer.

    This function retrieves all lines from the current buffer in the Neovim instance
    and joins them into a single string with newlines.

    Args:
        ctx: The MCP context containing the request context

    Returns:
        The content of the current buffer as a string
    """
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
def edit_content(
    ctx: Context,
    previous_content: str,
    new_content: str,
    work_on_empty_content: bool = False,
) -> dict:
    """Update text by matching and replacing a block of content.

    This tool provides precision editing by matching a specific block of text
    and replacing it with new content. It's particularly useful for targeted updates
    in code files, configuration files, or text documents.

    IMPORTANT:
    1. The previous_content must exist in the buffer and be unique enough to match
       exactly one occurrence.
    2. The new_content should contain the updated version with the same surrounding
       context to ensure proper placement.
    3. If multiple occurrences of previous_content are found, the function will raise
       an error requesting more specific context.
    4. It is OK to pass an empty previous_content if the buffer is empty.

    Make sure to include enough surrounding context in both the previous_content
    and new_content to ensure a unique match. For code files, pay special attention
    to indentation and line breaks.

    Args:
        previous_content: A unique block of text to be replaced
        new_content: The new content to replace the previous content with
        work_on_empty_content: Make the edition even if the content is empty

    Returns:
        A dictionary with:
        - message: A description of the update operation
        - updated_content: The complete content of the buffer after the update

    Examples:
        To replace a function:
        - previous_content = "def my_function():\n    return False\n"
        - new_content = "def my_function():\n    return True\n"
    """
    nvim: NvimConnection = ctx.request_context.lifespan_context.nvim
    buffer_id = nvim.get_current_buffer()

    # Get current content
    current_lines = nvim.get_buffer_lines(buffer_id, 0, -1)
    current_content = "\n".join(current_lines)

    # Perform the replacement
    try:
        new_buffer_content = replace_with_context(
            current_content,
            previous_content,
            new_content,
            work_on_empty_content=work_on_empty_content,
        )
    except McpError as e:
        return {"message": e.error.message, "updated_content": current_content}

    if new_buffer_content == current_content:
        return {
            "message": "No changes made - replacement text identical to original",
            "updated_content": current_content,
        }

    # Update the buffer
    new_lines = new_buffer_content.split("\n")
    nvim.set_buffer_lines(buffer_id, 0, -1, new_lines)

    return {
        "message": "Content updated successfully",
        "updated_content": new_buffer_content,
    }


def replace_with_context(
    current_buffer_content: str,
    previous_content: str,
    new_content: str,
    work_on_empty_content: bool = False,
) -> str:
    """Replace a specific block of text in the buffer content.

    This function matches a specific block of text in the current buffer content
    and replaces it with new content. The previous_content must be unique
    enough to match exactly one occurrence.

    Args:
        current_buffer_content: The full text content of the current buffer
        previous_content: A unique block of text to be replaced
        new_content: The new content to replace the previous content with
        work_on_empty_content: Make the edition even if the content is empty

    Returns:
        The modified text with the specified content replaced

    Raises:
        McpError: If the previous_content can't be found, or if multiple matches are found
    """
    # Handle edge cases first
    if previous_content == "" and not work_on_empty_content:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="Previous content cannot be empty as it would create ambiguous matches.",
            )
        )

    # Special case: If current buffer is empty, we can't match anything
    if current_buffer_content == "" and not work_on_empty_content:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="Cannot find content to replace in an empty buffer.",
            )
        )

    # Escape the previous content for use in regex
    previous_content_escaped = re.escape(previous_content)

    # Find all occurrences of the previous content
    matches = list(
        re.finditer(previous_content_escaped, current_buffer_content, re.DOTALL)
    )

    # Check if there are no matches
    if not matches:
        logger.error("Previous content not found")
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="Could not find the specified content in the buffer. Please provide more accurate previous content.",
            )
        )

    # Check if there are multiple matches
    if len(matches) > 1:
        logger.error(f"Multiple matches found: {len(matches)}")
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=f"Found {len(matches)} occurrences of the specified content. Please provide more specific context to ensure a unique match.",
            )
        )

    # Get the match details
    match = matches[0]
    start = match.start()
    end = match.end()

    # Construct the new buffer content
    result = current_buffer_content[:start] + new_content + current_buffer_content[end:]

    return result
