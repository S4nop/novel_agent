"""Provider adapter — the ONLY module that talks to the model (DESIGN §5).

Single model for every LLM role: Gemini 3.6 Flash. Keeping this behind a thin
adapter is what makes the model swappable without touching any node.

Deliberately NOT a LangChain wrapper: nodes call `f(inputs) -> validated output`
so the deterministic core stays testable (tests inject a fake at this seam).

WHY THE NATIVE SDK, NOT THE OpenAI-COMPAT ENDPOINT (verified 2026-07-29):
the OpenAI-compatible endpoint REJECTS safety settings —
`400 Invalid JSON payload received. Unknown name "safety_settings"`.
Safety-setting control is load-bearing here: the genre needs 사이다 revenge,
villain POV and graphic violence without sanitizing, so we use google-genai,
where safetySettings/responseSchema/countTokens are first-class.

Content policy (DESIGN §5): HARASSMENT / HATE_SPEECH / DANGEROUS_CONTENT are
relaxed for dark fiction. SEXUALLY_EXPLICIT is NEVER relaxed — explicit content
is out of scope by Google policy and relaxing it risks account suspension.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

# Default per-1M-token USD prices (gemini-3.6-flash). Override via Usage(...)
# or the NOVEL_PRICE_* env vars so the cost meter matches the chosen provider.
PRICE_IN_PER_1M = 1.50
PRICE_OUT_PER_1M = 7.50
USD_KRW = 1400

# Relaxed for dark fiction. SEXUALLY_EXPLICIT intentionally absent — see docstring.
FICTION_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

T = TypeVar("T", bound=BaseModel)


class LLMRefusal(RuntimeError):
    """The model declined, was filtered, or returned unusable output."""


# Transient upstream conditions worth retrying (demand spikes, rate limits).
_RETRY_STATUS = (429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 5


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in _RETRY_STATUS:
        return True
    text = str(exc)
    return any(str(s) in text for s in _RETRY_STATUS) or "UNAVAILABLE" in text


def _with_retry(fn, *, attempts: int = _MAX_ATTEMPTS):
    """Exponential backoff with jitter. A serial runs for weeks; a demand spike
    must not lose an episode."""
    delay = 4.0
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below when not transient
            if attempt == attempts or not _is_transient(exc):
                raise
            time.sleep(delay + random.uniform(0, 2))
            delay *= 2
    raise AssertionError("unreachable")


@dataclass
class Usage:
    """Token/cost accounting — feeds the budget-cap rail (DESIGN §5, §7)."""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thinking_tokens: int = 0
    calls: int = 0
    price_in_per_1m: float = PRICE_IN_PER_1M
    price_out_per_1m: float = PRICE_OUT_PER_1M
    usd_krw: float = USD_KRW

    def add(self, *, input_tokens: int, output_tokens: int,
            cached_tokens: int = 0, thinking_tokens: int = 0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_tokens += cached_tokens
        self.thinking_tokens += thinking_tokens
        self.calls += 1

    @property
    def usd(self) -> float:
        # Thinking tokens bill as output on Gemini reasoning models.
        out = self.output_tokens + self.thinking_tokens
        return (self.input_tokens / 1e6 * self.price_in_per_1m
                + out / 1e6 * self.price_out_per_1m)

    @property
    def krw(self) -> float:
        return self.usd * self.usd_krw


class LLM(Protocol):
    """The seam every node depends on. Tests inject a fake; prod injects Gemini."""

    def text(self, messages: list[dict], *, max_tokens: int = 8192) -> str: ...

    def structured(self, messages: list[dict], schema: type[T]) -> T: ...


def _split_messages(messages: list[dict]) -> tuple[str, str]:
    """Our ContextPack renders [system, user]; the native API takes them apart."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    user = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
    return system, user


class GeminiLLM:
    """Gemini via the official google-genai SDK (native API)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.6-flash",
        safety_settings: list[dict] | None = None,
        usage: Usage | None = None,
    ) -> None:
        from google import genai  # lazy: the deterministic core needs no SDK

        self.model = model
        self.usage = usage or Usage()
        self.safety_settings = (
            FICTION_SAFETY_SETTINGS if safety_settings is None else safety_settings
        )
        self._client = genai.Client(api_key=api_key)

    # ── config ──────────────────────────────────────────────────────────────
    def _config(self, *, system: str, max_tokens: int, schema: type[T] | None = None) -> dict:
        cfg: dict = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "safety_settings": self.safety_settings,
        }
        if schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = schema
        return cfg

    def _record(self, resp) -> None:
        u = getattr(resp, "usage_metadata", None)
        if not u:
            return
        self.usage.add(
            input_tokens=getattr(u, "prompt_token_count", 0) or 0,
            output_tokens=getattr(u, "candidates_token_count", 0) or 0,
            cached_tokens=getattr(u, "cached_content_token_count", 0) or 0,
            thinking_tokens=getattr(u, "thoughts_token_count", 0) or 0,
        )

    # ── calls ───────────────────────────────────────────────────────────────
    def text(self, messages: list[dict], *, max_tokens: int = 8192) -> str:
        system, user = _split_messages(messages)
        resp = _with_retry(
            lambda: self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=self._config(system=system, max_tokens=max_tokens),
            )
        )
        self._record(resp)
        out = resp.text
        if not out:
            raise LLMRefusal(self._diagnose(resp))
        return out

    def structured(self, messages: list[dict], schema: type[T]) -> T:
        """Constrained decoding to a Pydantic model. Keep schemas SHALLOW —
        responseSchema is a JSON-Schema subset (DESIGN §5)."""
        system, user = _split_messages(messages)
        resp = _with_retry(
            lambda: self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=self._config(system=system, max_tokens=8192, schema=schema),
            )
        )
        self._record(resp)
        parsed = getattr(resp, "parsed", None)
        if parsed is None:
            raise LLMRefusal(self._diagnose(resp))
        return parsed

    def count_tokens(self, text: str) -> int:
        """Re-baseline budgets on REAL Korean text (DESIGN §3/§5)."""
        r = self._client.models.count_tokens(model=self.model, contents=text)
        return r.total_tokens

    # ── diagnostics ─────────────────────────────────────────────────────────
    def _diagnose(self, resp) -> str:
        """Turn an empty response into an actionable reason (filter vs refusal)."""
        bits = []
        fb = getattr(resp, "prompt_feedback", None)
        if fb is not None and getattr(fb, "block_reason", None):
            bits.append(f"prompt blocked: {fb.block_reason}")
        for cand in getattr(resp, "candidates", None) or []:
            if getattr(cand, "finish_reason", None):
                bits.append(f"finish_reason={cand.finish_reason}")
            for r in getattr(cand, "safety_ratings", None) or []:
                if getattr(r, "blocked", False):
                    bits.append(f"blocked_category={r.category}")
        return "; ".join(bits) or "empty response"


class OpenAICompatLLM:
    """Any OpenAI-Chat-Completions-compatible endpoint (OpenAI, Moonshot/Kimi,
    DeepSeek, Upstage, OpenRouter, …). No safety-settings concept."""

    def __init__(self, *, api_key: str, model: str, base_url: str,
                 usage: Usage | None = None) -> None:
        from openai import OpenAI

        self.model = model
        self.usage = usage or Usage()
        self._client = OpenAI(api_key=api_key, base_url=base_url or None)

    def _record(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if not u:
            return
        self.usage.add(
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
        )

    def text(self, messages: list[dict], *, max_tokens: int = 8192) -> str:
        resp = _with_retry(lambda: self._client.chat.completions.create(
            model=self.model, messages=messages, max_tokens=max_tokens))
        self._record(resp)
        out = resp.choices[0].message.content or ""
        if not out:
            raise LLMRefusal("empty response")
        return out

    def structured(self, messages: list[dict], schema: type[T]) -> T:
        resp = _with_retry(lambda: self._client.beta.chat.completions.parse(
            model=self.model, messages=messages, response_format=schema))
        self._record(resp)
        parsed = resp.choices[0].message.parsed
        if parsed is None:
            raise LLMRefusal(resp.choices[0].message.content or "unparseable")
        return parsed

    def count_tokens(self, text: str) -> int:
        """No universal endpoint across compatible providers — rough estimate."""
        return max(1, int(len(text) * 1.3))


def build_llm(settings=None, **overrides) -> LLM:
    """Construct the LLM named by the environment (config.Settings).

    provider=gemini → native google-genai (keeps safety-setting control).
    provider=openai → any OpenAI-compatible endpoint via base_url.
    """
    if settings is None:
        from .config import load_settings

        settings = load_settings()

    usage = overrides.pop("usage", None) or Usage(
        price_in_per_1m=settings.price_in_per_1m,
        price_out_per_1m=settings.price_out_per_1m,
        usd_krw=settings.usd_krw,
    )
    provider = str(overrides.pop("provider", settings.llm_provider)).lower()
    model = overrides.pop("model", settings.llm_model)
    api_key = overrides.pop("api_key", settings.llm_api_key)

    if provider in ("gemini", "google"):
        return GeminiLLM(api_key=api_key, model=model, usage=usage, **overrides)
    if provider in ("openai", "openai-compatible", "compat"):
        base_url = overrides.pop("base_url", settings.resolved_base_url())
        return OpenAICompatLLM(api_key=api_key, model=model,
                               base_url=base_url, usage=usage, **overrides)
    raise ValueError(f"unknown NOVEL_LLM_PROVIDER: {provider!r} (use 'gemini' or 'openai')")
