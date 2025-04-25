import os
import pytest
import socket
import subprocess
import tempfile
import time
import signal
from pathlib import Path

from server import NvimConnection, FastMCP


@pytest.fixture(scope="session")
def test_directory():
    """Fixture that creates a temporary test directory.

    Yields:
        Path: Path to the temporary test directory
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_dir_path = Path(tmp_dir)

        # Create some test files in the directory
        (temp_dir_path / "file1.txt").write_text("Content of file 1")
        (temp_dir_path / "file2.txt").write_text("Content of file 2")

        # Create a subdirectory with a file
        subdir = temp_dir_path / "subdir"
        subdir.mkdir()
        (subdir / "file3.txt").write_text("Content of file 3")

        yield temp_dir_path

        # Cleanup happens automatically when the context manager exits


@pytest.fixture(scope="session")
def running_neovim(test_directory):
    """Fixture that starts a Neovim instance with a socket for testing.

    Yields:
        str: Path to the Neovim socket
    """
    # Create a temp directory for socket
    socket_path = Path(test_directory) / "neovim.socket"

    # Start Neovim with the socket option
    nvim_process = subprocess.Popen(
        ["nvim", "--headless", "--listen", str(socket_path), "-n"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,  # Use process group for clean termination
    )

    # Give Neovim a moment to start and create the socket
    timeout = 5  # seconds
    start_time = time.time()
    while not socket_path.exists() and time.time() - start_time < timeout:
        time.sleep(0.1)

    # Make sure Neovim started correctly
    if not socket_path.exists():
        nvim_process.terminate()
        nvim_process.wait()
        pytest.fail("Neovim failed to start and create socket")

    # Return the socket path for tests to use
    yield str(socket_path)

    # Clean termination using process group
    try:
        os.killpg(os.getpgid(nvim_process.pid), signal.SIGTERM)
        nvim_process.wait(timeout=2)
    except:
        os.killpg(os.getpgid(nvim_process.pid), signal.SIGKILL)


@pytest.fixture
def nvim_connection(running_neovim):
    """Fixture that provides a configured NvimConnection.

    Args:
        running_neovim: The socket path from the running_neovim fixture

    Yields:
        NvimConnection: A connected Neovim connection
    """
    # Extract socket path and directory from the full path
    socket_path = Path(running_neovim)
    socket_dir = socket_path.parent
    socket_name = socket_path.name

    # Create and configure connection
    connection = NvimConnection(socket_name=socket_name)
    connection.set_socket_path(str(socket_dir))
    connection.connect()

    yield connection

    # Cleanup
    connection.close()


@pytest.fixture
def test_file(test_directory):
    """Fixture that creates a temporary test file with known content.

    Yields:
        Path: Path to the temporary test file
    """
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as f:
        f.write("Line 1: Test content\nLine 2: More test content\nLine 3: Final line")
        temp_file_path = Path(f.name)

    yield temp_file_path

    # Cleanup
    if temp_file_path.exists():
        temp_file_path.unlink()


@pytest.fixture
def server_client():
    """Fixture that provides a configured FastMCP client for testing.

    Yields:
        TestClient: A FastMCP test client
    """
    # This function is a placeholder - actual implementation would depend
    # on how your FastMCP server can be accessed in test mode
    from fastapi.testclient import TestClient

    # Create a test instance of the server
    # This is assuming FastMCP has similar test client capabilities to FastAPI
    # You may need to adapt this based on your actual server implementation
    test_client = TestClient(server.app)

    yield test_client
