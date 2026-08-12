"""Behavior tests for the Anthropic adapter.

The fake sits at the HTTP boundary (httpx.MockTransport), not in front of the
SDK, so these exercise the real request shaping and real response parsing —
they fail if the wire contract drifts, which an object-level mock could not see.
"""
import json

import httpx
import pytest
from pydantic import BaseModel

from novel_agent.llm import THINKING_HEADROOM, AnthropicLLM, LLMRefusal, Usage


class Plan(BaseModel):
    hook: str
    beats: list[str]


def _sse(events: list[dict]) -> bytes:
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
    ).encode()


def _stream_body(text: str, *, stop_reason: str = "end_turn",
                 input_tokens: int = 10, cache_read: int = 0,
                 cache_write: int = 0, output_tokens: int = 25) -> bytes:
    return _sse([
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant",
            "model": "claude-sonnet-5", "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0,
                      "cache_read_input_tokens": cache_read,
                      "cache_creation_input_tokens": cache_write}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta",
         "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": {"output_tokens": output_tokens}},
        {"type": "message_stop"},
    ])


def _json_body(content: list[dict], *, stop_reason: str = "end_turn",
               stop_details: dict | None = None, usage: dict | None = None) -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude-sonnet-5", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage or {"input_tokens": 10, "output_tokens": 25},
    }


def make_llm(handler, **kw) -> tuple[AnthropicLLM, list[dict]]:
    """Returns the adapter plus a list that captures every request body sent."""
    sent: list[dict] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content) if request.content else {})
        return handler(request)

    llm = AnthropicLLM(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        **kw,
    )
    return llm, sent


def ok_stream(text="집필된 본문입니다.", **kw):
    return lambda _r: httpx.Response(
        200, content=_stream_body(text, **kw),
        headers={"content-type": "text/event-stream"})


def ok_json(payload):
    return lambda _r: httpx.Response(200, json=payload)


MESSAGES = [
    {"role": "system", "content": "고정 프리픽스: 캐논과 보이스 바이블."},
    {"role": "user", "content": "3화를 집필하세요."},
]


# ── request shaping ──────────────────────────────────────────────────────────
def test_returns_generated_prose_from_a_streamed_response():
    llm, _ = make_llm(ok_stream("1화 본문"))
    assert llm.text(MESSAGES) == "1화 본문"


def test_stable_prefix_is_sent_as_a_cached_system_block():
    """The whole ContextPack design rests on the prefix being cacheable."""
    llm, sent = make_llm(ok_stream())
    llm.text(MESSAGES)
    system = sent[0]["system"]
    assert system[0]["text"] == "고정 프리픽스: 캐논과 보이스 바이블."
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # only the volatile turn belongs in messages
    assert sent[0]["messages"] == [{"role": "user", "content": "3화를 집필하세요."}]


def test_a_bare_user_turn_omits_system_entirely():
    """`"system": null` is a 400. The /api/health probe sends no system turn,
    so this is the first call a new user makes."""
    llm, sent = make_llm(ok_stream("연결 정상"))
    assert llm.text([{"role": "user", "content": "연결 확인용입니다."}]) == "연결 정상"
    assert "system" not in sent[0]


def test_caching_can_be_switched_off_for_short_one_off_calls():
    llm, sent = make_llm(ok_stream(), cache_prefix=False)
    llm.text(MESSAGES)
    assert "cache_control" not in sent[0]["system"][0]


def test_budget_reserves_headroom_so_thinking_cannot_truncate_the_prose():
    llm, sent = make_llm(ok_stream())
    llm.text(MESSAGES, max_tokens=6000)
    assert sent[0]["max_tokens"] == 6000 + THINKING_HEADROOM


def test_effort_is_forwarded_and_rejected_sampling_params_are_never_sent():
    """temperature / top_p / top_k and thinking.budget_tokens are 400s on Sonnet 5."""
    llm, sent = make_llm(ok_stream(), effort="medium")
    llm.text(MESSAGES)
    body = sent[0]
    assert body["output_config"] == {"effort": "medium"}
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert "budget_tokens" not in json.dumps(body)


# ── structured output ────────────────────────────────────────────────────────
def test_structured_output_is_validated_into_the_requested_model():
    payload = _json_body([{
        "type": "text",
        "text": json.dumps({"hook": "절벽 끝", "beats": ["추격", "반전"]}),
    }])
    llm, sent = make_llm(ok_json(payload), effort="xhigh")
    plan = llm.structured(MESSAGES, Plan)
    assert plan == Plan(hook="절벽 끝", beats=["추격", "반전"])
    # the SDK injects `format` into output_config — it must MERGE, not clobber
    # our effort, or every structured call silently reverts to the default depth
    assert sent[0]["output_config"]["format"]["type"] == "json_schema"
    assert sent[0]["output_config"]["effort"] == "xhigh"


# ── failure modes ────────────────────────────────────────────────────────────
def test_refusal_is_raised_with_its_category_rather_than_read_as_content():
    """A decline is HTTP 200 — reading content first would yield silent garbage."""
    payload = _json_body([], stop_reason="refusal",
                         stop_details={"type": "refusal", "category": "cyber",
                                       "explanation": "declined"})
    llm, _ = make_llm(ok_json(payload))
    with pytest.raises(LLMRefusal, match="cyber"):
        llm.structured(MESSAGES, Plan)


def test_truncated_output_reports_the_budget_not_a_refusal():
    llm, _ = make_llm(ok_stream("잘린 본문", stop_reason="max_tokens"))
    with pytest.raises(LLMRefusal, match="max_tokens"):
        llm.text(MESSAGES)


def test_transient_upstream_failure_is_retried_and_then_succeeds(monkeypatch):
    """A serial runs for weeks; one 529 must not lose an episode."""
    monkeypatch.setattr("novel_agent.llm.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(_r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(529, json={"type": "error", "error": {
                "type": "overloaded_error", "message": "overloaded"}})
        return httpx.Response(200, content=_stream_body("복구된 본문"),
                              headers={"content-type": "text/event-stream"})

    llm, _ = make_llm(handler)
    assert llm.text(MESSAGES) == "복구된 본문"


# ── cost meter ───────────────────────────────────────────────────────────────
def test_cache_tiers_are_metered_at_their_discounted_rates():
    usage = Usage(price_in_per_1m=3.00, price_out_per_1m=15.00)
    llm, _ = make_llm(ok_stream(input_tokens=1000, cache_read=10_000,
                                cache_write=2_000, output_tokens=500),
                      usage=usage)
    llm.text(MESSAGES)
    # 1000 full + 10000*0.1 + 2000*1.25 = 4500 billable input tokens
    assert usage.input_tokens == 1000
    assert usage.cached_tokens == 10_000
    assert usage.cache_write_tokens == 2_000
    assert usage.usd == pytest.approx(4500 / 1e6 * 3.00 + 500 / 1e6 * 15.00)


def test_thinking_is_not_double_billed_on_top_of_output():
    """Anthropic already counts reasoning inside output_tokens."""
    usage = Usage()
    llm, _ = make_llm(ok_stream(output_tokens=800), usage=usage)
    llm.text(MESSAGES)
    assert usage.thinking_tokens == 0
    assert usage.output_tokens == 800


def test_cache_hit_rate_surfaces_a_broken_stable_prefix():
    usage = Usage()
    llm, _ = make_llm(ok_stream(input_tokens=1000, cache_read=0), usage=usage)
    llm.text(MESSAGES)
    assert usage.cache_hit_rate == 0.0      # nothing reused → prefix is unstable


# ── env → adapter wiring ─────────────────────────────────────────────────────
def test_env_selects_claude_and_carries_effort_and_pricing_through(monkeypatch):
    """A provider switch must be a .env edit, not a code change."""
    from novel_agent.config import Settings
    from novel_agent.llm import build_llm

    for k, v in {"NOVEL_LLM_PROVIDER": "anthropic",
                 "NOVEL_LLM_MODEL": "claude-sonnet-5",
                 "NOVEL_LLM_API_KEY": "sk-ant-test",
                 "NOVEL_LLM_EFFORT": "medium",
                 "NOVEL_PRICE_IN_PER_1M": "3.0",
                 "NOVEL_PRICE_OUT_PER_1M": "15.0"}.items():
        monkeypatch.setenv(k, v)

    llm = build_llm(Settings(_env_file=None))
    assert isinstance(llm, AnthropicLLM)
    assert llm.model == "claude-sonnet-5"
    assert llm.effort == "medium"
    assert llm.usage.price_out_per_1m == 15.0


def test_unknown_provider_names_the_valid_options(monkeypatch):
    from novel_agent.config import Settings
    from novel_agent.llm import build_llm

    monkeypatch.setenv("NOVEL_LLM_PROVIDER", "sonnet")   # a plausible mistake
    with pytest.raises(ValueError, match="anthropic"):
        build_llm(Settings(_env_file=None))


# ── token counting ───────────────────────────────────────────────────────────
def test_token_count_uses_the_real_endpoint_for_korean_text():
    llm, sent = make_llm(ok_json({"input_tokens": 1234}))
    assert llm.count_tokens("한국어 본문입니다.") == 1234
    assert sent[0]["model"] == "claude-sonnet-5"
