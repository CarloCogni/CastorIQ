# core/tests/test_usage_analytics.py
"""Tests for ``core.services.usage_analytics``.

Window correctness is the high-risk area — every helper takes a
``window_days`` argument and gets the cutoff wrong is silent. We pin
timestamps via post-insert ``.update(created_at=...)`` because
``LLMCallLog.created_at`` is ``auto_now_add`` and ignores values passed at
insert time.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import ErrorLog, LLMCallLog, UserTokenBudget
from core.services import usage_analytics
from environments.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _make_call(
    user,
    *,
    provider="ollama",
    purpose=LLMCallLog.Purpose.ASK,
    tokens_in=100,
    tokens_out=50,
    cost="0.001234",
    latency_ms=200,
    succeeded=True,
    error_type="",
    age_hours=0,
):
    """Create a call log row pinned to ``now - age_hours``.

    Default ``age_hours=0`` (i.e. ``created_at = now``) keeps tests that
    just need "a recent call in today's UTC bucket" stable when the suite
    happens to run near 00:00 UTC. Tests that genuinely need an older row
    pass an explicit ``age_hours`` (e.g. ``24 * 8`` for "8 days ago").
    """
    row = LLMCallLog.objects.create(
        user=user,
        provider=provider,
        model="test-model",
        purpose=purpose,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost_usd=Decimal(cost),
        latency_ms=latency_ms,
        succeeded=succeeded,
        error_type=error_type,
    )
    LLMCallLog.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timedelta(hours=age_hours)
    )
    row.refresh_from_db()
    return row


# ── kpis() ──────────────────────────────────────────────────────────────────


def test_kpis_returns_zeros_for_empty_log():
    result = usage_analytics.kpis(window_days=7)
    assert result["calls"] == 0
    assert result["tokens"] == 0
    assert result["cost_usd"] == Decimal("0")
    assert result["active_users"] == 0
    assert result["errors"] == 0
    assert result["p95_latency_ms"] is None


def test_kpis_window_excludes_older_rows():
    user = UserFactory()
    _make_call(user, age_hours=1, tokens_in=100, tokens_out=50)
    _make_call(user, age_hours=24 * 8, tokens_in=999, tokens_out=999)  # outside 7d

    result = usage_analytics.kpis(window_days=7)

    assert result["calls"] == 1
    assert result["tokens"] == 150
    assert result["active_users"] == 1


def test_kpis_24h_window_excludes_3_day_old_row():
    user = UserFactory()
    _make_call(user, age_hours=2, tokens_in=10, tokens_out=10)
    _make_call(user, age_hours=72, tokens_in=999, tokens_out=999)

    result = usage_analytics.kpis(window_days=1)

    assert result["calls"] == 1
    assert result["tokens"] == 20


def test_kpis_active_users_is_distinct():
    a = UserFactory()
    b = UserFactory()
    _make_call(a, age_hours=1)
    _make_call(a, age_hours=2)
    _make_call(b, age_hours=3)

    result = usage_analytics.kpis(window_days=7)

    assert result["calls"] == 3
    assert result["active_users"] == 2


def test_kpis_errors_combines_failed_calls_and_errorlog():
    user = UserFactory()
    _make_call(user, age_hours=1, succeeded=True)
    _make_call(user, age_hours=2, succeeded=False, error_type="TimeoutError")
    ErrorLog.objects.create(
        severity="error", exception_type="ValueError", message="boom"
    )

    result = usage_analytics.kpis(window_days=7)

    # 1 failed LLM call + 1 ErrorLog row = 2.
    assert result["errors"] == 2


def test_kpis_p95_latency_returns_largest_when_few_samples():
    user = UserFactory()
    for ms in (100, 200, 300):
        _make_call(user, latency_ms=ms, age_hours=1)

    result = usage_analytics.kpis(window_days=7)

    # Nearest-rank p95 of [100, 200, 300] = 300.
    assert result["p95_latency_ms"] == 300


# ── tokens_per_day() / cost_per_day() ───────────────────────────────────────


def test_tokens_per_day_buckets_and_provider_split():
    user = UserFactory()
    _make_call(user, provider="ollama", tokens_in=50, tokens_out=50, age_hours=0)
    _make_call(user, provider="anthropic", tokens_in=20, tokens_out=20, age_hours=0)
    _make_call(user, provider="ollama", tokens_in=10, tokens_out=10, age_hours=0)

    result = usage_analytics.tokens_per_day(window_days=7)

    assert len(result["labels"]) == 7
    assert set(result["providers"]) == {"ollama", "anthropic"}
    # Today's bucket (last index) holds the rows we just made.
    assert result["series"]["ollama"][-1] == 120  # 50+50 + 10+10
    assert result["series"]["anthropic"][-1] == 40
    # Earlier days are zero-filled.
    for i in range(len(result["labels"]) - 1):
        assert result["series"]["ollama"][i] == 0


def test_cost_per_day_window_boundary():
    user = UserFactory()
    _make_call(user, cost="1.00", age_hours=1)
    _make_call(user, cost="9.99", age_hours=24 * 31)  # outside 30d

    result = usage_analytics.cost_per_day(window_days=30)

    assert len(result["labels"]) == 30
    total = sum(sum(series) for series in result["series"].values())
    assert total == Decimal("1.00")


# ── top_users_by_cost() ─────────────────────────────────────────────────────


def test_top_users_by_cost_orders_desc_and_flags_blocked():
    a = UserFactory(username="alice")
    b = UserFactory(username="bob")
    _make_call(a, cost="2.00", age_hours=1)
    _make_call(b, cost="0.50", age_hours=1)
    UserTokenBudget.objects.create(user=a, hard_blocked=True)

    result = usage_analytics.top_users_by_cost(window_days=7, limit=10)

    assert [u["username"] for u in result] == ["alice", "bob"]
    assert result[0]["hard_blocked"] is True
    assert result[1]["hard_blocked"] is False


def test_top_users_by_cost_respects_limit():
    for n in range(5):
        u = UserFactory(username=f"u{n}")
        _make_call(u, cost=f"{n + 1}.00", age_hours=1)

    result = usage_analytics.top_users_by_cost(window_days=7, limit=3)

    assert len(result) == 3
    # Highest cost first.
    assert result[0]["username"] == "u4"


def test_top_users_by_cost_excludes_anonymous():
    """LLMCallLog rows with user=NULL must not crash or appear."""
    LLMCallLog.objects.create(
        user=None,
        provider="ollama",
        model="x",
        purpose=LLMCallLog.Purpose.ASK,
        tokens_in=1,
        tokens_out=1,
        estimated_cost_usd=Decimal("99"),
    )
    result = usage_analytics.top_users_by_cost(window_days=7, limit=10)
    assert result == []


# ── system_pulse() ──────────────────────────────────────────────────────────


def test_system_pulse_counts_unresolved_errors_and_blocks():
    a = UserFactory()
    UserTokenBudget.objects.create(user=a, hard_blocked=True)
    ErrorLog.objects.create(
        severity="error", exception_type="X", message="m", is_resolved=False
    )
    ErrorLog.objects.create(
        severity="error", exception_type="Y", message="m", is_resolved=True
    )

    pulse = usage_analytics.system_pulse()

    assert pulse["unresolved_errors"] == 1
    assert pulse["hard_blocked_users"] == 1
    assert pulse["calls_last_hour"] == 0


def test_system_pulse_calls_last_hour_excludes_older():
    user = UserFactory()
    _make_call(user, age_hours=0)  # well within last hour
    _make_call(user, age_hours=2)  # outside

    pulse = usage_analytics.system_pulse()
    assert pulse["calls_last_hour"] == 1


# ── Tab 2 helpers ───────────────────────────────────────────────────────────


def test_cost_per_day_by_purpose_splits_ask_and_modify():
    user = UserFactory()
    _make_call(
        user,
        purpose=LLMCallLog.Purpose.ASK,
        cost="2.00",
        age_hours=0,
    )
    _make_call(
        user,
        purpose=LLMCallLog.Purpose.MODIFY,
        cost="0.50",
        age_hours=0,
    )

    result = usage_analytics.cost_per_day_by_purpose(window_days=7)

    assert set(result["categories"]) == {"ask", "modify"}
    assert result["series"]["ask"][-1] == Decimal("2.00")
    assert result["series"]["modify"][-1] == Decimal("0.50")
    # Earlier days zero-filled.
    assert result["series"]["ask"][0] == Decimal("0")


def test_tokens_per_day_local_vs_paid_collapses_providers():
    user = UserFactory()
    _make_call(user, provider="ollama", tokens_in=100, tokens_out=50, age_hours=0)
    _make_call(user, provider="anthropic", tokens_in=20, tokens_out=10, age_hours=0)
    _make_call(user, provider="groq", tokens_in=5, tokens_out=5, age_hours=0)

    result = usage_analytics.tokens_per_day_local_vs_paid(window_days=7)

    assert set(result["categories"]) == {"local", "paid"}
    # Today's bucket: ollama 150, anthropic+groq = 30+10 = 40 paid.
    assert result["series"]["local"][-1] == 150
    assert result["series"]["paid"][-1] == 40


def test_tokens_per_day_local_vs_paid_unknown_provider_counts_as_paid():
    """Anything not 'ollama' is paid — including future/typo providers."""
    user = UserFactory()
    _make_call(user, provider="future_cloud", tokens_in=10, tokens_out=10, age_hours=0)

    result = usage_analytics.tokens_per_day_local_vs_paid(window_days=7)

    assert "paid" in result["series"]
    assert result["series"]["paid"][-1] == 20


def test_cost_per_active_user_per_day_divides_by_dau():
    a = UserFactory()
    b = UserFactory()
    _make_call(a, cost="1.00", age_hours=0)
    _make_call(b, cost="3.00", age_hours=0)

    result = usage_analytics.cost_per_active_user_per_day(window_days=7)

    assert len(result["labels"]) == 7
    # Today: $4.00 total / 2 users = $2.00.
    assert result["values"][-1] == pytest.approx(2.0)
    # Days with no activity → 0.
    assert result["values"][0] == 0.0


def test_user_budget_strip_lists_recently_active_users():
    a = UserFactory(username="alice")
    b = UserFactory(username="bob")
    UserTokenBudget.objects.create(
        user=a, daily_cap=1000, used_today=900, hard_blocked=False
    )
    UserTokenBudget.objects.create(
        user=b, daily_cap=1000, used_today=0, hard_blocked=False
    )
    _make_call(a, age_hours=1)  # alice is recently active
    # Bob has no activity → excluded.

    result = usage_analytics.user_budget_strip(activity_window_days=7)

    usernames = [r["username"] for r in result]
    assert "alice" in usernames
    assert "bob" not in usernames


def test_user_budget_strip_includes_users_with_residual_used_today():
    """A user with no recent calls but non-zero used_today still surfaces.

    Useful when a user hit the cap, got blocked, and stopped firing calls —
    we still want them visible until the next reset.
    """
    a = UserFactory(username="alice")
    UserTokenBudget.objects.create(user=a, daily_cap=1000, used_today=500)

    result = usage_analytics.user_budget_strip(activity_window_days=7)

    assert any(r["username"] == "alice" for r in result)


def test_user_budget_strip_percent_handles_unlimited_cap():
    a = UserFactory(username="alice")
    UserTokenBudget.objects.create(user=a, daily_cap=0, used_today=999)
    _make_call(a, age_hours=1)

    [row] = [r for r in usage_analytics.user_budget_strip() if r["username"] == "alice"]
    assert row["percent"] is None
    assert row["used_today"] == 999


def test_user_budget_strip_percent_capped_at_200():
    """Over-cap users render at 100% bar but report the real percent up to 200%."""
    a = UserFactory(username="alice")
    UserTokenBudget.objects.create(user=a, daily_cap=100, used_today=500)
    _make_call(a, age_hours=1)

    [row] = [r for r in usage_analytics.user_budget_strip() if r["username"] == "alice"]
    # min(500/100, 2.0) * 100 = 200.0
    assert row["percent"] == 200.0


def test_user_budget_strip_orders_blocked_first():
    a = UserFactory(username="alice")
    b = UserFactory(username="bob")
    UserTokenBudget.objects.create(
        user=a, daily_cap=1000, used_today=999, hard_blocked=False
    )
    UserTokenBudget.objects.create(
        user=b, daily_cap=1000, used_today=10, hard_blocked=True
    )
    _make_call(a, age_hours=1)
    _make_call(b, age_hours=1)

    result = usage_analytics.user_budget_strip()
    # bob (blocked) before alice (high usage but unblocked).
    assert [r["username"] for r in result] == ["bob", "alice"]


# ── Tab 3 helpers ───────────────────────────────────────────────────────────


def test_kpis_for_reliability_empty_log_returns_none_for_rates():
    result = usage_analytics.kpis_for_reliability(window_days=7)
    assert result["calls"] == 0
    assert result["success_pct"] is None
    assert result["errors"] == 0
    assert result["p95_latency_ask_ms"] is None
    assert result["p95_latency_modify_ms"] is None


def test_kpis_for_reliability_success_pct_and_split_latency():
    user = UserFactory()
    _make_call(
        user,
        purpose=LLMCallLog.Purpose.ASK,
        latency_ms=100,
        succeeded=True,
        age_hours=1,
    )
    _make_call(
        user,
        purpose=LLMCallLog.Purpose.ASK,
        latency_ms=200,
        succeeded=True,
        age_hours=1,
    )
    _make_call(
        user,
        purpose=LLMCallLog.Purpose.MODIFY,
        latency_ms=500,
        succeeded=False,
        age_hours=1,
    )

    result = usage_analytics.kpis_for_reliability(window_days=7)

    assert result["calls"] == 3
    # 2 succeeded out of 3 = 66.7%
    assert result["success_pct"] == pytest.approx(66.7, rel=0.01)
    assert result["errors"] == 1
    assert result["p95_latency_ask_ms"] == 200
    assert result["p95_latency_modify_ms"] == 500


# ── provider_success_rate ───────────────────────────────────────────────────


def test_provider_success_rate_excludes_zero_call_providers():
    """No row for providers with no calls in the window."""
    result = usage_analytics.provider_success_rate(window_days=7)
    assert result == []


def test_provider_success_rate_computes_per_provider_pct():
    user = UserFactory()
    _make_call(user, provider="ollama", succeeded=True, age_hours=1)
    _make_call(user, provider="ollama", succeeded=True, age_hours=1)
    _make_call(user, provider="ollama", succeeded=False, age_hours=1)
    _make_call(user, provider="groq", succeeded=True, age_hours=1)

    result = usage_analytics.provider_success_rate(window_days=7)

    by_provider = {r["provider"]: r for r in result}
    assert by_provider["ollama"]["calls"] == 3
    assert by_provider["ollama"]["succeeded"] == 2
    assert by_provider["ollama"]["success_pct"] == pytest.approx(66.7, rel=0.01)
    assert by_provider["groq"]["success_pct"] == 100.0


def test_provider_success_rate_window_boundary():
    user = UserFactory()
    _make_call(user, provider="ollama", age_hours=1)
    _make_call(user, provider="ollama", age_hours=24 * 8)  # outside 7d

    result = usage_analytics.provider_success_rate(window_days=7)
    [row] = result
    assert row["calls"] == 1


# ── p95_latency_per_purpose_per_day ─────────────────────────────────────────


def test_p95_latency_per_purpose_per_day_empty_yields_none_series():
    result = usage_analytics.p95_latency_per_purpose_per_day(window_days=7)
    assert len(result["labels"]) == 7
    assert all(v is None for v in result["series"]["ask"])
    assert all(v is None for v in result["series"]["modify"])


def test_p95_latency_per_purpose_per_day_gap_when_no_calls_that_day():
    """Days with no calls return None (Chart.js gap), not 0."""
    user = UserFactory()
    _make_call(
        user,
        purpose=LLMCallLog.Purpose.ASK,
        latency_ms=300,
        age_hours=0,
    )

    result = usage_analytics.p95_latency_per_purpose_per_day(window_days=7)

    assert result["series"]["ask"][-1] == 300
    # All earlier days must be None.
    for v in result["series"]["ask"][:-1]:
        assert v is None
    # Modify never has data → all None.
    assert all(v is None for v in result["series"]["modify"])


def test_p95_latency_per_purpose_per_day_separates_ask_modify():
    user = UserFactory()
    _make_call(user, purpose=LLMCallLog.Purpose.ASK, latency_ms=100, age_hours=0)
    _make_call(user, purpose=LLMCallLog.Purpose.MODIFY, latency_ms=900, age_hours=0)

    result = usage_analytics.p95_latency_per_purpose_per_day(window_days=7)
    assert result["series"]["ask"][-1] == 100
    assert result["series"]["modify"][-1] == 900


# ── error_type_breakdown ────────────────────────────────────────────────────


def test_error_type_breakdown_empty_yields_zero_total():
    result = usage_analytics.error_type_breakdown(window_days=7)
    assert result["total"] == 0
    assert result["top"] == []
    assert result["other"] == 0


def test_error_type_breakdown_unions_llm_and_errorlog():
    """Same label on both sides collapses to one bucket."""
    user = UserFactory()
    _make_call(user, succeeded=False, error_type="TimeoutError", age_hours=1)
    ErrorLog.objects.create(
        severity="error", exception_type="TimeoutError", message="m"
    )

    result = usage_analytics.error_type_breakdown(window_days=7)

    by_label = {r["label"]: r["count"] for r in result["top"]}
    assert by_label["TimeoutError"] == 2
    assert result["total"] == 2


def test_error_type_breakdown_normalises_blank_to_unknown():
    user = UserFactory()
    _make_call(user, succeeded=False, error_type="", age_hours=1)
    ErrorLog.objects.create(severity="error", exception_type="", message="m")

    result = usage_analytics.error_type_breakdown(window_days=7)
    by_label = {r["label"]: r["count"] for r in result["top"]}
    assert by_label["(unknown)"] == 2


def test_error_type_breakdown_excludes_succeeded_llm_calls():
    """Failed-call filter on LLMCallLog: succeeded=True rows must not contribute."""
    user = UserFactory()
    _make_call(user, succeeded=True, error_type="ShouldNotAppear", age_hours=1)

    result = usage_analytics.error_type_breakdown(window_days=7)
    assert result["total"] == 0


def test_error_type_breakdown_collapses_into_other_past_limit():
    """Anything past `limit` rolls into the `other` bucket."""
    for i in range(15):
        ErrorLog.objects.create(
            severity="error", exception_type=f"ExcType{i}", message="m"
        )

    result = usage_analytics.error_type_breakdown(window_days=7, limit=10)

    assert len(result["top"]) == 10
    assert result["other"] == 5
    assert result["total"] == 15


# ── unresolved_error_backlog ────────────────────────────────────────────────


def test_unresolved_error_backlog_empty_zero_total():
    result = usage_analytics.unresolved_error_backlog()
    assert result["total"] == 0
    assert all(b["count"] == 0 for b in result["buckets"])
    assert result["recent"] == []


def test_unresolved_error_backlog_age_buckets():
    row_fresh = ErrorLog.objects.create(
        severity="error", exception_type="A", message="m", is_resolved=False
    )
    row_mid = ErrorLog.objects.create(
        severity="error", exception_type="B", message="m", is_resolved=False
    )
    row_stale = ErrorLog.objects.create(
        severity="error", exception_type="C", message="m", is_resolved=False
    )
    row_resolved = ErrorLog.objects.create(
        severity="error", exception_type="Z", message="m", is_resolved=True
    )

    # Pin ages: fresh = 1h ago, mid = 3d ago, stale = 30d ago.
    now = timezone.now()
    ErrorLog.objects.filter(pk=row_fresh.pk).update(created_at=now - timedelta(hours=1))
    ErrorLog.objects.filter(pk=row_mid.pk).update(created_at=now - timedelta(days=3))
    ErrorLog.objects.filter(pk=row_stale.pk).update(created_at=now - timedelta(days=30))
    ErrorLog.objects.filter(pk=row_resolved.pk).update(created_at=now - timedelta(hours=1))

    result = usage_analytics.unresolved_error_backlog()

    by_label = {b["label"]: b["count"] for b in result["buckets"]}
    assert by_label["<24h"] == 1
    assert by_label["24h–7d"] == 1
    assert by_label[">7d"] == 1
    assert result["total"] == 3  # resolved row excluded


def test_unresolved_error_backlog_recent_capped_at_10():
    for i in range(15):
        ErrorLog.objects.create(
            severity="error", exception_type=f"E{i}", message="m", is_resolved=False
        )
    result = usage_analytics.unresolved_error_backlog()
    assert len(result["recent"]) == 10


# ── failure_record_taxonomy ─────────────────────────────────────────────────


def test_failure_record_taxonomy_empty_returns_zero_total():
    result = usage_analytics.failure_record_taxonomy(window_days=7)
    assert result["total"] == 0
    assert result["phases"]  # phases list still populated for template grid
    assert all(cell == 0 for row in result["grid"] for cell in row)


def test_failure_record_taxonomy_buckets_by_phase_and_category_and_tier():
    from metacastor.models import FailureRecord

    from environments.tests.factories import ProjectFactory

    project = ProjectFactory()

    # 2 VALIDATION+RETRYABLE at tier=None, 1 EXECUTION+NON_RETRYABLE at tier=2,
    # 1 SANDBOX+NON_RETRYABLE at tier=3
    FailureRecord.objects.create(
        project=project,
        query_text="q1",
        failure_phase=FailureRecord.FailurePhase.VALIDATION,
        category=FailureRecord.Category.RETRYABLE,
        tier=None,
        error_type="VALIDATION_ERROR",
        error_detail="x",
        diagnosis="x",
    )
    FailureRecord.objects.create(
        project=project,
        query_text="q2",
        failure_phase=FailureRecord.FailurePhase.VALIDATION,
        category=FailureRecord.Category.RETRYABLE,
        tier=None,
        error_type="VALIDATION_ERROR",
        error_detail="x",
        diagnosis="x",
    )
    FailureRecord.objects.create(
        project=project,
        query_text="q3",
        failure_phase=FailureRecord.FailurePhase.EXECUTION,
        category=FailureRecord.Category.NON_RETRYABLE,
        tier=2,
        error_type="EXEC",
        error_detail="x",
        diagnosis="x",
    )
    FailureRecord.objects.create(
        project=project,
        query_text="q4",
        failure_phase=FailureRecord.FailurePhase.SANDBOX,
        category=FailureRecord.Category.NON_RETRYABLE,
        tier=3,
        error_type="SBX",
        error_detail="x",
        diagnosis="x",
    )

    result = usage_analytics.failure_record_taxonomy(window_days=7)

    assert result["total"] == 4
    # phase × category grid: indices match the order returned in `phases`/`categories`.
    phase_idx = {p: i for i, p in enumerate(result["phases"])}
    cat_idx = {c: i for i, c in enumerate(result["categories"])}
    assert result["grid"][phase_idx["VALIDATION"]][cat_idx["RETRYABLE"]] == 2
    assert result["grid"][phase_idx["EXECUTION"]][cat_idx["NON_RETRYABLE"]] == 1
    assert result["grid"][phase_idx["SANDBOX"]][cat_idx["NON_RETRYABLE"]] == 1

    # Tier totals — None bucket is populated for early-VALIDATION failures.
    assert result["tier_totals"][None] == 2
    assert result["tier_totals"][2] == 1
    assert result["tier_totals"][3] == 1
    assert result["tier_totals"][1] == 0


# ── ingestion_status ────────────────────────────────────────────────────────


def test_ingestion_status_empty_returns_zero_totals():
    result = usage_analytics.ingestion_status(window_days=7)
    assert result["documents"]["total"] == 0
    assert result["documents"]["success_pct"] is None
    assert result["ifc_files"]["total"] == 0
    assert result["ifc_files"]["success_pct"] is None


def test_ingestion_status_success_pct_excludes_pending_and_processing():
    """Success % = completed / (completed + failed). Pending/processing don't count."""
    from documents.models import Document
    from environments.tests.factories import ProjectFactory

    project = ProjectFactory()
    Document.objects.create(
        project=project,
        name="a.pdf",
        document_type=Document.DocumentType.PDF,
        status=Document.Status.COMPLETED,
    )
    Document.objects.create(
        project=project,
        name="b.pdf",
        document_type=Document.DocumentType.PDF,
        status=Document.Status.FAILED,
    )
    Document.objects.create(
        project=project,
        name="c.pdf",
        document_type=Document.DocumentType.PDF,
        status=Document.Status.PENDING,
    )

    result = usage_analytics.ingestion_status(window_days=7)

    docs = result["documents"]
    assert docs["total"] == 3
    # 1 completed / 2 terminal = 50%
    assert docs["success_pct"] == 50.0
    assert docs["by_status"]["completed"] == 1
    assert docs["by_status"]["failed"] == 1
    assert docs["by_status"]["pending"] == 1
    assert docs["by_status"]["processing"] == 0


# ── Tab 4 helpers ───────────────────────────────────────────────────────────


def _set_user_date_joined(user, when):
    """Pin a user's date_joined post-insert (factory auto-fills with now())."""
    type(user).objects.filter(pk=user.pk).update(date_joined=when)
    user.refresh_from_db()


def _set_proposal_created_at(proposal, when):
    """Pin a proposal's created_at — auto_now_add ignores values at insert."""
    type(proposal).objects.filter(pk=proposal.pk).update(created_at=when)
    proposal.refresh_from_db()


# engagement_kpis


def test_engagement_kpis_empty_log():
    result = usage_analytics.engagement_kpis(window_days=7)
    assert result["dau"] == 0
    assert result["wau"] == 0
    assert result["mau"] == 0
    assert result["stickiness_pct"] is None
    assert result["proposal_generators_pct"] is None
    assert result["active_users_window"] == 0


def test_engagement_kpis_counts_dau_wau_mau_distinct():
    a = UserFactory()
    b = UserFactory()
    # Both fire calls today → DAU=2, WAU/MAU≥2.
    _make_call(a, age_hours=0)
    _make_call(a, age_hours=0)
    _make_call(b, age_hours=0)
    # One older call from a third user → MAU+1, WAU unchanged.
    c = UserFactory()
    _make_call(c, age_hours=24 * 20)

    result = usage_analytics.engagement_kpis(window_days=7)

    assert result["dau"] == 2
    assert result["wau"] == 2
    assert result["mau"] == 3
    # Stickiness = 2/3 = 66.7%
    assert result["stickiness_pct"] == pytest.approx(66.7, rel=0.01)


def test_engagement_kpis_proposal_generators_share():
    from writeback.tests.factories import ModificationProposalFactory

    a = UserFactory()
    b = UserFactory()
    _make_call(a, age_hours=1)
    _make_call(b, age_hours=1)
    # Only `a` creates a proposal in the window.
    ModificationProposalFactory(created_by=a)

    result = usage_analytics.engagement_kpis(window_days=7)
    # 1 of 2 active users created a proposal = 50%.
    assert result["proposal_generators_pct"] == 50.0


# dau_wau_mau


def test_dau_wau_mau_returns_window_length_labels():
    result = usage_analytics.dau_wau_mau(window_days=14)
    assert len(result["labels"]) == 14
    assert len(result["series"]["dau"]) == 14
    assert len(result["series"]["wau"]) == 14
    assert len(result["series"]["mau"]) == 14


def test_dau_wau_mau_today_reflects_recent_activity():
    user = UserFactory()
    _make_call(user, age_hours=0)

    result = usage_analytics.dau_wau_mau(window_days=7)
    # Last bucket = today.
    assert result["series"]["dau"][-1] == 1
    assert result["series"]["wau"][-1] == 1
    assert result["series"]["mau"][-1] == 1


def test_dau_wau_mau_wau_window_includes_six_day_old_call():
    """WAU on day N counts users active across [N-6, N]."""
    user = UserFactory()
    # 5 days ago → still inside today's WAU.
    _make_call(user, age_hours=24 * 5)

    result = usage_analytics.dau_wau_mau(window_days=7)
    # WAU today = 1 (the 5-day-old call), DAU today = 0.
    assert result["series"]["dau"][-1] == 0
    assert result["series"]["wau"][-1] == 1


# time_to_first_value


def test_time_to_first_value_empty_returns_zero_user_total():
    """With no users, all buckets stay at 0."""
    result = usage_analytics.time_to_first_value()
    assert result["user_total"] == 0
    assert all(v == 0 for v in result["first_ask"].values())
    assert all(v == 0 for v in result["first_proposal"].values())


def test_time_to_first_value_buckets_first_ask_correctly():
    """A user joining and firing their first Ask within 30 minutes → <1h bucket."""
    user = UserFactory()
    join_at = timezone.now() - timedelta(hours=1)
    _set_user_date_joined(user, join_at)
    # Fire a successful call 30min after join (so 30min before "now").
    _make_call(user, succeeded=True, age_hours=0.5)

    result = usage_analytics.time_to_first_value()
    assert result["first_ask"]["<1h"] == 1
    # No proposal exists → "never" bucket.
    assert result["first_proposal"]["never"] == 1


def test_time_to_first_value_never_when_only_failed_calls():
    """First Ask requires succeeded=True."""
    user = UserFactory()
    _set_user_date_joined(user, timezone.now() - timedelta(days=2))
    _make_call(user, succeeded=False, age_hours=1)

    result = usage_analytics.time_to_first_value()
    assert result["first_ask"]["never"] == 1


# feature_mix


def test_feature_mix_empty_returns_zero_total():
    result = usage_analytics.feature_mix(window_days=30)
    assert result["total"] == 0
    assert result["ask_only"] == 0
    assert result["both"] == 0
    assert result["modify_only"] == 0


def test_feature_mix_classifies_users_by_purpose():
    ask_user = UserFactory()
    both_user = UserFactory()
    modify_user = UserFactory()
    _make_call(ask_user, purpose=LLMCallLog.Purpose.ASK, age_hours=1)
    _make_call(both_user, purpose=LLMCallLog.Purpose.ASK, age_hours=1)
    _make_call(both_user, purpose=LLMCallLog.Purpose.MODIFY, age_hours=1)
    _make_call(modify_user, purpose=LLMCallLog.Purpose.MODIFY, age_hours=1)

    result = usage_analytics.feature_mix(window_days=30)
    assert result["ask_only"] == 1
    assert result["both"] == 1
    assert result["modify_only"] == 1
    assert result["total"] == 3


# modify_funnel


def test_modify_funnel_empty_returns_zero_total():
    result = usage_analytics.modify_funnel(window_days=30)
    assert result["total"] == 0
    assert result["tiers"] == [1, 2, 3]  # untiered NOT appended when empty
    assert all(cell == 0 for row in result["grid"] for cell in row)


def test_modify_funnel_buckets_by_tier_and_stage():
    from writeback.tests.factories import ModificationProposalFactory

    # T1 applied × 2, T2 pending × 1, T3 rejected × 1.
    ModificationProposalFactory(tier=1, status="applied")
    ModificationProposalFactory(tier=1, status="applied")
    ModificationProposalFactory(tier=2, status="pending")
    ModificationProposalFactory(tier=3, status="rejected")

    result = usage_analytics.modify_funnel(window_days=30)
    assert result["total"] == 4

    stage_idx = {s: j for j, s in enumerate(result["stages"])}
    tier_idx = {t: i for i, t in enumerate(result["tiers"])}
    assert result["grid"][tier_idx[1]][stage_idx["applied"]] == 2
    assert result["grid"][tier_idx[2]][stage_idx["pending"]] == 1
    assert result["grid"][tier_idx[3]][stage_idx["rejected"]] == 1


def test_modify_funnel_appends_untiered_row_only_when_nonzero():
    """Tier=None proposals collapse into 'untiered' — but only when present."""
    from writeback.tests.factories import ModificationProposalFactory

    ModificationProposalFactory(tier=None, status="failed")

    result = usage_analytics.modify_funnel(window_days=30)
    assert "untiered" in result["tiers"]
    untiered_idx = result["tiers"].index("untiered")
    failed_idx = result["stages"].index("failed")
    assert result["grid"][untiered_idx][failed_idx] == 1


# activity_heatmap


def test_activity_heatmap_empty_returns_zero_max():
    result = usage_analytics.activity_heatmap(window_days=7)
    assert result["max_count"] == 0
    assert len(result["matrix"]) == 7  # one row per day-of-week
    assert all(len(row) == 24 for row in result["matrix"])
    assert all(cell == 0 for row in result["matrix"] for cell in row)


def test_activity_heatmap_increments_bucket_for_each_call():
    user = UserFactory()
    _make_call(user, age_hours=1)
    _make_call(user, age_hours=1)

    result = usage_analytics.activity_heatmap(window_days=7)
    total = sum(cell for row in result["matrix"] for cell in row)
    assert total == 2
    assert result["max_count"] >= 1


# cohort_retention_grid


def test_cohort_retention_grid_empty_returns_no_cohorts():
    result = usage_analytics.cohort_retention_grid(weeks=8)
    assert result["cohorts"] == []
    assert result["grid"] == []
    assert result["weeks_offsets"] == list(range(8))


def test_cohort_retention_grid_w0_reflects_signup_week_activity():
    """A user who signed up this week AND fired a call this week → W0 = 100%."""
    user = UserFactory()
    # Small delta (5 min) keeps date_joined inside today's UTC date even when
    # the suite runs near midnight; a 2-hour backdate would put W1 in the
    # past at 00:14 UTC and fail the "future cells are None" assertion.
    _set_user_date_joined(user, timezone.now() - timedelta(minutes=5))
    _make_call(user, age_hours=0)

    result = usage_analytics.cohort_retention_grid(weeks=4)
    assert len(result["cohorts"]) == 1
    cohort = result["cohorts"][0]
    assert cohort["size"] == 1
    # W0 = 100% (the user was active during their sign-up week).
    assert result["grid"][0][0] == 100.0
    # Future weeks (W1..W3) haven't elapsed → None.
    for cell in result["grid"][0][1:]:
        assert cell is None


def test_cohort_retention_grid_inactive_cohort_member_is_zero_pct():
    """A user who signed up this week but never fired a call → W0 = 0%."""
    UserFactory()  # signed up "now", no calls
    result = usage_analytics.cohort_retention_grid(weeks=4)
    assert len(result["cohorts"]) == 1
    assert result["grid"][0][0] == 0.0


# ── Tab 7 helpers ───────────────────────────────────────────────────────────


# proposal_acceptance_rate_by_tier


def test_proposal_acceptance_empty_returns_none_pct_per_tier():
    result = usage_analytics.proposal_acceptance_rate_by_tier(window_days=30)
    assert result["overall_total"] == 0
    assert [t["tier"] for t in result["by_tier"]] == [1, 2, 3]
    for t in result["by_tier"]:
        assert t["total"] == 0
        assert t["accepted_pct"] is None
        assert t["meets_target"] is False


def test_proposal_acceptance_collapses_approved_and_applied():
    """'Accepted' = approved + applied — both count as 'good enough to land'."""
    from writeback.tests.factories import ModificationProposalFactory

    ModificationProposalFactory(tier=1, status="applied")
    ModificationProposalFactory(tier=1, status="approved")
    ModificationProposalFactory(tier=1, status="rejected")
    ModificationProposalFactory(tier=1, status="pending")

    result = usage_analytics.proposal_acceptance_rate_by_tier(window_days=30)
    t1 = next(t for t in result["by_tier"] if t["tier"] == 1)
    assert t1["total"] == 4
    assert t1["applied"] == 2
    # 2/4 = 50%
    assert t1["accepted_pct"] == 50.0
    # Below T1's 90% target.
    assert t1["meets_target"] is False


def test_proposal_acceptance_meets_target_when_above_threshold():
    from writeback.tests.factories import ModificationProposalFactory

    for _ in range(9):
        ModificationProposalFactory(tier=1, status="applied")
    ModificationProposalFactory(tier=1, status="rejected")

    result = usage_analytics.proposal_acceptance_rate_by_tier(window_days=30)
    t1 = next(t for t in result["by_tier"] if t["tier"] == 1)
    assert t1["accepted_pct"] == 90.0  # at the boundary
    assert t1["meets_target"] is True


def test_proposal_acceptance_excludes_tier_none_rows():
    """Tier-None rows are early-validation failures, not a tier signal."""
    from writeback.tests.factories import ModificationProposalFactory

    ModificationProposalFactory(tier=None, status="failed")

    result = usage_analytics.proposal_acceptance_rate_by_tier(window_days=30)
    assert result["overall_total"] == 0


# provider_mix_summary


def test_provider_mix_summary_empty_returns_none_pcts():
    result = usage_analytics.provider_mix_summary(window_days=30)
    assert result["total_tokens"] == 0
    assert result["local_pct"] is None
    assert result["paid_pct"] is None


def test_provider_mix_summary_collapses_ollama_into_local():
    user = UserFactory()
    _make_call(user, provider="ollama", tokens_in=80, tokens_out=20, age_hours=0)
    _make_call(user, provider="anthropic", tokens_in=10, tokens_out=10, age_hours=0)
    _make_call(user, provider="groq", tokens_in=5, tokens_out=5, age_hours=0)

    result = usage_analytics.provider_mix_summary(window_days=30)
    # local: 100; paid: 30; total: 130
    assert result["local_tokens"] == 100
    assert result["paid_tokens"] == 30
    assert result["total_tokens"] == 130
    assert result["local_pct"] == pytest.approx(76.9, rel=0.01)
    assert result["paid_pct"] == pytest.approx(23.1, rel=0.01)


# ifc_ingestion_scatter


def test_ifc_ingestion_scatter_empty_returns_no_points():
    result = usage_analytics.ifc_ingestion_scatter()
    assert result["count"] == 0
    assert result["points"] == []
    assert result["p95_latency_s"] is None


def test_ifc_ingestion_scatter_only_includes_completed_with_processed_at():
    """Failed / in-flight files must be excluded so the scatter is honest."""
    from ifc_processor.models import IFCFile
    from environments.tests.factories import ProjectFactory

    project = ProjectFactory()
    now = timezone.now()

    # Completed file with processed_at → included.
    good = IFCFile.objects.create(
        project=project,
        name="good.ifc",
        file_hash="a" * 64,
        status=IFCFile.Status.COMPLETED,
        entity_count=1000,
        processed_at=now,
    )
    # Backdate created_at so latency is positive and computable.
    IFCFile.objects.filter(pk=good.pk).update(created_at=now - timedelta(seconds=12))

    # Failed file with processed_at → excluded.
    IFCFile.objects.create(
        project=project,
        name="failed.ifc",
        file_hash="b" * 64,
        status=IFCFile.Status.FAILED,
        entity_count=2000,
        processed_at=now,
    )
    # Pending file (no processed_at) → excluded.
    IFCFile.objects.create(
        project=project,
        name="pending.ifc",
        file_hash="c" * 64,
        status=IFCFile.Status.PENDING,
        entity_count=500,
    )

    result = usage_analytics.ifc_ingestion_scatter()
    assert result["count"] == 1
    [pt] = result["points"]
    assert pt["entities"] == 1000
    assert pt["latency_seconds"] == pytest.approx(12.0, abs=1)
    assert pt["name"] == "good.ifc"


# design_partner_engagement


def test_design_partner_engagement_empty_returns_no_rows():
    result = usage_analytics.design_partner_engagement(window_days=30)
    assert result == []


def test_design_partner_engagement_counts_calls_and_proposals():
    from writeback.tests.factories import ModificationProposalFactory

    a = UserFactory(username="alice")
    b = UserFactory(username="bob")
    # alice: 3 calls, 1 proposal; bob: 1 call, 0 proposals.
    _make_call(a, age_hours=0)
    _make_call(a, age_hours=0)
    _make_call(a, age_hours=0)
    _make_call(b, age_hours=0)
    ModificationProposalFactory(created_by=a)

    result = usage_analytics.design_partner_engagement(window_days=30)

    by_username = {r["username"]: r for r in result}
    assert by_username["alice"]["calls"] == 3
    assert by_username["alice"]["proposals"] == 1
    assert by_username["bob"]["calls"] == 1
    assert by_username["bob"]["proposals"] == 0
    # Ordered by calls desc.
    assert result[0]["username"] == "alice"


# investor_kpis


def test_investor_kpis_empty_dataset_returns_safe_defaults():
    result = usage_analytics.investor_kpis()
    assert result["mau_30d"] == 0
    assert result["total_entities_processed"] == 0
    assert result["local_token_share_pct"] is None
    assert result["avg_w4_retention_pct"] is None
    assert result["cohorts_with_w4"] == 0
    # ISO date format YYYY-MM-DD.
    assert len(result["week_ending"]) == 10


def test_investor_kpis_sums_entities_for_completed_only():
    from ifc_processor.models import IFCFile
    from environments.tests.factories import ProjectFactory

    project = ProjectFactory()
    IFCFile.objects.create(
        project=project,
        name="ok.ifc",
        file_hash="a" * 64,
        status=IFCFile.Status.COMPLETED,
        entity_count=1234,
    )
    IFCFile.objects.create(
        project=project,
        name="bad.ifc",
        file_hash="b" * 64,
        status=IFCFile.Status.FAILED,
        entity_count=9999,  # must not count
    )

    result = usage_analytics.investor_kpis()
    assert result["total_entities_processed"] == 1234
