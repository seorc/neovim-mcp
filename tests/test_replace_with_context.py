import unittest
import logging

# Import the module with our tools
from neovim_mcp import server
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, ErrorData

# Set up logging for tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestReplaceWithContext(unittest.TestCase):
    """Test cases for the replace_with_context function."""

    def test_basic_replacement(self):
        """Test a simple replacement case."""
        current_content = "Hello, world!"
        previous_content = "Hello, world!"
        new_content = "Hello, Python!"

        result = server.replace_with_context(current_content, previous_content, new_content)

        self.assertEqual(result, "Hello, Python!")

    def test_partial_replacement(self):
        """Test replacing part of the content."""
        current_content = "Hello, world! Welcome to testing."
        previous_content = "world!"
        new_content = "Python!"

        result = server.replace_with_context(current_content, previous_content, new_content)

        self.assertEqual(result, "Hello, Python! Welcome to testing.")

    def test_multiline_content(self):
        """Test replacement with multiline content."""
        current_content = "def example():\n    print('hello')\n    return 42"
        previous_content = "print('hello')"
        new_content = "print('Hello, world!')"

        result = server.replace_with_context(current_content, previous_content, new_content)

        self.assertEqual(
            result, "def example():\n    print('Hello, world!')\n    return 42"
        )

    def test_with_regex_special_chars(self):
        """Test replacement when contents contain regex special characters."""
        current_content = "The item price: $99.00 is now on sale"
        previous_content = "price: $99.00"
        new_content = "price: $149.00"

        result = server.replace_with_context(current_content, previous_content, new_content)

        self.assertEqual(result, "The item price: $149.00 is now on sale")

    def test_pattern_not_found(self):
        """Test behavior when the pattern is not found in the content."""
        current_content = "This pattern doesn't match anything."
        previous_content = "NOT IN CONTENT"
        new_content = "Won't be inserted"

        # Should raise McpError
        with self.assertRaises(McpError):
            server.replace_with_context(current_content, previous_content, new_content)

    def test_multiple_occurrences(self):
        """Test behavior when multiple matches are found."""
        current_content = "repeat repeat repeat"
        previous_content = "repeat"
        new_content = "REPLACED"

        # Should raise McpError for multiple matches
        with self.assertRaises(McpError):
            server.replace_with_context(current_content, previous_content, new_content)

    def test_code_block_replacement(self):
        """Test replacing code blocks similar to how it would be used in the tool."""
        current_content = "Here is some code:\n```python\ndef hello():\n    print('Hello')\n```\nEnd of code."
        previous_content = "```python\ndef hello():\n    print('Hello')\n```"
        new_content = "```python\ndef hello_world():\n    print('Hello, world!')\n```"

        result = server.replace_with_context(current_content, previous_content, new_content)

        expected = "Here is some code:\n```python\ndef hello_world():\n    print('Hello, world!')\n```\nEnd of code."
        self.assertEqual(result, expected)

    def test_function_replacement(self):
        """Test replacing a complete function."""
        current_content = "def test_function():\n    return False\n\ndef another_function():\n    pass"
        previous_content = "def test_function():\n    return False"
        new_content = "def test_function():\n    return True"
        
        result = server.replace_with_context(current_content, previous_content, new_content)
        
        expected = "def test_function():\n    return True\n\ndef another_function():\n    pass"
        self.assertEqual(result, expected)

    def test_whitespace_preservation(self):
        """Test replacement with whitespace and indentation preserved."""
        current_content = "class Test:\n    def method(self):\n        x = 1\n        y = 2\n        return x + y"
        previous_content = "    def method(self):\n        x = 1\n        y = 2"
        new_content = "    def method(self):\n        a = 10\n        b = 20"
        
        result = server.replace_with_context(current_content, previous_content, new_content)
        
        expected = "class Test:\n    def method(self):\n        a = 10\n        b = 20\n        return x + y"
        self.assertEqual(result, expected)
        
    def test_exact_match(self):
        """Test replacement of the entire content."""
        current_content = "This is a test string."
        previous_content = "This is a test string."
        new_content = "This is a completely different string."
        
        result = server.replace_with_context(current_content, previous_content, new_content)
        
        self.assertEqual(result, "This is a completely different string.")

    def test_very_large_content(self):
        """Test with a larger piece of content to check performance."""
        current_content = (
            "Header\n\n<!-- START -->\n" + "x" * 10000 + "\n<!-- END -->\n\nFooter"
        )
        previous_content = "<!-- START -->\n" + "x" * 10000 + "\n<!-- END -->"
        new_content = "<!-- START -->\nREPLACED\n<!-- END -->"

        result = server.replace_with_context(current_content, previous_content, new_content)

        expected = "Header\n\n<!-- START -->\nREPLACED\n<!-- END -->\n\nFooter"
        self.assertEqual(result, expected)
        

if __name__ == "__main__":
    unittest.main()
