"""Tests for memory_write extraction."""

from nekobot.memory.extractor import extract_memory_writes


class TestExtractor:
    def test_basic_extraction(self):
        response = """Here's my response.

<memory_write>
- profile.name: User
- project.nekobot: architecture done
</memory_write>

Done."""
        cleaned, facts = extract_memory_writes(response)
        assert "memory_write" not in cleaned
        assert "Here's my response." in cleaned
        assert "Done." in cleaned
        assert len(facts) == 2
        assert facts[0] == ("profile", "name", "User")
        assert facts[1] == ("project", "nekobot", "architecture done")

    def test_no_memory_write(self):
        response = "Just a normal response."
        cleaned, facts = extract_memory_writes(response)
        assert cleaned == response
        assert facts == []

    def test_multiple_blocks(self):
        response = """First part.

<memory_write>
- profile.age: 25
</memory_write>

Middle part.

<memory_write>
- project.foo: done
</memory_write>

End."""
        cleaned, facts = extract_memory_writes(response)
        assert len(facts) == 2
        assert "First part." in cleaned
        assert "Middle part." in cleaned
        assert "End." in cleaned

    def test_no_category_prefix(self):
        response = """<memory_write>
- some_key: some_value
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert facts[0] == ("active", "some_key", "some_value")

    def test_colon_in_value(self):
        response = """<memory_write>
- profile.url: https://example.com
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert facts[0] == ("profile", "url", "https://example.com")

    def test_empty_block(self):
        response = """<memory_write>
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert facts == []

    def test_malformed_line_skipped(self):
        response = """<memory_write>
- profile.name: valid
- no_colon_here
- project.foo: also valid
</memory_write>"""
        _, facts = extract_memory_writes(response)
        assert len(facts) == 2
