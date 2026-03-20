# core/tests/test_token_utils.py
"""Tests for core.token_utils — pure Python, no DB required."""

import pytest

from core.token_utils import estimate_messages_tokens, estimate_tokens


def test_estimate_tokens_empty_string_returns_min_1():
    """Empty string should return minimum 1, not 0."""
    assert estimate_tokens("") == 1


def test_estimate_tokens_simple_text_uses_len_div_4():
    """'hello world' is 11 chars → 11 // 4 = 2 tokens, min 1."""
    assert estimate_tokens("hello world") == max(1, len("hello world") // 4)


def test_estimate_tokens_long_text():
    """400-char string → 100 tokens."""
    text = "a" * 400
    assert estimate_tokens(text) == 100


def test_estimate_messages_tokens_empty_list_returns_zero():
    """No messages → 0 tokens."""
    assert estimate_messages_tokens([]) == 0


def test_estimate_messages_tokens_adds_4_overhead_per_message():
    """Each message contributes 4 tokens of overhead + content tokens."""
    messages = [{"role": "user", "content": ""}]
    # empty content → estimate_tokens("") = 1, plus 4 overhead = 5
    result = estimate_messages_tokens(messages)
    assert result == 4 + estimate_tokens("")


def test_estimate_messages_tokens_multiple_messages():
    """Two messages should sum correctly with per-message overhead."""
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    expected = sum(4 + estimate_tokens(m["content"]) for m in msgs)
    assert estimate_messages_tokens(msgs) == expected


def test_estimate_messages_tokens_missing_content_treated_as_empty():
    """Message without 'content' key should not crash — treated as empty."""
    messages = [{"role": "system"}]
    result = estimate_messages_tokens(messages)
    assert result == 4 + estimate_tokens("")
