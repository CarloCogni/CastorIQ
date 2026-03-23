# environments/tests/test_templatetags.py
"""Tests for environments templatetags — dict_filters and chat_filters."""


# ── dict_filters.get_item ────────────────────────────────────────────────────


class TestGetItem:
    """Tests for the get_item template filter."""

    def test_get_existing_key_returns_value(self):
        """Returns value for an existing key."""
        from environments.templatetags.dict_filters import get_item

        d = {"name": "Alice", "age": 30}
        assert get_item(d, "name") == "Alice"
        assert get_item(d, "age") == 30

    def test_get_missing_key_returns_none(self):
        """Returns None for a key not in the dictionary."""
        from environments.templatetags.dict_filters import get_item

        d = {"name": "Alice"}
        assert get_item(d, "missing") is None

    def test_get_item_none_dict_returns_none(self):
        """Returns None when dictionary is None."""
        from environments.templatetags.dict_filters import get_item

        assert get_item(None, "key") is None

    def test_get_item_none_key_returns_none(self):
        """Returns None when key is None."""
        from environments.templatetags.dict_filters import get_item

        d = {"name": "Alice"}
        assert get_item(d, None) is None

    def test_get_item_empty_dict_returns_none(self):
        """Returns None for any key on an empty dict."""
        from environments.templatetags.dict_filters import get_item

        assert get_item({}, "key") is None

    def test_get_item_false_value_returned_correctly(self):
        """Returns False value (not None) when key exists with falsy value."""
        from environments.templatetags.dict_filters import get_item

        d = {"is_external": False, "count": 0}
        # Returns the actual falsy values, not None
        assert get_item(d, "is_external") is False
        assert get_item(d, "count") == 0

    def test_get_item_nested_dict(self):
        """Returns nested dict value correctly."""
        from environments.templatetags.dict_filters import get_item

        d = {"nested": {"inner": "value"}}
        result = get_item(d, "nested")
        assert result == {"inner": "value"}


# ── chat_filters.render_markdown ────────────────────────────────────────────


class TestRenderMarkdown:
    """Tests for the render_markdown template filter."""

    def test_empty_string_returns_empty(self):
        """Empty input returns empty string."""
        from environments.templatetags.chat_filters import render_markdown

        assert render_markdown("") == ""

    def test_none_returns_empty(self):
        """None input returns empty string."""
        from environments.templatetags.chat_filters import render_markdown

        assert render_markdown(None) == ""

    def test_plain_text_wrapped_in_paragraph(self):
        """Plain text is wrapped in <p> tags."""
        from environments.templatetags.chat_filters import render_markdown

        result = render_markdown("Hello world")
        assert "<p>" in result
        assert "Hello world" in result

    def test_bold_markdown_converted_to_strong(self):
        """**bold** is converted to <strong>."""
        from environments.templatetags.chat_filters import render_markdown

        result = render_markdown("**bold text**")
        assert "<strong>" in result
        assert "bold text" in result

    def test_heading_converted_to_h_tag(self):
        """# Heading is converted to <h1>."""
        from environments.templatetags.chat_filters import render_markdown

        result = render_markdown("# My Title")
        assert "<h1>" in result
        assert "My Title" in result

    def test_code_block_converted(self):
        """Fenced code block is converted to <code> or <pre>."""
        from environments.templatetags.chat_filters import render_markdown

        result = render_markdown("```python\nprint('hello')\n```")
        assert "<code>" in result or "<pre>" in result

    def test_returns_safe_string(self):
        """Return value is a SafeString (marked as safe HTML)."""
        from django.utils.safestring import SafeData

        from environments.templatetags.chat_filters import render_markdown

        result = render_markdown("Hello")
        assert isinstance(result, SafeData)

    def test_table_markdown_converted(self):
        """Markdown table is converted to HTML table."""
        from environments.templatetags.chat_filters import render_markdown

        table_md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = render_markdown(table_md)
        assert "<table" in result

    def test_bullet_list_converted(self):
        """Markdown bullet list is converted to <ul>."""
        from environments.templatetags.chat_filters import render_markdown

        result = render_markdown("- item 1\n- item 2")
        assert "<ul>" in result or "<li>" in result
