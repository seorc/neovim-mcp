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
        while True:
            chunk = self.sock.recv(4096)
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
                continue
            finally:
                logger.debug("Response from neovim: %s", full_response)

    def execute_command(self, cmd):
        """Execute a Vim command"""
        return self._send_request("nvim_command", [cmd])

    def eval_expr(self, expr):
        """Evaluate a Vim expression"""
        return self._send_request("nvim_eval", [expr])

    def get_current_buffer(self):
        """Get the current buffer number"""
        return self._send_request("nvim_get_current_buf", [])

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
    buffer_info = nvim.get_current_buffer()
    buffer_id = int.from_bytes(buffer_info.data)
    logger.debug(f"Buffer {buffer_id} info {buffer_info}")
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
    logger.debug(f"Reading selection from buffer id {buffer_id[0]} {start}:{end}")
    result = nvim.get_buffer_lines(buffer_id[0], start, end)
    result = "\n".join(result)
    logger.debug("This is the result from read_selected_text: %s", result)
    return result
