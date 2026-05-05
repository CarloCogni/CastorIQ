# Token Economics — Castor Beta

> Cost model and the rules that keep $150 of LLM credit alive for a month.
> Strategy context: `public-beta-barcelona-2026.md`. Implementation milestone: `live-roadmap.md` M2.

---

## Provider mix

| Endpoint | Default provider | Default model | Why |
|---|---|---|---|
| Ask (`chat/_ask.html`) | Anthropic | `claude-sonnet-4-6` | High-quality reasoning over RAG context (IFC entities + documents). Citations matter; correctness > latency. |
| Modify (`writeback/`) | Groq | `llama-3.3-70b-versatile` | Faster, cheaper, more capable than what we test on locally. Modify pipeline already involves multiple LLM calls (triage → planner → hint generator); latency adds up. |
| Embeddings | Ollama on VPS | `mxbai-embed-large` (1024-dim) | Zero per-call cost, EU-resident, fits existing pgvector schema. CPU-only at launch. Voyage AI documented as escalation in `vps-deployment.md`. |

**Defaults, not contracts.** The Ask/Modify bindings above live in the `SiteLLMConfig` admin singleton (M1) and are flippable at runtime. **Local Ollama LLM remains a first-class option** — the admin can route Ask, Modify, or both through local Ollama with a single click. Useful for cost-free testing, provider outages, or local development against the production code path. The Claude/Groq split is a sensible *default* for the beta, not a hard binding. Ollama also stays on the VPS for embeddings regardless of which LLM provider is active.


---

## Pricing snapshot (2026-05)

| Provider | Model | Input ($/M tok) | Output ($/M tok) | Notes |
|---|---|---|---|---|
| Anthropic | `claude-sonnet-4-6` | $3.00 | $15.00 | Prompt caching available — 90% discount on cached input. Worth wiring on stable system prompts. |
| Groq | `llama-3.3-70b-versatile` | $0.59 | $0.79 | Output cost is roughly the same as input — uniquely flat pricing among hosted LLMs. |

**Implication:** Modify is roughly 5× cheaper per token than Ask (Groq vs Claude). That maps cleanly onto the use-case: 
   Modify makes many short structured calls; 
   Ask makes fewer long context-heavy calls.

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

Claude's prompt caching gives a ~90% discount on cached input tokens. Worth wiring on:
- The RAG system prompt (~stable text, several thousand tokens)
- The classifier rubric in writeback triage
- Any system prompt that's large and rarely changes

The `claude-api` skill in this project is built for exactly this — invoke it during M1 implementation.

Rough impact: if 80% of Ask input is cached system prompt, effective Ask input cost drops from $3/M to ~$0.6/M. That's the difference between a $150 budget lasting 2 weeks and lasting 6+ weeks.

---

## Post-launch: BYOK (deferred to v1.1)

Power users can plug their own Anthropic / Groq API keys via the settings page; their calls bypass the daily cap and the shared balance entirely.

**Why deferred:**
- Adds an encrypted credential model (`LLMProviderCredential` with cryptography-backed field), settings UI, key validation, key rotation. ~3–4 days.
- Risks the May 31 deadline.
- The 50k/day cap is enough for genuine exploration during the beta; power users wait two weeks for v1.1.

**v1.1 design notes (when we build it):**
- One credential per (user, provider) pair.
- Encrypted at rest using a Django key from env var; decrypted only at call time.
- Validation: try a `tokens=1` ping against the provider on save; reject invalid keys.
- UI: per-provider "Use my own key" toggle in user settings; when on, that provider's calls go through the user's key and don't count against `used_today`.
- Audit: `LLMCallLog` records `used_byok=True` so we can distinguish for analytics.

---

## What we're NOT building (and why)

- **Per-call cost meter in the UI.** Token counter is enough; turning every call into "$0.0042" makes the UI noisy and creates a paywall vibe inappropriate for a free beta.
- **Tiered caps (free / pro / enterprise).** No tiers. Everyone gets 50k/day. Power users get BYOK in v1.1.
- **Streaming usage (real-time token countdown).** Nice but unnecessary. Banner refreshes on each call.
- **Stripe / billing integration.** Nothing to charge for during the beta.

These are all explicit non-goals for May 31. Some may move into the post-launch backlog after Barcelona feedback.
