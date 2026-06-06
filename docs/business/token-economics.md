# Token Economics — Castor Beta

> Cost model and the rules that keep $150 of LLM credit alive for a month.
> Strategy context: `public-beta-barcelona-2026.md`. Implementation milestone: `live-roadmap.md` M2.

---

## Provider mix

| Endpoint | Default provider | Default model | Why |
|---|---|---|---|
| Ask (`chat/_ask.html`) | Anthropic | `claude-sonnet-4-6` | High-quality reasoning over RAG context (IFC entities + documents). Citations matter; correctness > latency. |
| Modify (`writeback/`) | Groq | `meta-llama/llama-4-scout-17b-16e-instruct` | Llama 4 Scout — MoE, ~5× cheaper input and ~50% faster than Llama 3.3 70B. The Modify pipeline makes 4–8 sequential structured calls per request, so speed compounds. Llama 3.3 70B kept on the registry as a battle-tested fallback if Scout's JSON-mode flakes. |
| Embeddings | Ollama on VPS | `mxbai-embed-large` (1024-dim) | Zero per-call cost, EU-resident, fits existing pgvector schema. CPU-only at launch. Voyage AI documented as escalation in `vps-deployment.md`. |

**Defaults, not contracts.** The Ask/Modify bindings above live in the `SiteLLMConfig` admin singleton (M1) and are flippable at runtime. **Local Ollama LLM remains a first-class option** — the admin can route Ask, Modify, or both through local Ollama with a single click. Useful for cost-free testing, provider outages, or local development against the production code path. The Claude/Groq split is a sensible *default* for the beta, not a hard binding. Ollama also stays on the VPS for embeddings regardless of which LLM provider is active.


---

## Pricing snapshot (2026-05)

List prices, USD per million tokens. Anthropic offers prompt caching (~90% discount on cached input — see "Anthropic prompt caching" below). Groq pricing is flat: no caching tier.

**Anthropic (Ask pipeline candidates)**

Anthropic publishes four rates per model — base input/output plus cache write (first call that primes the cache) and cache read (every subsequent call hitting that cache).

| Model | Input | Output | Cache write | Cache read | Use case |
|---|---|---|---|---|---|
| `claude-opus-4-7` | $5.00 | $25.00 | $6.25 | $0.50 | Highest quality. Only ~1.7× Sonnet's price — practical for high-stakes single queries, but still expensive at default scale. |
| `claude-sonnet-4-6` | $3.00 | $15.00 | $3.75 | $0.30 | **Ask default.** Balanced reasoning + cost. Cache reads at 10% of input rate make stable system prompts effectively free after the first call. |
| `claude-haiku-4-5` | $1.00 | $5.00 | $1.25 | $0.10 | Cheap fallback if Sonnet pressure becomes a concern mid-beta. |

> **Cost-tracking caveat (code-side).** `TrackedChatModel` currently logs cost using only the base input/output rates from `_PRICE_TABLE` in `src/core/llm.py` — it does not yet distinguish cache reads from regular input. Once prompt caching is wired on (see "Anthropic prompt caching" below), `LLMCallLog.estimated_cost_usd` will overstate the true Anthropic spend. The Anthropic console (`console.anthropic.com`) remains authoritative for billing; the in-app log is a directional figure.

**Groq (Modify pipeline candidates)**

| Model | Input | Output | Use case |
|---|---|---|---|
| `meta-llama/llama-4-scout-17b-16e-instruct` | $0.11 | $0.34 | **Modify default.** MoE — fast + cheap. Best $/quality on the Groq lineup as of 2026-05. |
| `llama-3.3-70b-versatile` | $0.59 | $0.79 | Dense 70B, well-tested historically — **but Groq throttles it to 100K TPD on the current plan, so it's not a viable default or fallback at scale.** Short diagnostic runs only. See "Groq rate-limit ceilings" below. |
| `qwen/qwen3-32b` | $0.29 | $0.59 | Best strict-JSON / tool-call adherence. Switch to this if structured-output errors spike. |
| `openai/gpt-oss-120b` | $0.15 | $0.60 | Quality reach for Tier 3 (RED) code generation. Slower than Scout, cheaper input than 70B. |

**Source of truth in code:** the prices above mirror `_PRICE_TABLE` in `src/core/llm.py`, used by `TrackedChatModel` to write `LLMCallLog.estimated_cost_usd` on every call. The admin dropdown is populated from `CLOUD_MODELS` in `src/core/llm_cloud_registry.py`. Update both files together when prices change.

**Where to verify current pricing:**

- Anthropic — `https://www.anthropic.com/pricing` (list prices) and `https://console.anthropic.com/` (your actual billing dashboard with real-time spend).
- Groq — `https://groq.com/pricing` (per-model breakdown, same page used to assemble the table above).

Provider list prices drift quarterly; before any planning exercise that depends on these numbers, re-check both pages.

**Implication:** with Scout as the Modify default, **Modify is now roughly 30–40× cheaper per token than Ask** (was 5× when Modify ran on Llama 3.3 70B). The split maps cleanly onto the use case — Modify makes many short structured calls; Ask makes fewer long context-heavy calls — and the larger ratio means the $100 Groq balance has dramatically more runway than the $50 Anthropic balance during the beta. If a budget imbalance shows up in the first week, the lever is to shift more pre-paid balance toward Anthropic, not toward Groq.

---

## Groq rate-limit ceilings

Pre-paid balance is one cost ceiling. **Per-org rate limits are the other one**, and on the current Groq plan they bind sooner than the dollar budget does. Snapshot from the Groq console (2026-05):

| Model | RPM | RPD | TPM | TPD |
|---|---:|---:|---:|---:|
| `meta-llama/llama-4-scout-17b-16e-instruct` (Modify default) | 30 | 1K | **30K** | **500K** |
| `qwen/qwen3-32b` | **60** | 1K | 6K | 500K |
| `openai/gpt-oss-120b` | 30 | 1K | 8K | 200K |
| `llama-3.3-70b-versatile` | 30 | 1K | 12K | **100K** |

Source: Groq dashboard → Organization Limits. The dashboard explicitly notes "On Developer plan, you get higher limits and can request additional limit increases."

**What these numbers actually constrain:**

- **Daily ceiling on Modify.** Scout's 500K TPD is the real Modify budget per day. Per-user 50K cap × ~10 concurrent power users would saturate it. Before that point the experience is fine; past it, the dispatcher will return `429 rate_limit_error`.
- **Burst ceiling on concurrent users.** A single Modify request burns 4–8 LLM calls back-to-back across the writeback pipeline (triage → slots → resolver → router → planner → guardian → reviewer). Scout's 30 RPM means at most ~4–7 concurrent Modify requests can complete per minute before requests start queueing. Qwen3 doubles the RPM (60) but at 5× lower TPM.
- **Llama 3.3 70B is not a viable fallback at this plan.** 100K TPD ≈ 2 average users at the per-user cap, or 1 power user. The admin dropdown labels it "low Groq TPD — short tests only" so the operator sees the constraint at flip time. If a real fallback for Scout is needed, the practical answer is one of: (a) request a TPD increase from Groq, (b) flip to Qwen3 (same 500K TPD), or (c) flip to local Ollama via `force_local_ollama` and accept the latency hit.

**When to act:**

- **Pre-launch (now):** none of these limits bind today — usage is operator-only. No action needed.
- **First week post-launch:** watch `LLMCallLog` daily. If saturated days approach 500K Scout TPD, request a TPD increase from Groq before Barcelona.
- **Barcelona launch event:** RPM is the more likely binding constraint during a live demo with concurrent attendees. Worth requesting an RPM bump from 30 → 100+ on Scout a week before the event.

These ceilings are not modelled in `LLMCallLog` or `UserTokenBudget`. They're a separate operational concern surfaced only by provider-side `429` responses.

---

## Total budget for the beta

| Source | Amount |
|---|---|
| Pre-paid Groq balance | $100 |
| Pre-paid Anthropic balance | $50 (extendable to $100 if depleted) |
| **Ceiling** | **$150** |

Pre-paying offloads the hard cost cap to the provider — when the balance hits zero, the API just refuses.
We don't have to build cost-cap enforcement; we just have to handle the error gracefully.

---

## Per-user daily cap

**Hard cap: 50,000 tokens per user per day.**

**Why a cap, and why this number:**

- A single Ask query can pull 100k+ tokens of IFC context into the prompt without a cap. One curious user could exhaust the entire month's Anthropic balance in an hour.
- 50k/day = roughly 5–10 typical Ask sessions OR ~20 Modify proposals. Plenty for genuine exploration; an obvious wall for runaway behaviour.
- For ~30 active users at full cap (`30 × 50k × 30 days = 45M tokens/month`), worst-case Anthropic spend is `~45M × $9/M (avg in/out)` ≈ $405. So in practice the cap is **defensive** — usage will be a fraction of this — but it stops one user trashing the month.

**Mechanism (`UserTokenBudget` model):**

| Field | Type | Purpose |
|---|---|---|
| `user` | `OneToOneField(User)` | One budget per user |
| `daily_cap` | `IntegerField(default=50000)` | Adjustable per user; admin can raise it for power users without rebuilding |
| `used_today` | `IntegerField(default=0)` | Running tally |
| `last_reset_at` | `DateTimeField` | Last UTC midnight crossed |
| `hard_blocked` | `BooleanField(default=False)` | Manual lock — admin sets this to freeze a user without changing the cap |

**Flow:**

1. **Pre-call:** estimate prompt size (`len(prompt) // 4` heuristic, fast). If `used_today + estimate > daily_cap`, refuse with a friendly toast: *"You've used X / Y tokens today. Cap resets at 00:00 UTC, or email admin to request more."*
2. **Make the call.**
3. **Post-call reconciliation:** read real `response.usage.input_tokens` and `response.usage.output_tokens`. Add to `used_today`. Log to `LLMCallLog`.
4. **Daily reset:** lazy — first call after midnight UTC zeroes `used_today` and updates `last_reset_at`. No cron required.

**UI surface (M2):** banner in `_ask.html` and `_modify.html`: *"X / Y tokens today"* with a "request more" mailto link. Token awareness is part of the user education here, not a hidden meter.

---

## Master kill-switch

**`LLM_MASTER_KILL=1` env var.**

When set:
- All cloud LLM calls return immediately with a `LLMMasterKillError`.
- The chat UI and modify UI render a banner: *"Castor is paused for maintenance — service will resume shortly."*
- Form submissions on the landing page still work (we don't lose applications during a pause).

This is the last-resort circuit-breaker. Triggers (manual decision):
- Anthropic or Groq pricing surprise / billing incident
- Detected abuse pattern (one user drilling through the cap on multiple accounts)
- Provider outage that's leaving users hanging on long timeouts — better a clean banner than dead requests

**Toggle:** edit env, `docker compose restart castor`. ~10 seconds to flip. ~10 seconds to flip back.

---

## What happens when a provider balance is exhausted

The pre-paid balance hits zero → provider returns a quota error (Anthropic: `429 rate_limit_error` with `type=usage_limit_exceeded`; Groq: `402 payment_required`).

**Handling (M2 work):**
- Catch provider-specific quota/auth exceptions in the dispatcher.
- Convert to a friendly toast: *"The shared LLM balance is out for today. Email admin if this is urgent."*
- Log to Sentry with high severity so the operator (you) sees it immediately.
- The site otherwise stays up — only LLM-dependent endpoints fail.

**Refill flow:** top up the balance via the provider dashboard, no code change needed.

---

## Anthropic prompt caching

Claude's prompt caching has two rates per model: a one-time **write** premium (~25% above input) and a per-call **read** discount (~10% of input). After the first call primes the cache, subsequent reads are an order of magnitude cheaper. Worth wiring on:
- The RAG system prompt (~stable text, several thousand tokens)
- The classifier rubric in writeback triage
- Any system prompt that's large and rarely changes

The `claude-api` skill in this project is built for exactly this — invoke it during M1 implementation.

**Rough impact on Sonnet (Ask default):**

- Base input rate: $3.00 / M.
- Cache read rate: $0.30 / M (= 10% of input).
- If 80% of every Ask call's input is a cached system prompt and 20% is per-query context, the blended input rate is `0.8 × $0.30 + 0.2 × $3.00 = $0.84 / M` — a ~70% reduction.
- At 90% cached (long stable system prompt + minimal per-query context), blended input ≈ `0.9 × $0.30 + 0.1 × $3.00 = $0.57 / M` — a ~80% reduction.

The cache **write** call costs 25% more than a plain input call ($3.75 / M for Sonnet), so caching is net-positive after roughly the second hit on the same prompt. Anything reused across users or sessions wins easily.

That's the difference between a $150 budget lasting 2 weeks and lasting 6+ weeks.

---

## BYOK (Bring Your Own Key) — shipped 2026-06

Power users plug their own Anthropic / Groq API keys via Settings; their calls bypass the daily cap and the shared $150 balance entirely. The shared pool funds the friction-free default; BYOK is the escape hatch for users who want more or who don't want their usage gated.

**Shipped design:**
- Storage: encrypted Fernet ciphertext on `UserLLMConfig` (`anthropic_api_key_ciphertext`, `groq_api_key_ciphertext`). Keyed by `FIELD_ENCRYPTION_KEY` env var. Plaintext access via property getters that route through `core.crypto` — never assigned to the instance.
- Scope: per-purpose. `ask_provider_override` and `modify_provider_override` independently route Ask and Modify, mirroring the site-level Anthropic/Groq split.
- Model selection: curated 3-option dropdown per provider (see `core/llm_catalog.py`). Every option is keyed into the `_PRICE_TABLE` in `core/llm.py` so cost tracking stays accurate.
- Failure handling: 401/429 surface as a toast pointing back to Settings. **No silent fallback to the shared pool** — that would defeat the user's choice and risk unexpected $ from the $150 if a key goes bad. Spec at `docs/specs/llm-connection.md`.
- Audit: `LLMCallLog.byok = True` for every BYOK call. Cost dashboards exclude these from managed-pool spend. The daily-cap counter (`UserTokenBudget.used_today`) is never incremented for BYOK calls.
- Key removal atomically resets any provider override that pointed at the removed key (no `override=anthropic, key=blank` half-state).

**What's deferred to v1.1:**
- "Verify key" button that pings the provider on save instead of waiting for the first real Ask to discover a typo.
- Key rotation management command (manual SQL acceptable for the < 100 beta users until then).
- OpenAI / other providers — Anthropic + Groq cover the cheap-fast and frontier-reasoning paths.

---

## What we're NOT building (and why)

- **Per-call cost meter in the UI.** Token counter is enough; turning every call into "$0.0042" makes the UI noisy and creates a paywall vibe inappropriate for a free beta.
- **Tiered caps (free / pro / enterprise).** No tiers. Everyone gets 50k/day. Power users get BYOK in v1.1.
- **Streaming usage (real-time token countdown).** Nice but unnecessary. Banner refreshes on each call.
- **Stripe / billing integration.** Nothing to charge for during the beta.

These are all explicit non-goals for May 31. Some may move into the post-launch backlog after Barcelona feedback.
