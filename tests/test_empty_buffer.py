import unittest
import logging

# Import the module with our tools
from neovim_mcp.server import replace_with_context
from mcp.shared.exceptions import McpError

# Set up logging for tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestEmptyBuffer(unittest.TestCase):
    """Test cases for handling empty buffers in the replace_with_context function."""

    def test_empty_buffer(self):
        """Test behavior when the buffer is completely empty."""
        current_content = ""
        previous_content = "some content"
        new_content = "new content"

        # Should raise McpError since previous content is not found in empty buffer
        with self.assertRaises(McpError):
            replace_with_context(current_content, previous_content, new_content)

    def test_empty_replacement_content(self):
        """Test behavior when trying to replace with empty content."""
        current_content = "Hello, world!"
        previous_content = "Hello, world!"
        new_content = ""

        result = replace_with_context(current_content, previous_content, new_content)
        self.assertEqual(result, "")

    def test_empty_previous_content(self):
        """Test behavior when previous content is empty.

        Note: This is an edge case that might lead to replacing all content if not handled correctly.
        Empty string would match at the beginning of any content, which could lead to unexpected behavior.
        """
        current_content = "Hello, world!"
        previous_content = ""
        new_content = "New text: "

        # This should raise an error because empty content to match is ambiguous
        with self.assertRaises(McpError):
            replace_with_context(current_content, previous_content, new_content)

    def test_both_contents_empty(self):
        """Test behavior when both previous and new content are empty."""
        current_content = "Hello, world!"
        previous_content = ""
        new_content = ""

        # Should raise McpError because empty previous content is ambiguous
        with self.assertRaises(McpError):
            replace_with_context(current_content, previous_content, new_content)

    def test_all_empty(self):
        """Test behavior when buffer, previous content, and new content are all empty."""
        current_content = ""
        previous_content = ""
        new_content = ""

        # Should raise McpError - empty matches in empty content is ambiguous
        with self.assertRaises(McpError):
            replace_with_context(current_content, previous_content, new_content)
