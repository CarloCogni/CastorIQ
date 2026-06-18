# BYOK — Bring Your Own LLM Key

Castor is **local-first by default** — Ollama running on your machine handles both Ask and Modify. If you want to use a cloud provider for speed or quality, **BYOK** lets you supply your own Anthropic or Groq API key, encrypted at rest, scoped to your user.

The deeper architecture rationale lives in [specs/llm-connection.md](specs/llm-connection.md). This page is what a user needs to turn it on.

## Supported providers

| Provider | Best for | Get a key |
|---|---|---|
| **Anthropic** (Claude) | Higher-quality Ask / Modify reasoning | `console.anthropic.com` |
| **Groq** | Fast, low-latency Modify pipeline runs | `console.groq.com` |

Castor ships a curated short-list of models per provider in the Settings dropdown. The list is intentionally small — picking the wrong model is a common foot-gun, and the curated set is the one Castor actively tests against.

## Turn it on

1. Open **Settings → LLM Configuration → BYOK**.
2. Paste your provider key. It is validated server-side, then Fernet-encrypted and stored on your `UserLLMConfig` row. The plaintext is never persisted; only a hint (last 4 characters) is kept so you can confirm which key is there.
3. Pick a curated model for that provider. The defaults are a sensible starting point.
4. Set the **per-purpose override**:
   - **Ask** — which provider runs the RAG response stage
   - **Modify** — which provider runs the V2 writeback pipeline stages
   You can mix and match: Anthropic for Ask quality, Groq for Modify speed, or anything else.

The provider badge on each LLM response shows which key actually answered, so you can confirm routing at a glance.

## What happens if a key is invalid

Castor **hard-errors** on auth failure — there is no silent fallback to the site's managed pool. The reason: silent fallback would mean a quiet, invisible cost transfer (your tokens stop being used; the operator's start being used). You see an explicit toast — `Anthropic key rejected (401)` — and you fix the key.

Rate-limit responses (`429`) surface the same way, as `BYOKRateLimitError`.

## Removing a key

**Settings → LLM Configuration → BYOK → Remove**. Wipes the ciphertext. Your per-purpose overrides revert to the site default automatically.

## For operators / self-hosters

BYOK requires `FIELD_ENCRYPTION_KEY` in your `.env` before serving any traffic — `core/crypto.py` refuses to start the encrypted-fields path without it in production. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Persist that value across restarts. Rotating it without re-encrypting existing ciphertexts will lock every user out of their saved keys — there is no plaintext copy to fall back on.

## Reference

- API endpoints: `core/views_byok.py`
- Validation + encryption: `core/services/byok_service.py`, `core/crypto.py`
- Model catalogue: `core/llm_catalog.py`
- Resolution order (kill switch → site config → user override → BYOK): `core/llm.py`, `_resolve_llm_choice()`
- Architecture rationale (tiered Ollama-local / BYOK / managed): [specs/llm-connection.md](specs/llm-connection.md)
