# core/management/commands/smoke_llm_providers.py
"""
Manual smoke harness for the multi-provider LLM dispatcher.

Usage:
    cd src && uv run python manage.py smoke_llm_providers --provider ollama
    cd src && uv run python manage.py smoke_llm_providers --provider anthropic
    cd src && uv run python manage.py smoke_llm_providers --provider groq
    cd src && uv run python manage.py smoke_llm_providers --provider all

For each provider the command runs one representative Ask call and one
representative Modify (JSON intent) call, prints the latency and the first
chars of the reply, and restores the original SiteLLMConfig at the end.
Not part of CI — provider keys, network, and quotas make this a manual check.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from langchain_core.messages import HumanMessage

from core.llm import (
    LLMConfigurationError,
    LLMMasterKillError,
    cached_system,
    get_llm,
)
from core.models import SiteLLMConfig

ASK_SYSTEM = "You are a helpful assistant. Reply concisely."
ASK_HUMAN = "In one sentence, what is an IFC file?"

MODIFY_SYSTEM = (
    "You classify user requests. Output ONLY valid JSON with fields "
    "'intent' (string) and 'confidence' (number 0..1)."
)
MODIFY_HUMAN = "Classify: 'set the fire rating of the main door to 60 minutes'."


class Command(BaseCommand):
    help = "Run a representative Ask + Modify call against one (or all) LLM providers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            choices=["ollama", "anthropic", "groq", "all"],
            default="all",
            help="Provider to smoke-test. 'all' runs each in turn.",
        )

    def handle(self, *args, **options):
        provider_arg: str = options["provider"]
        targets = ["ollama", "anthropic", "groq"] if provider_arg == "all" else [provider_arg]

        cfg = SiteLLMConfig.load()
        original = {
            "ask_provider": cfg.ask_provider,
            "modify_provider": cfg.modify_provider,
            "force_local_ollama": cfg.force_local_ollama,
        }
        # Disable the emergency override for the duration of the test — otherwise
        # cloud calls would always be redirected to local Ollama and the test
        # would silently lie.
        cfg.force_local_ollama = False

        any_failure = False
        try:
            for provider in targets:
                cfg.ask_provider = provider
                cfg.modify_provider = provider
                cfg.save()
                self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {provider.upper()} ==="))
                ok_ask = self._run_ask(provider)
                ok_mod = self._run_modify(provider)
                if not (ok_ask and ok_mod):
                    any_failure = True
        finally:
            for k, v in original.items():
                setattr(cfg, k, v)
            cfg.save()
            self.stdout.write("\nSiteLLMConfig restored to original state.")

        if any_failure:
            raise CommandError("One or more providers failed the smoke test.")
        self.stdout.write(self.style.SUCCESS("\nAll smoke tests passed."))

    def _run_ask(self, provider: str) -> bool:
        try:
            llm = get_llm(purpose="ask", temperature=0.2)
            messages = [cached_system(llm, ASK_SYSTEM), HumanMessage(content=ASK_HUMAN)]
            t0 = time.perf_counter()
            response = llm.invoke(messages)
            elapsed = time.perf_counter() - t0
            content = self._extract_text(response)
            self.stdout.write(
                f"  [Ask    ] OK   {elapsed:5.2f}s  {content[:120]!r}".replace("→", "->")
            )
            return True
        except (LLMConfigurationError, LLMMasterKillError) as e:
            self.stdout.write(self.style.WARNING(f"  [Ask    ] SKIP      {e}"))
            return False
        except Exception as e:  # provider/network/quota
            self.stdout.write(self.style.ERROR(f"  [Ask    ] FAIL      {type(e).__name__}: {e}"))
            return False

    def _run_modify(self, provider: str) -> bool:
        try:
            llm = get_llm(purpose="modify", temperature=0.0, format_json=True)
            messages = [cached_system(llm, MODIFY_SYSTEM), HumanMessage(content=MODIFY_HUMAN)]
            t0 = time.perf_counter()
            response = llm.invoke(messages)
            elapsed = time.perf_counter() - t0
            content = self._extract_text(response)
            self.stdout.write(
                f"  [Modify ] OK   {elapsed:5.2f}s  {content[:120]!r}".replace("→", "->")
            )
            return True
        except (LLMConfigurationError, LLMMasterKillError) as e:
            self.stdout.write(self.style.WARNING(f"  [Modify ] SKIP      {e}"))
            return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  [Modify ] FAIL      {type(e).__name__}: {e}"))
            return False

    @staticmethod
    def _extract_text(response) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, list):
            # Anthropic-style content blocks
            parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            return " ".join(parts)
        return str(content)
