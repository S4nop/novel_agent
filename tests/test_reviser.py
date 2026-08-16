"""Behavior tests for the bounded revise loop (DESIGN §3)."""
from novel_agent.artifacts import Draft
from novel_agent.context_pack import ContextPackBuilder
from novel_agent.ledgers import ForeshadowLedger, RhythmState
from novel_agent.reviser import length_findings, revise_draft

from .factories import beat_sheet, canon, genre_profile, north_star, summary, voice_bible

CLEAN = "\n".join(
    ['"가진 놈 것만 훔친다."', "놈이 칼자루를 쥐었다.", '"규칙인가?"', '"취향이다."',
     "골목이 조용해졌다.", '"세 걸음."'] * 4
)
DIRTY = '"오이오이!! 정말 대단하다고?!"\n' + "역시 나였다. 나는 분노했다.\n" * 5


class SequenceLLM:
    """Boundary fake returning a queued prose string per call."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def text(self, messages, *, max_tokens=8192):
        self.calls += 1
        return self.outputs.pop(0) if self.outputs else "여전히 오이오이!!"

    def structured(self, messages, schema):  # pragma: no cover
        raise NotImplementedError


def _pack():
    return ContextPackBuilder().build(
        genre_profile=genre_profile(), north_star=north_star(), voice_bible=voice_bible(),
        canon=canon(), beat_sheet=beat_sheet(1), foreshadow=ForeshadowLedger(),
        rhythm=RhythmState(), summary=summary(), current_episode=1, previous_episode=None,
    )


def _draft(prose):
    return Draft(episode_number=1, prose=prose)


def test_reports_shortfall_when_draft_is_under_target():
    findings = length_findings(_draft("가" * 2000), 5200)
    assert findings and "분량 미달" in findings[0]


def test_reports_no_length_finding_inside_tolerance():
    assert length_findings(_draft("가" * 5000), 5200) == []


def test_clean_draft_needs_no_revision_call():
    llm = SequenceLLM()
    result = revise_draft(llm, _draft(CLEAN), _pack(), target_chars=len(CLEAN))
    assert llm.calls == 0
    assert result.iterations == 0
    assert result.passed is True


def test_revision_replaces_draft_when_style_improves():
    llm = SequenceLLM(CLEAN)
    result = revise_draft(llm, _draft(DIRTY), _pack(), target_chars=len(CLEAN))
    assert result.iterations == 1
    assert result.draft.prose == CLEAN
    assert result.score > 50


def test_keeps_best_version_when_revision_makes_it_worse():
    """Over-editing regresses — a worse candidate must be discarded."""
    worse = '"오이오이!!" 역시 나였다. 나는 분노했다. 정말 너무 대단하다.\n' * 3
    mild = "놈이 칼자루를 쥐었다. 나는 분노했다.\n" * 3
    llm = SequenceLLM(worse, worse)
    result = revise_draft(llm, _draft(mild), _pack(), target_chars=len(mild),
                          max_iterations=2)
    assert result.draft.prose == mild          # original kept
    assert result.iterations == 2               # but it did try


def test_revision_loop_is_bounded_by_max_iterations():
    llm = SequenceLLM()   # always returns dirty prose
    result = revise_draft(llm, _draft(DIRTY), _pack(), target_chars=5200, max_iterations=2)
    assert llm.calls == 2
    assert result.iterations == 2
    assert result.passed is False
    assert result.remaining              # findings still reported for escalation


def test_failing_draft_reports_remaining_violations_for_escalation():
    result = revise_draft(SequenceLLM(), _draft(DIRTY), _pack(),
                          target_chars=5200, max_iterations=1)
    assert result.passed is False
    assert any(v.severity == "blocker" for v in result.remaining)


def test_prefers_length_correct_candidate_at_equal_style_score():
    """The keep-best bug this guards: a candidate that fixes length but trades one
    violation for another scores identically, and must NOT be discarded."""
    short = "놈이 칼자루를 쥐었다. 나는 분노했다.\n" * 3          # short + 1 major
    full_len = len(short) * 3
    # same style score (one major), but correct length
    correct = "놈이 칼자루를 쥐었다. 나는 분노했다.\n" * 9
    llm = SequenceLLM(correct)
    result = revise_draft(llm, _draft(short), _pack(), target_chars=full_len, max_iterations=1)
    assert result.draft.prose == correct.strip()   # drafter strips surrounding space
    assert result.draft.char_count > len(short)


# ── Track A as a hard gate (클로드 제안 5) ────────────────────────────────────
def _continuity_blocker(_draft):
    from novel_agent.style import Violation
    return [Violation(rule="캐논 위반: 퇴장한 인물 등장", severity="blocker",
                      count=1, limit="0회", evidence="'현무'")]


def test_a_continuity_blocker_fails_the_gate_despite_a_passing_style_score():
    """A canon break must not be style-scored away. Before Track A the gate saw
    only prose quality, so an episode contradicting canon could pass cleanly."""
    target = len(CLEAN)
    clean_only = revise_draft(SequenceLLM(), _draft(CLEAN), _pack(),
                              target_chars=target, max_iterations=1)
    assert clean_only.passed is True          # baseline: prose alone passes

    gated = revise_draft(SequenceLLM(), _draft(CLEAN), _pack(), target_chars=target,
                         max_iterations=1, extra_findings=_continuity_blocker)
    assert gated.passed is False
    assert any(v.rule.startswith("캐논 위반") for v in gated.remaining)


def test_continuity_findings_are_fed_to_the_reviser_as_fix_instructions():
    """Findings must reach the revise prompt, or the loop cannot repair them."""
    llm = SequenceLLM(CLEAN)
    revise_draft(llm, _draft(CLEAN), _pack(), target_chars=len(CLEAN),
                 max_iterations=1, extra_findings=_continuity_blocker)
    assert llm.calls == 1                      # a clean draft still got revised...


def test_a_short_over_dialogue_draft_is_told_to_add_narration_not_more_dialogue():
    """The ratchet: every short draft used to be told to convert narration into
    dialogue, with no finding able to ask for the opposite — which is how one
    run reached 83% dialogue characters."""
    from novel_agent.reviser import _fix_instructions
    from novel_agent.style import Violation

    over = [Violation(rule="지문 부족(대사 과다)", severity="major", count=83,
                      limit="65% 이하", limit_num=65)]
    text = _fix_instructions(over, ["분량 미달: 현재 2680자, 목표 5200자"])
    assert "대사로 채우지 마세요" in text
    assert "지문 덩어리를 인물 간" not in text     # the old advice must not appear

    under = [Violation(rule="대사 줄 비중", severity="major", count=26,
                       limit="40% 이상", limit_num=40, lower_is_better=False)]
    text2 = _fix_instructions(under, ["분량 미달: 현재 2680자, 목표 5200자"])
    assert "지문 덩어리를 인물 간" in text2       # still given when dialogue IS low
