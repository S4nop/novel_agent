"""Behavior tests for the Drafter node — LLM faked at the boundary only."""
from novel_agent.artifacts import Draft
from novel_agent.context_pack import ContextPackBuilder
from novel_agent.drafter import draft_episode, extract_fact_requests
from novel_agent.ledgers import ForeshadowLedger, RhythmState
from novel_agent.llm import Usage

from .factories import beat_sheet, canon, genre_profile, north_star, summary, voice_bible


class FakeLLM:
    """Boundary fake: records the messages it was given, returns canned prose."""

    def __init__(self, prose: str) -> None:
        self.prose = prose
        self.seen: list[dict] | None = None

    def text(self, messages, *, max_tokens=8192):
        self.seen = messages
        return self.prose

    def structured(self, messages, schema):  # pragma: no cover - unused here
        raise NotImplementedError


def _pack(episode=1):
    return ContextPackBuilder().build(
        genre_profile=genre_profile(), north_star=north_star(), voice_bible=voice_bible(),
        canon=canon(), beat_sheet=beat_sheet(episode), foreshadow=ForeshadowLedger(),
        rhythm=RhythmState(), summary=summary(), current_episode=episode,
        previous_episode=None,
    )


def test_extracts_fact_requests_and_strips_markers_from_prose():
    cleaned, reqs = extract_fact_requests(
        "문이 열렸다.\n[[FACT: 길드장의 이름이 무엇인가?]]\n그가 웃었다."
    )
    assert "[[FACT" not in cleaned
    assert cleaned.startswith("문이 열렸다.")
    assert [r.question for r in reqs] == ["길드장의 이름이 무엇인가?"]
    assert reqs[0].blocking is True


def test_prose_without_markers_yields_no_fact_requests():
    cleaned, reqs = extract_fact_requests("문이 열렸다.")
    assert cleaned == "문이 열렸다."
    assert reqs == []


def test_draft_reports_episode_number_and_korean_char_count():
    llm = FakeLLM("가나다라마")
    draft = draft_episode(llm, _pack(episode=7))
    assert isinstance(draft, Draft)
    assert draft.episode_number == 7
    assert draft.char_count == 5


def test_draft_carries_blocking_fact_request_flag():
    llm = FakeLLM("본문 [[FACT: 게이트 등급 체계는?]] 계속")
    draft = draft_episode(llm, _pack())
    assert draft.has_blocking_fact_request() is True


def test_drafter_sends_system_prefix_then_volatile_user_turn():
    llm = FakeLLM("본문")
    draft_episode(llm, _pack())
    assert [m["role"] for m in llm.seen] == ["system", "user"]
    assert "한국어로만" in llm.seen[0]["content"]      # Korean mandate in system
    assert "비트시트" in llm.seen[1]["content"]        # episode plan in user turn


def test_usage_accumulates_cost_in_usd_and_krw():
    u = Usage()
    u.add(input_tokens=1_000_000, output_tokens=1_000_000)
    assert round(u.usd, 2) == 18.00       # claude-sonnet-5: $3 in + $15 out
    assert u.calls == 1
    assert round(u.krw) == 25200
