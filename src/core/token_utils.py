# core/token_utils.py
"""
Lightweight token estimation utilities.

Provides fast heuristic token counts without requiring a tokenizer.
The len/4 heuristic overestimates slightly — that is intentional: it
keeps budgets conservative, reducing the risk of silent context overflow.
"""


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for a string.

    Uses the len/4 heuristic (consistent with dump_context.py).
    Overestimates slightly — safe direction for budget calculations.
    """
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """
    Estimate token count for a list of {role, content} message dicts.

    Adds 4 tokens of overhead per message to account for role markers
    and message delimiters injected by most chat templates.
    """
    return sum(4 + estimate_tokens(msg.get("content", "")) for msg in messages)
