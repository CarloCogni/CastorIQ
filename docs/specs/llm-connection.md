# Castor — LLM Connection Strategy

**Status:** Decision memo. Deferred — roadmap input, not a build ticket.
**Author prompt:** "Platform online for FREE, but local LLM on user PC connecting through browser?"
**Scope:** How Castor connects to LLM inference when served as an online platform, given the current all-local Ollama architecture and the business constraint of keeping hosting costs sustainable.

---

## 1. Context

Castor today runs entirely local: Ollama on the same machine as Django, models pulled once, zero cloud dependency. CLAUDE.md §"Key Design Decisions" #1 states this explicitly — **"Local-first: all LLM inference via Ollama. No cloud API calls."** That decision was made when Castor was a single-user desktop tool. It is now being stress-tested by a different question: *can Castor be offered as an online platform without Castor paying for every user's tokens?*

Three forces collide:

- **Unit economics.** A free-tier online platform that runs inference on rented GPUs burns capital per user. Even at Groq prices (~$0.20/Mtok input, ~$0.20/Mtok output for Llama 3.1 70B at the time of writing), RAG over a moderately sized document set plus a chat turn easily reaches 10–30 Ktok per user interaction. Ten thousand free users at thirty interactions a day is not a small bill.
- **Privacy posture.** AEC firms routinely operate under NDAs on project data. Floor plans, structural layouts, tenant rosters, fire-safety psets — this is the material inside an IFC file. Shipping it to a cloud LLM API is a legal question before it is a technical one. Many firms will simply not permit it.
- **Hardware reality of the target user.** A BIM coordinator on a Revit workstation has 32–64 GB RAM and an RTX 4070+. A site PM on a mid-range laptop has integrated graphics. A facility manager on an iPad has nothing. Assuming "construction users have good GPUs" is wrong as soon as you move beyond the BIM specialist persona.

This document names the options, pressure-tests the assumptions under each, and lands on a recommendation. It does **not** authorize any code change. It exists so the question does not have to be re-derived from scratch when the platform go-to-market forces a decision.

---

## 2. Three options on the table

### 2.1 Browser → user's local Ollama

The user installs Ollama on their own machine, pulls the required models, and the Castor web app (served from `https://castor.domain`) issues LLM requests directly to `http://localhost:11434` from the browser. Inference runs on the user's GPU. Castor pays nothing per token.

**Preconditions:**
- Ollama installed and running as a background service on the user's machine.
- Models pulled (e.g. `ollama pull llama3.1:8b`).
- `OLLAMA_ORIGINS=https://castor.domain` set on the user's machine to enable CORS.
- User's browser respects the "localhost is a secure context" exception (Chrome and Firefox do; Safari is stricter; some corporate proxies interpose and break this).

**What it buys:** zero per-token cost to Castor, full data sovereignty for the user, preserves CLAUDE.md §1.

**What it costs:** install friction that will lose the majority of non-technical signups. "Pull a 4–40 GB model, keep a background service running, paste an env var" is a conversion funnel killer.

### 2.2 Browser → WebLLM / MLC-LLM (in-browser via WebGPU)

Model weights are downloaded to the browser and inference runs *inside the browser process* via WebGPU. No install. Projects like MLC-LLM, WebLLM, and Transformers.js (ONNX) all ship this today.

**What it buys:** genuinely zero-install LLM. The user opens Castor, waits for a one-time 2–4 GB model download, and inference runs on their GPU/CPU from then on.

**What it costs:** model size ceiling. Practical browser-deployable models today are 3–8B parameters quantized to 4 bits. That is fine for intent classification and light Q&A but visibly weaker than Llama 70B or Claude for the Tier 3 code-generation path in writeback. Also: first-load UX is a cliff (2+ GB download blocks first use), and browser GPU memory is fragile — tab backgrounding can kill the runtime.

### 2.3 BYOK cloud (Claude / Groq / OpenAI)

The user pastes their own API key for a cloud provider into Castor settings. Castor proxies requests through its backend (or calls direct from browser) using the user's key. User pays the cloud provider directly; Castor pays nothing for tokens.

**What it buys:** zero install, best-in-class model quality (Claude Opus, GPT-4-class), instant onboarding.

**What it costs:** direct violation of CLAUDE.md §1 as written. IFC content leaves the user's machine. Depending on the firm's NDA and the cloud provider's data-retention policy, this may be a non-starter for the exact users Castor is aimed at.

---

## 3. Five assumptions to pressure-test before committing

Per CLAUDE.md "Review Stance" — assume-failure lens first, then proceed.

### 3.1 "LLM is the dominant cost of running Castor" — false

The LLM is the most *visible* line item, not the largest one. Castor also needs:

- **Postgres 16 + pgvector** with a 1024-dim embedding index per project. Index memory scales with entity count; a large IFC can produce 100K+ embedded chunks.
- **Object storage** for IFC files (100 MB – 2 GB each), attached documents (O&M manuals, permits, photos), and the per-project Git repositories that track IFC history.
- **Django ASGI + Daphne** for the WebSocket pipeline (see `writeback/consumers.py`, `chat/consumers.py`).
- **Embedding inference** at ingest — currently Ollama's embedding model, which is also GPU work. If LLM moves to the user but embeddings stay on the server, the server still needs GPU.

Offloading the LLM does not make Castor free to host. It shifts the largest line item; it does not eliminate the bill. Any "free platform" plan needs a separate cost model for storage and database before the LLM question is even interesting.

### 3.2 "Construction users have good GPUs" — partially true, collapses outside BIM specialists

| Persona | Typical hardware | Can run 8B local? | Can run 70B local? |
|---|---|---|---|
| BIM coordinator / modeler | RTX 4070+ workstation, 32–64 GB RAM | Yes, comfortably | Borderline (quantized) |
| Structural / MEP engineer | Similar to BIM coordinator | Yes | Borderline |
| Site project manager | Mid-range laptop, integrated graphics | CPU-only, slow | No |
| Facility manager (post-handover) | Office laptop or tablet | No | No |
| Contractor / subcontractor | Varies wildly; often tablet or phone | No | No |
| Tenant / occupant | Phone | No | No |

Castor's current Ask/Modify users are BIM-adjacent (row 1–2). The 7D FM vision (`docs/specs/7D-facility-management.md`) explicitly targets rows 3–7. The "everyone has a GPU" argument is a design trap: it works for today's user and fails the moment Castor expands to its stated roadmap.

### 3.3 "Offloading LLM solves it" — embeddings stay, hybrid complexity grows

RAG has two inference legs: **embedding at ingest/query time** and **generation at response time**. If LLM generation moves to the user but embeddings stay on the server, the server still needs GPU provisioning. If both move to the user, document ingest becomes slow and blocking on the user's machine — which is acceptable for a power user but miserable for someone uploading a 500-page O&M manual on a laptop.

The honest partition is:

| Operation | Where it can reasonably run |
|---|---|
| LLM generation (Ask response, intent classification, writeback code gen) | User's machine OR cloud OR server |
| Query-time embedding (fast, single vector) | User's machine OR server |
| Ingest embedding (bulk, per-chunk) | Realistically: server, at least for V1 |
| Vector search (pgvector ANN) | Server (the index lives there) |

This means "offload the LLM to the user" is one toggle, not a strategy. The full picture has at least three knobs.

### 3.4 "BYOK is a clean escape hatch" — legal question before technical

The content Castor sends to an LLM in Ask mode includes retrieved IFC entity descriptions and document chunks. In Modify mode (Tier 3), it includes executable IfcOpenShell code generated against real building data. In both modes, the LLM prompt contains building-identifying information.

Standard AEC engagement contracts restrict this data to the named recipients. Cloud LLM providers typically disclaim retention for API calls, but "we don't store it" is not the same as "the client's contract permits it to touch your infrastructure." This is a question for each firm's legal review, not a technical toggle.

**Consequence:** BYOK cannot be the default path without reversing CLAUDE.md §1 deliberately, with eyes open. It is a *user choice*, never a *Castor default*.

### 3.5 "HTTPS → localhost works everywhere" — browsers yes, corporate networks not always

Chrome and Firefox treat `http://localhost` (and `127.0.0.1`) as secure contexts and permit them from HTTPS origins (per Secure Contexts spec). This is the happy path.

Failure modes observed in practice:

- **Corporate MITM proxies** (Zscaler, Netskope, Palo Alto) intercept HTTPS and rewrite. Some block or break the localhost exception.
- **Safari** on macOS is stricter than Chrome/Firefox and has historically blocked mixed-content edge cases that Chrome permits.
- **Windows firewall profiles** in "Public network" mode block Ollama's listening port from non-self origins.
- **Mobile browsers** do not expose a way to run a local Ollama — the entire Tier A approach is desktop-only.

None of these are blockers; all of them are support-ticket generators that have to be budgeted for.

---

## 4. Option comparison

| Dimension | Local Ollama (BYO) | WebLLM in browser | BYOK cloud | Castor-hosted cloud |
|---|---|---|---|---|
| Cost to Castor per token | 0 | 0 | 0 | Variable, significant |
| Cost to user per token | 0 (owns hardware) | 0 (owns hardware) | Pays provider | Pays subscription |
| Install friction | High | Low (first-load delay) | Low | None |
| Privacy / NDA posture | Best | Best | Worst | Depends on hosting |
| Model quality ceiling | High (70B possible on workstations) | Medium (≤8B practical) | Highest (frontier models) | Medium-high (bounded by Castor's GPU budget) |
| Works on mobile | No | Partial (iPad/iPhone WebGPU limited) | Yes | Yes |
| Respects CLAUDE.md §1 | Yes | Yes (arguably) | No | No |
| Enterprise viability | High | Medium | Depends on NDAs | Depends on hosting region + certs |
| Failure modes | CORS, firewall, install abandonment | Browser GPU / memory, cold start | Key leakage, provider outage, cost spike | Castor's infra bill |

No single column wins. This is the signal that pushes toward a tiered offering.

---

## 5. Recommendation — tiered, Ollama-local default

**Keep CLAUDE.md §1 as the default stance.** Add tiers on top of it, not instead of it.

### Tier A — Local Ollama (default, recommended)

User points Castor at their own Ollama endpoint. Default is `http://localhost:11434`; power users can set it to a Ollama-compatible server on their LAN (e.g. a dedicated inference box). Setup help is a 3-step guide with copy-paste commands for Windows/macOS/Linux. Models auto-detected via `/api/tags`.

This is the path for AEC professionals with serious hardware and privacy-sensitive projects. It costs Castor nothing and upholds the local-first principle.

### Tier B — BYOK (convenience, opt-in)

User adds a provider key (Groq first — cheapest and fastest; Anthropic second — highest quality) in settings. Key is stored **encrypted at rest** (Django `cryptography`-based field), scoped per-user, never logged, never sent to anyone but the named provider. The UI makes clear when a call is about to leave the user's machine ("This call will be sent to Groq. Cost estimate: $0.004.") — every single time, not once at onboarding.

This serves users who want frictionless onboarding and have verified that their project NDAs permit cloud LLM use. It is a user-initiated deviation from local-first, not a Castor default.

### Tier C — Castor-managed (future, paid)

If and only if Castor reaches a scale where a managed tier makes business sense, Castor hosts a small-GPU inference pool running an open model (e.g. Llama 3.1 70B on a cloud GPU or on-prem depending on residency needs) and bills it through a subscription. This is a business decision, not a technical one — defer until product-market fit is established.

### Per-call policy

The model selection per call should consider:

1. **Privacy sensitivity of the payload** (writeback Tier 3 code gen against entity data = high; intent classification = lower).
2. **User preference** (explicit per-project choice of which tier to use).
3. **Availability** (local Ollama unreachable → fall back to configured Tier B if the user has opted in; otherwise surface a clear error — never silently escalate to cloud).

The "never silently escalate" rule is non-negotiable. A user who configured local Ollama must never have data leak to the cloud because their Ollama was offline. Fallback happens only with explicit prior consent.

---

## 6. Implementation implications (non-binding sketch)

This section is a forward-looking sketch of what the code change would look like, **not** an authorized refactor. It exists so the decision in §5 is grounded in real extension points.

### Extension point: `core/llm_model_registry.py`

The registry (confirmed at `src/core/llm_model_registry.py`) already centralizes model metadata. It would gain a `provider` field per entry:

- `ollama_local` — user's machine via browser-to-localhost
- `ollama_remote` — user's LAN Ollama or self-hosted server
- `anthropic` — BYOK via Anthropic API
- `groq` — BYOK via Groq
- `openai` — BYOK via OpenAI (optional)
- `castor_managed` — Tier C, future

### Service layer dispatch

CLAUDE.md §"Django Patterns" mandates business logic in services. A new `core/services/llm_client.py` (or extension of an existing abstraction) dispatches by provider. Writeback and RAG service entry points do not change — the change is upstream of them. This preserves the existing interface (`rag_service.py`, `modification_service.py`) and localizes the provider logic.

### Configuration surface

- Per-user encrypted credential store for Tier B keys.
- Per-project preferred provider (some projects' NDAs permit cloud, some don't — the project, not the user, should carry this flag).
- UI element visible in the chat/modify panels showing "LLM: local llama3.1:8b" or "LLM: Groq Llama 3.1 70B (cloud)" on every response.

### CORS and deployment

For Tier A to work from an HTTPS origin, the user's Ollama must have `OLLAMA_ORIGINS=https://castor.domain` set. Castor's onboarding flow needs to detect a missing CORS header and show a specific remediation step, not a generic "failed to connect."

### Embeddings stay server-side for V1

Moving embedding inference to the user is a separate decision and is out of scope for this document. Assume embeddings run on Castor infrastructure regardless of which LLM tier the user picks.

### Desktop shell — a cleaner alternative to browser-to-localhost

If Castor ships a Tauri or Electron desktop shell, the localhost-from-HTTPS question disappears: the app *is* local, it can talk to Ollama without CORS, and mobile is a separate (web) codepath. This is a strategic question worth naming but not resolving here.

---

## 7. Open questions for future decision

1. **Desktop shell vs browser-only.** Does Castor ship a Tauri/Electron shell to eliminate localhost-CORS friction? Pros: smoother Tier A, offline-capable, no mixed-content concerns. Cons: two codepaths to maintain, app-store distribution overhead.
2. **BYOK proxying.** Do we proxy BYOK calls through the Django backend (we terminate TLS, we see payload, we can enforce rate limits — but we become a data-handler for the provider's content) or make them direct browser → provider (simpler, user's browser holds the key)? Both have legal implications.
3. **Project-scoped privacy flag.** Should every project carry a `allow_cloud_llm: bool` that hard-gates Tier B/C regardless of user preference? This gives project owners a single lever to enforce client NDAs across all users on the project.
4. **Tier C abuse model.** If and when Castor offers managed inference, how is abuse (prompt-injection-driven token burn, runaway context) bounded?
5. **Embeddings strategy.** Ingest embeddings are slow on mid-tier user hardware. Do we ever offer "bring your own embedding endpoint" as an advanced setting, or does this stay server-side indefinitely?

---

## 8. Decision log

**2026-04-17 — Deferred.** Current stance: Ollama-local remains Castor's default per CLAUDE.md §"Key Design Decisions" #1. Browser-to-localhost LLM is viable and aligns with the local-first principle. WebLLM is noted but not pursued in V1 (model-quality ceiling too low for Tier 3 writeback). BYOK and Castor-managed tiers are roadmap items contingent on platform go-to-market strategy and privacy-policy drafting. Any move to implement Tier B must be preceded by a CLAUDE.md update — the principle is not to be diluted silently.

**Next revisit trigger:** when a platform-hosting decision is made, or when user demand for cloud LLM becomes load-bearing on adoption.
