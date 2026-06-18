# core/tests/test_tracked_chat_byok.py
"""Tests for TrackedChatModel BYOK behaviour — budget bypass + log flag."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from core import crypto
from core.llm import TrackedChatModel
from core.models import LLMCallLog, UserTokenBudget
from environments.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _crypto_key(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    crypto._reset_cache()
    yield
    crypto._reset_cache()


def _fake_llm(tokens_in: int = 100, tokens_out: int = 50):
    """Build a stub langchain BaseChatModel that returns a response with usage."""
    llm = MagicMock()
    response = MagicMock()
    response.usage_metadata = {"input_tokens": tokens_in, "output_tokens": tokens_out}
    llm.invoke.return_value = response
    return llm


# ── Budget bypass ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_byok_call_skips_budget_check_when_user_at_cap():
    """A user at their daily cap can still make BYOK calls."""
    user = UserFactory()
    budget = UserTokenBudget.load(user)
    budget.daily_cap = 1000
    budget.used_today = 999
    budget.save()

    tracked = TrackedChatModel(
        _fake_llm(tokens_in=500, tokens_out=200),
        user=user,
        provider="anthropic",
        model="claude-sonnet-4-6",
        purpose="ask",
        byok=True,
    )

    # Would normally raise TokenBudgetExceededError; BYOK skips the check.
    tracked.invoke([])

    budget.refresh_from_db()
    # BYOK never increments the managed-pool counter.
    assert budget.used_today == 999


@pytest.mark.django_db
def test_managed_call_still_enforces_budget():
    """A non-BYOK call at the cap still raises (regression check)."""
    from core.llm import TokenBudgetExceededError

    user = UserFactory()
    budget = UserTokenBudget.load(user)
    budget.daily_cap = 1000
    budget.used_today = 999
    budget.save()

    tracked = TrackedChatModel(
        _fake_llm(),
        user=user,
        provider="anthropic",
        model="claude-sonnet-4-6",
        purpose="ask",
        byok=False,
    )

    with pytest.raises(TokenBudgetExceededError):
        tracked.invoke([])


# ── LLMCallLog ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_byok_call_logs_byok_true():
    user = UserFactory()
    tracked = TrackedChatModel(
        _fake_llm(tokens_in=10, tokens_out=20),
        user=user,
        provider="groq",
        model="llama-3.3-70b-versatile",
        purpose="modify",
        byok=True,
    )

    tracked.invoke([])

    log = LLMCallLog.objects.get(user=user)
    assert log.byok is True
    assert log.provider == "groq"
    assert log.tokens_in == 10
    assert log.tokens_out == 20


@pytest.mark.django_db
def test_managed_call_logs_byok_false():
    user = UserFactory()
    tracked = TrackedChatModel(
        _fake_llm(),
        user=user,
        provider="anthropic",
        model="claude-sonnet-4-6",
        purpose="ask",
        byok=False,
    )

    tracked.invoke([])

    log = LLMCallLog.objects.get(user=user)
    assert log.byok is False


@pytest.mark.django_db
def test_byok_call_increments_no_budget_but_records_log():
    """Budget stays put; log row is written regardless."""
    user = UserFactory()
    budget = UserTokenBudget.load(user)
    budget.daily_cap = 10_000
    budget.used_today = 100
    budget.save()

    tracked = TrackedChatModel(
        _fake_llm(tokens_in=50, tokens_out=25),
        user=user,
        provider="anthropic",
        model="claude-sonnet-4-6",
        purpose="ask",
        byok=True,
    )

    tracked.invoke([])

    budget.refresh_from_db()
    assert budget.used_today == 100  # untouched
    assert LLMCallLog.objects.filter(user=user, byok=True).count() == 1


# ── Auth-error translation ────────────────────────────────────────────────


class _FakeAuthError(Exception):
    """Mimics groq.AuthenticationError / anthropic.AuthenticationError shape."""

    def __init__(self, status_code: int, message: str = "rejected"):
        super().__init__(message)
        self.status_code = status_code


def _failing_llm(status_code: int):
    llm = MagicMock()
    llm.invoke.side_effect = _FakeAuthError(status_code)
    return llm


@pytest.mark.django_db
def test_byok_401_converts_to_byok_auth_error():
    """A 401 from a BYOK call surfaces as BYOKAuthError carrying the provider name."""
    from core.llm import BYOKAuthError

    user = UserFactory()
    tracked = TrackedChatModel(
        _failing_llm(401),
        user=user,
        provider="groq",
        model="llama-3.3-70b-versatile",
        purpose="ask",
        byok=True,
    )

    with pytest.raises(BYOKAuthError) as excinfo:
        tracked.invoke([])
    assert excinfo.value.provider == "groq"
    # Original auth error is preserved as __cause__.
    assert isinstance(excinfo.value.__cause__, _FakeAuthError)


@pytest.mark.django_db
def test_byok_429_converts_to_byok_rate_limit_error():
    from core.llm import BYOKRateLimitError

    user = UserFactory()
    tracked = TrackedChatModel(
        _failing_llm(429),
        user=user,
        provider="anthropic",
        model="claude-sonnet-4-6",
        purpose="modify",
        byok=True,
    )

    with pytest.raises(BYOKRateLimitError) as excinfo:
        tracked.invoke([])
    assert excinfo.value.provider == "anthropic"


@pytest.mark.django_db
def test_managed_401_does_not_convert():
    """Non-BYOK 401 stays as the raw provider error — operator concern, not user."""
    user = UserFactory()
    tracked = TrackedChatModel(
        _failing_llm(401),
        user=user,
        provider="anthropic",
        model="claude-sonnet-4-6",
        purpose="ask",
        byok=False,
    )

    with pytest.raises(_FakeAuthError):
        tracked.invoke([])
