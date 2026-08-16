"""Provider adapter — the ONLY module that talks to the model (DESIGN §5).

Default model for every LLM role: **Claude Sonnet 5** (`claude-sonnet-5`).
Keeping this behind a thin adapter is what makes the model swappable without
touching any node; `gemini` and `openai` remain selectable from `.env`.

Deliberately NOT a LangChain wrapper: nodes call `f(inputs) -> validated output`
so the deterministic core stays testable (tests inject a fake at this seam).

ANTHROPIC SPECIFICS THAT SHAPE THIS FILE
  * Adaptive thinking is ON by default on Sonnet 5, and `max_tokens` caps
    thinking + visible text TOGETHER. A budget sized for prose alone truncates
    mid-sentence, so `text()` adds THINKING_HEADROOM on top of the caller's ask.
  * Prompt caching is EXPLICIT here (Gemini's was implicit). The ContextPack's
    cache-stable prefix arrives as the system message, so that is where the
    `cache_control` breakpoint goes — see ContextPackBuilder for why the prefix
    is byte-stable. Below ~1024 tokens a prefix silently will not cache.
  * `temperature`/`top_p`/`top_k` and `thinking.budget_tokens` are REJECTED
    (400) on Sonnet 5 — steer with the prompt, size effort with `effort`.
  * A declined request returns HTTP 200 with `stop_reason == "refusal"`, so the
    refusal check must happen before reading content.

CONTENT POLICY (DESIGN §5): there is no safety-settings knob on Anthropic —
`FICTION_SAFETY_SETTINGS` applies to provider=gemini only. Anthropic's usage
policy prohibits sexually explicit content regardless of framing, which matches
the scope already chosen (전연령 / 15+; explicit-19+ is out of scope). Dark and
violent genre content — 사이다 revenge, villain POV — is in scope and needs no
special configuration, but re-test the darkest end of the range on any switch.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

# Default per-1M-token USD prices (claude-sonnet-5, standard rate). Anthropic's
# introductory $2/$10 runs through 2026-08-31; defaulting to the standard rate
# keeps the meter honest past that date. Override via Usage(...) or NOVEL_PRICE_*.
PRICE_IN_PER_1M = 3.00
PRICE_OUT_PER_1M = 15.00
USD_KRW = 1400

# Cache-tier multipliers on the INPUT price (Anthropic prompt caching).
CACHE_READ_MULTIPLIER = 0.1     # served from cache
CACHE_WRITE_MULTIPLIER = 1.25   # written to cache, 5-minute TTL

# Room for adaptive thinking on top of the caller's visible-output budget.
# max_tokens bounds thinking + text together; the model is billed for what it
# actually uses, so a generous cap costs nothing but prevents truncation.
THINKING_HEADROOM = 8192

# Relaxed for dark fiction. provider=gemini ONLY — no Anthropic equivalent.
# SEXUALLY_EXPLICIT intentionally absent — see module docstring.
FICTION_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

T = TypeVar("T", bound=BaseModel)


class LLMRefusal(RuntimeError):
    """The model declined, was filtered, or returned unusable output."""


class LLMUnavailable(RuntimeError):
    """The account cannot call the API at all — no credit, bad key, no access.

    Distinct from a refusal because retrying is pointless and the circuit
    breaker is the wrong response: every remaining episode would fail the same
    way. An unattended run must stop immediately and say what to do.
    """


# Transient upstream conditions worth retrying (demand spikes, rate limits).
_RETRY_STATUS = (429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 5


_FATAL_MARKERS = ("credit balance is too low", "insufficient_quota",
                  "authentication_error", "invalid x-api-key",
                  "permission_error", "billing")


def _fatal_account_error(exc: Exception) -> str:
    """Account-level failures that no retry can clear."""
    text = str(exc).lower()
    return next((m for m in _FATAL_MARKERS if m in text), "")


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
            marker = _fatal_account_error(exc)
            if marker:
                raise LLMUnavailable(str(exc)) from exc
            if attempt == attempts or not _is_transient(exc):
                raise
            time.sleep(delay + random.uniform(0, 2))
            delay *= 2
    raise AssertionError("unreachable")


@dataclass
class Usage:
    """Token/cost accounting — feeds the budget-cap rail (DESIGN §5, §7).

    Field semantics are normalized ACROSS providers so the meter is comparable:
      input_tokens        full-price input, EXCLUDING anything cached
      cached_tokens       served from cache   (billed at 0.1x input)
      cache_write_tokens  written to cache    (billed at 1.25x input)
      output_tokens       visible output
      thinking_tokens     reasoning billed as output ON TOP of output_tokens.
                          Anthropic already folds thinking into output_tokens,
                          so its adapter leaves this at 0 to avoid double-billing.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    calls: int = 0
    price_in_per_1m: float = PRICE_IN_PER_1M
    price_out_per_1m: float = PRICE_OUT_PER_1M
    usd_krw: float = USD_KRW

    def add(self, *, input_tokens: int, output_tokens: int,
            cached_tokens: int = 0, cache_write_tokens: int = 0,
            thinking_tokens: int = 0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_tokens += cached_tokens
        self.cache_write_tokens += cache_write_tokens
        self.thinking_tokens += thinking_tokens
        self.calls += 1

    @property
    def usd(self) -> float:
        billable_in = (self.input_tokens
                       + self.cached_tokens * CACHE_READ_MULTIPLIER
                       + self.cache_write_tokens * CACHE_WRITE_MULTIPLIER)
        out = self.output_tokens + self.thinking_tokens
        return (billable_in / 1e6 * self.price_in_per_1m
                + out / 1e6 * self.price_out_per_1m)

    @property
    def cache_hit_rate(self) -> float:
        """0.0 across repeated calls means a silent cache invalidator upstream —
        the ContextPack prefix is not byte-stable. Worth surfacing in the UI."""
        total_in = self.input_tokens + self.cached_tokens + self.cache_write_tokens
        return (self.cached_tokens / total_in) if total_in else 0.0

    @property
    def krw(self) -> float:
        return self.usd * self.usd_krw


class LLM(Protocol):
    """The seam every node depends on. Tests inject a fake; prod injects Claude."""

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
        # Gemini's prompt_token_count INCLUDES cached tokens; Usage wants them
        # split so the discounted tier is priced correctly (and comparably).
        cached = getattr(u, "cached_content_token_count", 0) or 0
        prompt = getattr(u, "prompt_token_count", 0) or 0
        self.usage.add(
            input_tokens=max(0, prompt - cached),
            output_tokens=getattr(u, "candidates_token_count", 0) or 0,
            cached_tokens=cached,
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


class AnthropicLLM:
    """Claude via the official `anthropic` SDK. Default provider.

    Adaptive thinking is left at the Sonnet 5 default (on) — do not pass a
    `thinking` block or sampling parameters, both are 400s on this model.
    Depth is sized with `effort`; `high` is the API default.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-5",
        effort: str = "high",
        cache_prefix: bool = True,
        usage: Usage | None = None,
        timeout: float = 600.0,
        http_client=None,
    ) -> None:
        import anthropic  # lazy: the deterministic core needs no SDK

        self.model = model
        self.effort = effort
        self.cache_prefix = cache_prefix
        self.usage = usage or Usage()
        # The SDK retries 429/5xx itself; _with_retry wraps it for the long
        # waits a weeks-long serial needs. timeout suppresses the SDK's
        # non-streaming large-max_tokens guard.
        # http_client: proxy support in prod, a MockTransport in tests — the
        # fake sits at the HTTP boundary so request shaping is really exercised.
        extra = {"http_client": http_client} if http_client is not None else {}
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=2, **extra
        )

    # ── request shaping ─────────────────────────────────────────────────────
    def _system(self, system: str):
        """The ContextPack's cache-stable prefix — the one cache breakpoint.

        Returns NOT_GIVEN (not None) when there is no system message: None
        serializes as `"system": null`, which the API rejects with a 400. The
        health check sends a bare user turn, so this path is real.
        """
        if not system:
            import anthropic

            return anthropic.NOT_GIVEN
        block: dict = {"type": "text", "text": system}
        if self.cache_prefix:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _record(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if not u:
            return
        # thinking_tokens stays 0: Anthropic counts thinking inside output_tokens.
        self.usage.add(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cached_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    # ── calls ───────────────────────────────────────────────────────────────
    def text(self, messages: list[dict], *, max_tokens: int = 8192) -> str:
        """Streamed so long Korean episodes never hit an HTTP read timeout."""
        system, user = _split_messages(messages)

        def run():
            with self._client.messages.stream(
                model=self.model,
                max_tokens=max_tokens + THINKING_HEADROOM,
                system=self._system(system),
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": user}],
            ) as stream:
                return stream.get_final_message()

        resp = _with_retry(run)
        self._record(resp)
        self._check_stop(resp)
        out = "".join(b.text for b in resp.content if b.type == "text")
        if not out.strip():
            raise LLMRefusal(self._diagnose(resp))
        return out

    def structured(self, messages: list[dict], schema: type[T]) -> T:
        """Constrained decoding to a Pydantic model via the SDK's parse helper,
        which validates the response against the schema for us."""
        system, user = _split_messages(messages)
        resp = _with_retry(lambda: self._client.messages.parse(
            model=self.model,
            max_tokens=8192 + THINKING_HEADROOM,
            system=self._system(system),
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        ))
        self._record(resp)
        self._check_stop(resp)
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise LLMRefusal(self._diagnose(resp))
        return parsed

    def count_tokens(self, text: str) -> int:
        """Re-baseline budgets on REAL Korean text (DESIGN §3/§5)."""
        r = self._client.messages.count_tokens(
            model=self.model, messages=[{"role": "user", "content": text}]
        )
        return r.input_tokens

    # ── diagnostics ─────────────────────────────────────────────────────────
    def _check_stop(self, resp) -> None:
        """A decline is HTTP 200 with stop_reason='refusal' — check it BEFORE
        reading content. Truncation gets its own message because the fix is
        different (raise the budget, not rewrite the prompt)."""
        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            raise LLMRefusal(self._diagnose(resp))
        if stop == "max_tokens":
            raise LLMRefusal(
                "max_tokens 초과로 응답이 잘렸습니다 — 적응형 사고가 예산을 함께 "
                f"소모합니다. THINKING_HEADROOM({THINKING_HEADROOM}) 또는 effort"
                f"('{self.effort}') 설정을 조정하세요."
            )

    def _diagnose(self, resp) -> str:
        bits = [f"stop_reason={getattr(resp, 'stop_reason', None)}"]
        details = getattr(resp, "stop_details", None)
        if details is not None:
            for attr in ("category", "explanation"):
                if getattr(details, attr, None):
                    bits.append(f"{attr}={getattr(details, attr)}")
        return "; ".join(bits)


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


PROVIDERS = ("anthropic", "gemini", "openai")


def build_llm(settings=None, **overrides) -> LLM:
    """Construct the LLM named by the environment (config.Settings).

    provider=anthropic → official anthropic SDK (default; Claude Sonnet 5).
    provider=gemini    → native google-genai (keeps safety-setting control).
    provider=openai    → any OpenAI-compatible endpoint via base_url.
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

    if provider in ("anthropic", "claude"):
        overrides.setdefault("effort", settings.llm_effort)
        return AnthropicLLM(api_key=api_key, model=model, usage=usage, **overrides)
    if provider in ("gemini", "google"):
        return GeminiLLM(api_key=api_key, model=model, usage=usage, **overrides)
    if provider in ("openai", "openai-compatible", "compat"):
        base_url = overrides.pop("base_url", settings.resolved_base_url())
        return OpenAICompatLLM(api_key=api_key, model=model,
                               base_url=base_url, usage=usage, **overrides)
    raise ValueError(
        f"unknown NOVEL_LLM_PROVIDER: {provider!r} (use one of {', '.join(PROVIDERS)})"
    )
