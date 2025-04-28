import unittest
import logging

# Import the function we want to test
from neovim_mcp.server import substitute_within_context

# Set up logging for tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestSubstituteWithinContext(unittest.TestCase):
    """Test cases for the substitute_within_context function."""

    def test_basic_replacement(self):
        """Test a simple replacement case."""
        before_context = "Hello, "
        after_context = "!"
        current_content = "Hello, world!"
        new_content = "Python"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(result, "Hello, Python!")

    def test_multiple_occurrences(self):
        """Test replacement when the pattern appears multiple times."""
        before_context = "<start>"
        after_context = "</end>"
        current_content = "<start>first</end> and <start>second</end>"
        new_content = "REPLACED"

        # By default, re.sub replaces all occurrences
        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(result, "<start>REPLACED</end> and <start>REPLACED</end>")

    def test_multiline_content(self):
        """Test replacement with multiline content."""
        before_context = "def example():\n    "
        after_context = "\n    return"
        current_content = "def example():\n    print('hello')\n    return 42"
        new_content = "print('Hello, world!')"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(
            result, "def example():\n    print('Hello, world!')\n    return 42"
        )

    def test_with_regex_special_chars(self):
        """Test replacement when contexts contain regex special characters."""
        before_context = "price: $"
        after_context = ".00"
        current_content = "The item price: $99.00 is now on sale"
        new_content = "149"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(result, "The item price: $149.00 is now on sale")

    def test_empty_target_content(self):
        """Test replacement where the content between contexts is empty."""
        before_context = "<empty>"
        after_context = "</empty>"
        current_content = "This is an <empty></empty> test"
        new_content = "NOT EMPTY"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(result, "This is an <empty>NOT EMPTY</empty> test")

    def test_pattern_not_found(self):
        """Test behavior when the pattern is not found in the content."""
        before_context = "NOT IN CONTENT"
        after_context = "ALSO NOT THERE"
        current_content = "This pattern doesn't match anything."
        new_content = "Won't be inserted"

        # Current behavior returns an error message - this test verifies that behavior
        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        # This is testing the current error message returned,
        # but ideally the function should raise an exception instead
        self.assertTrue(isinstance(result, str))
        self.assertTrue("Error: Could not find text" in result)

    def test_nested_contexts(self):
        """Test replacement with nested contexts."""
        before_context = "<outer><inner>"
        after_context = "</inner></outer>"
        current_content = "<outer><inner>original</inner></outer>"
        new_content = "replaced"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(result, "<outer><inner>replaced</inner></outer>")

    def test_adjacent_matches(self):
        """Test replacement with immediately adjacent matches."""
        before_context = "<tag>"
        after_context = "</tag>"
        current_content = "<tag>first</tag><tag>second</tag>"
        new_content = "REPLACED"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(result, "<tag>REPLACED</tag><tag>REPLACED</tag>")

    def test_overlapping_possibilities(self):
        """Test where the after_context could match in multiple places."""
        before_context = "start:"
        after_context = "end"
        current_content = "start:middle-end-middle-end"
        new_content = "NEW"

        # The function should match greedily by default with (.*?)
        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        # Since the regex uses non-greedy matching, it should match the first 'end'
        self.assertEqual(result, "start:NEWend-middle-end")

    def test_very_large_content(self):
        """Test with a larger piece of content to check performance."""
        before_context = "<!-- START -->"
        after_context = "<!-- END -->"
        current_content = (
            "Header\n\n<!-- START -->\n" + "x" * 10000 + "\n<!-- END -->\n\nFooter"
        )
        new_content = "REPLACED"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        expected = "Header\n\n<!-- START -->REPLACED<!-- END -->\n\nFooter"
        self.assertEqual(result, expected)


class TestSubstituteWithinContextEdgeCases(unittest.TestCase):
    """Test edge cases for the substitute_within_context function."""

    def test_empty_contexts(self):
        """Test with empty before and after contexts - this is an edge case!"""
        # Note: This might not be a valid use case, but testing behavior
        before_context = ""
        after_context = ""
        current_content = "Some content here"
        new_content = "REPLACEMENT"

        # This would match and replace the entire content with current implementation
        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        # If empty contexts are allowed, expect full replacement
        self.assertEqual(result, "REPLACEMENT")

    def test_identical_before_after(self):
        """Test when before and after contexts are identical."""
        before_context = "---"
        after_context = "---"
        current_content = "start---middle---end"
        new_content = "NEW"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        # Since the regex is non-greedy, it should match the first occurrence
        self.assertEqual(result, "start---NEW---end")

    def test_regex_escape_behavior(self):
        """Test that regex special characters are properly escaped."""
        before_context = "a+b*c"  # These are regex special chars
        after_context = "(d|e)^f"  # More regex special chars
        current_content = "a+b*c_TEST_(d|e)^f"
        new_content = "REPLACED"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        self.assertEqual(result, "a+b*cREPLACED(d|e)^f")

    def test_code_block_replacement(self):
        """Test replacing code blocks similar to how it would be used in the tool."""
        before_context = "```python\n"
        after_context = "\n```"
        current_content = "Here is some code:\n```python\ndef hello():\n    print('Hello')\n```\nEnd of code."
        new_content = "def hello_world():\n    print('Hello, world!')"

        result = substitute_within_context(
            before_context, after_context, current_content, new_content
        )

        expected = "Here is some code:\n```python\ndef hello_world():\n    print('Hello, world!')\n```\nEnd of code."
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
