"""LLM provider seam: one Protocol, three implementations.

- MockProvider  — deterministic, offline. Its output is keyed off the prompt
  name/content so tests are stable and the eval gate is reproducible.
- AnthropicProvider — real path. Imports the `anthropic` SDK lazily; selected
  when AGENT_PROVIDER=anthropic AND ANTHROPIC_API_KEY is set.
- OpenAIProvider — real path. Imports the `openai` SDK lazily; selected when
  AGENT_PROVIDER=openai AND OPENAI_API_KEY is set.

Both real providers are swappable behind the same Protocol, which is the point:
the platform is provider-agnostic. `get_provider()` reads config and returns the
right one. Nothing here calls a paid API unless the matching key is present.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, *, prompt_name: str, rendered_prompt: str, context: str) -> str:
        """Return the model's text response for a rendered prompt."""
        ...


def _stable_score(text: str) -> float:
    """A deterministic pseudo-score in [0,1] derived from text (no randomness)."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


class MockProvider:
    """Deterministic stand-in. Structured, prompt-keyed responses.

    The point is not to *simulate* a model well; it is to make the orchestration,
    guardrails, evals, and audit trail fully testable offline. Output shape
    matches what the graph parses for each step.
    """

    name = "mock"

    def __init__(self, model: str = "mock-deterministic-v1") -> None:
        self.model = model

    def complete(self, *, prompt_name: str, rendered_prompt: str, context: str) -> str:
        base = prompt_name.split(".")[0]
        if base == "plan":
            return json.dumps(
                {
                    "steps": [
                        "Identify the engineering standards relevant to the change",
                        "Assess the change against those standards",
                        "Flag risks and required follow-ups",
                    ]
                }
            )
        if base == "draft":
            # Pull a couple of cited source ids out of the provided context so
            # the draft's provenance is real (the retriever put them there).
            sources = re.findall(r"\[source:([^\]]+)\]", context)
            # A "regressed" prompt (see evals) omits the standards instruction;
            # detect that so the mock produces a visibly weaker draft and the
            # eval gate can catch the regression.
            weak = "STANDARDS" not in rendered_prompt.upper()
            if weak:
                return json.dumps(
                    {
                        "summary": "Looks fine to me.",
                        "findings": [],
                        "risk": "low",
                        "cited_sources": [],
                    }
                )
            return json.dumps(
                {
                    "summary": (
                        "The change is assessed against the retrieved standards. "
                        "It is broadly compliant but has gaps that need follow-up "
                        "before merge."
                    ),
                    "findings": [
                        "Input validation is not covered by a test (see SEC-STD).",
                        "No rollback note for the migration (see REL-STD).",
                        "Logging omits a correlation id (see OBS-STD).",
                    ],
                    "risk": "medium",
                    "cited_sources": sources[:3],
                }
            )
        if base == "critique":
            # Judge the draft that is embedded in the context. A strong draft
            # (has findings + citations) passes; a weak one fails. Parse the
            # draft JSON so the verdict is robust to serialization spacing.
            try:
                draft = json.loads(context)
            except (json.JSONDecodeError, TypeError):
                draft = {}
            has_findings = len(draft.get("findings") or []) > 0
            has_citations = len(draft.get("cited_sources") or []) > 0
            passed = has_findings and has_citations
            score = 0.9 if passed else 0.3
            return json.dumps(
                {
                    "passed": passed,
                    "score": score,
                    "rubric": {
                        "cites_sources": has_citations,
                        "actionable_findings": has_findings,
                        "states_risk": True,
                    },
                    "rationale": (
                        "Draft cites standards and lists actionable findings."
                        if passed
                        else "Draft is vague and cites no standards."
                    ),
                }
            )
        # Fallback: deterministic echo with a stable score, never crashes.
        return json.dumps({"text": "ok", "score": _stable_score(rendered_prompt)})


class AnthropicProvider:
    """Real Anthropic path. Only importable/usable when a key is configured.

    Imported lazily so the package need not be installed for offline use.
    """

    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        try:
            import anthropic  # noqa: F401  (lazy: only when actually selected)
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "AGENT_PROVIDER=anthropic but the 'anthropic' package is not "
                "installed. `pip install anthropic`."
            ) from exc
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self.model = model

    def complete(self, *, prompt_name: str, rendered_prompt: str, context: str) -> str:  # pragma: no cover - needs a live key
        # Ask for raw JSON via the system prompt. (Assistant prefill, the usual
        # JSON-forcing trick, is rejected by newer models like Opus 4.x, so we
        # rely on the instruction plus the tolerant parser in graph._parse_json,
        # which strips any stray fences/prose.)
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=(
                "You are a rigorous engineering-standards reviewer. Respond with "
                "ONLY strict JSON matching the requested schema — no markdown, no "
                "code fences, no prose before or after the JSON object."
            ),
            messages=[{"role": "user", "content": f"{rendered_prompt}\n\n{context}"}],
        )
        return "".join(block.text for block in msg.content if block.type == "text")


class OpenAIProvider:
    """Real OpenAI path. Only importable/usable when a key is configured.

    Imported lazily so the package need not be installed for offline use. Uses
    the Chat Completions API with JSON-object response formatting so the graph's
    strict-JSON parsing behaves the same as on the other providers.
    """

    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        try:
            import openai  # noqa: F401  (lazy: only when actually selected)
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "AGENT_PROVIDER=openai but the 'openai' package is not "
                "installed. `pip install openai`."
            ) from exc
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model

    def complete(self, *, prompt_name: str, rendered_prompt: str, context: str) -> str:  # pragma: no cover - needs a live key
        resp = self._client.chat.completions.create(
            model=self.model,
            max_completion_tokens=1024,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a rigorous engineering-standards reviewer. "
                        "Respond with strict JSON matching the requested schema."
                    ),
                },
                {"role": "user", "content": f"{rendered_prompt}\n\n{context}"},
            ],
        )
        return resp.choices[0].message.content or ""


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Factory: return the provider selected by config.

    Defaults to MockProvider. Only returns a real provider when explicitly
    requested AND its key is present — so a stray AGENT_PROVIDER=anthropic|openai
    without the matching key falls back to mock rather than crashing an offline
    demo.
    """
    settings = settings or get_settings()
    provider = settings.agent_provider
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, settings.model_for("anthropic"))
    if provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(settings.openai_api_key, settings.model_for("openai"))
    return MockProvider()
