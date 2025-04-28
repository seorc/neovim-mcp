from unittest.mock import MagicMock
from pathlib import Path

from neovim_mcp import server  # Import the module with our tools


class TestReadBufferOperations:
    """Test suite for buffer reading operations"""

    def test_read_buffer(self, running_neovim, test_file):
        """Test that read_buffer correctly reads the content of the current buffer"""
        # Setup: Create a mock Context with our NvimConnection
        socket_path = Path(running_neovim)
        socket_dir = socket_path.parent
        socket_name = socket_path.name

        # Create and configure connection
        connection = server.NvimConnection(socket_name=socket_name)
        connection.set_socket_path(str(socket_dir))
        connection.connect()

        # Create mock context with our connection
        mock_context = MagicMock()
        mock_context.request_context.lifespan_context.nvim = connection

        try:
            # Open our test file in Neovim
            connection.execute_command(f"edit {test_file}")

            # Call the read_buffer tool with our mock context
            buffer_content = server.read_buffer(mock_context)

            # Verify the buffer content matches our test file
            expected_content = (
                "Line 1: Test content\nLine 2: More test content\nLine 3: Final line"
            )
            assert buffer_content == expected_content

        finally:
            # Cleanup
            connection.close()

    def test_read_selected_text(self, running_neovim, test_file):
        """Test that read_selected_text correctly reads the selected text"""
        # Setup: Create a mock Context with our NvimConnection
        socket_path = Path(running_neovim)
        socket_dir = socket_path.parent
        socket_name = socket_path.name

        # Create and configure connection
        connection = server.NvimConnection(socket_name=socket_name)
        connection.set_socket_path(str(socket_dir))
        connection.connect()

        # Create mock context with our connection
        mock_context = MagicMock()
        mock_context.request_context.lifespan_context.nvim = connection

        try:
            # Open our test file in Neovim
            connection.execute_command(f"edit {test_file}")

            # Mock the visual selection - this is tricky because we can't actually
            # set a visual selection programmatically without user interaction
            # Instead, we'll patch the get_visual_selection method
            original_method = connection.get_visual_selection
            connection.get_visual_selection = lambda: (0, 1)  # Select first line only

            try:
                # Call the read_selected_text tool with our mock context
                selected_text = server.read_selected_text(mock_context)

                # Verify the selected text
                expected_text = "Line 1: Test content"
                assert selected_text == expected_text
            finally:
                # Restore original method
                connection.get_visual_selection = original_method

        finally:
            # Cleanup
            connection.close()
