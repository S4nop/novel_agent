"""Track A continuity checking (클로드 제안 5).

Why this exists at all, from the project's own measurement in
`phase0-results.md`: neither model self-reported its invented facts. So
`[[FACT:]]` self-reporting cannot be the safety net, and continuity has to be
checked by something other than the writer.

The deterministic rules are tested exhaustively because they are the half that
must hold when the judge call is flaky, rate-limited, or refused.
"""
import pytest

from novel_agent.artifacts import (
    Beat,
    BeatSheet,
    BeatType,
    Canon,
    CharacterCard,
    Draft,
    GlossaryEntry,
    KnownFact,
    WorldRule,
)
from novel_agent.continuity import (
    RULE_CANON_CONTRADICTION,
    RULE_GLOSSARY_DRIFT,
    RULE_PLANNED_ABSENT,
    RULE_RETIRED_ACTOR,
    blocks_acceptance,
    check_continuity,
    deterministic_findings,
)
from novel_agent.schemas import ContinuityFindingDraft, ContinuityReportDraft


def _canon(**chars) -> Canon:
    return Canon(characters={n: c for n, c in chars.items()})


def _beats(entities=()) -> BeatSheet:
    return BeatSheet(episode_number=3, opening_hook="훅", the_one_progression="진전",
                     beats=[Beat(text="b", beat_type=BeatType.SETUP)],
                     closing_cliffhanger="절단", entities_present=list(entities))


def _draft(prose: str) -> Draft:
    return Draft(episode_number=3, prose=prose)


def rules(vs) -> set[str]:
    return {v.rule for v in vs}


# ── retired/dead actor ───────────────────────────────────────────────────────
def test_a_dead_character_acting_in_the_scene_is_a_blocker():
    canon = _canon(현무=CharacterCard(name="현무", status="dead"))
    vs = deterministic_findings(_draft("현무가 칼을 뽑았다."), _beats(), canon)
    v = next(x for x in vs if x.rule == RULE_RETIRED_ACTOR)
    assert v.severity == "blocker"
    assert "현무" in v.evidence


def test_a_retired_character_is_caught_through_an_alias():
    """Canon aliases are how a long serial refers to the same person."""
    canon = _canon(현무=CharacterCard(name="현무", aliases=["묵빛"], status="퇴장"))
    vs = deterministic_findings(_draft("묵빛이 문을 열었다."), _beats(), canon)
    assert RULE_RETIRED_ACTOR in rules(vs)


def test_an_active_character_acting_is_not_a_finding():
    canon = _canon(케이타=CharacterCard(name="케이타", status="active"))
    vs = deterministic_findings(_draft("케이타가 장부를 넘겼다."), _beats(), canon)
    assert RULE_RETIRED_ACTOR not in rules(vs)


def test_an_unfamiliar_status_is_treated_as_present_not_silently_skipped():
    """A status the checker does not recognise must not disable the rule — but it
    also must not fire, or every custom status becomes a false blocker."""
    canon = _canon(케이타=CharacterCard(name="케이타", status="투옥"))
    vs = deterministic_findings(_draft("케이타가 걸었다."), _beats(), canon)
    assert RULE_RETIRED_ACTOR not in rules(vs)


def test_a_dead_character_merely_absent_from_the_prose_is_fine():
    canon = _canon(현무=CharacterCard(name="현무", status="dead"))
    vs = deterministic_findings(_draft("케이타는 혼자였다."), _beats(), canon)
    assert vs == []


# ── glossary drift ───────────────────────────────────────────────────────────
def test_using_a_non_canonical_spelling_of_a_glossary_term_is_flagged():
    canon = Canon(glossary=[GlossaryEntry(term="천유결계", canonical_form="天流結界")])
    vs = deterministic_findings(_draft("천유결계가 흔들렸다."), _beats(), canon)
    v = next(x for x in vs if x.rule == RULE_GLOSSARY_DRIFT)
    assert v.severity == "major"
    assert "天流結界" in v.evidence


def test_the_canonical_form_being_present_clears_the_drift_finding():
    canon = Canon(glossary=[GlossaryEntry(term="천유결계", canonical_form="天流結界")])
    vs = deterministic_findings(_draft("天流結界가 흔들렸다."), _beats(), canon)
    assert RULE_GLOSSARY_DRIFT not in rules(vs)


def test_a_term_whose_canonical_form_equals_itself_never_drifts():
    canon = Canon(glossary=[GlossaryEntry(term="호패", canonical_form="호패")])
    vs = deterministic_findings(_draft("호패를 꺼냈다."), _beats(), canon)
    assert vs == []


# ── plan reconciliation ──────────────────────────────────────────────────────
def test_a_planned_entity_missing_from_the_prose_is_reported_as_minor():
    """A divergence signal for the re-planner, not necessarily an error."""
    vs = deterministic_findings(_draft("케이타만 있었다."), _beats(["케이타", "감사관"]),
                                Canon())
    v = next(x for x in vs if x.rule == RULE_PLANNED_ABSENT)
    assert v.severity == "minor" and "감사관" in v.evidence


def test_all_planned_entities_present_produces_nothing():
    vs = deterministic_findings(_draft("케이타와 감사관이 마주 섰다."),
                                _beats(["케이타", "감사관"]), Canon())
    assert vs == []


# ── the hard gate ────────────────────────────────────────────────────────────
def test_a_continuity_blocker_refuses_acceptance_regardless_of_prose_quality():
    """Track A is a hard gate: a canon break cannot be style-scored away."""
    canon = _canon(현무=CharacterCard(name="현무", status="dead"))
    vs = deterministic_findings(_draft("현무가 웃었다."), _beats(), canon)
    assert blocks_acceptance(vs) is True


def test_only_minor_findings_do_not_block_acceptance():
    vs = deterministic_findings(_draft("케이타."), _beats(["케이타", "없는사람"]), Canon())
    assert vs and blocks_acceptance(vs) is False


# ── the LLM half ─────────────────────────────────────────────────────────────
class StubJudge:
    def __init__(self, report=None, boom=False):
        self.report, self.boom, self.prompts = report, boom, []

    def structured(self, messages, schema):
        self.prompts.append(messages[-1]["content"])
        if self.boom:
            raise RuntimeError("rate limited")
        return self.report

    def text(self, messages, *, max_tokens=8192):
        raise AssertionError("continuity must use structured output")


def test_a_model_reported_contradiction_becomes_a_finding():
    judge = StubJudge(ContinuityReportDraft(findings=[
        ContinuityFindingDraft(canon_fact="왼쪽 뺨에 흉터", prose_claim="오른쪽 뺨의 흉터",
                               severity="blocker", where="오른쪽 뺨")]))
    vs = check_continuity(judge, _draft("오른쪽 뺨의 흉터가 씰룩였다."), _beats(), Canon())
    v = next(x for x in vs if x.rule == RULE_CANON_CONTRADICTION)
    assert v.severity == "blocker"
    assert "왼쪽 뺨에 흉터" in v.evidence and "오른쪽 뺨의 흉터" in v.evidence


def test_a_judge_failure_still_returns_the_deterministic_findings():
    """An unattended run must not lose an episode because the judge call died."""
    canon = _canon(현무=CharacterCard(name="현무", status="dead"))
    vs = check_continuity(StubJudge(boom=True), _draft("현무가 웃었다."), _beats(), canon)
    assert rules(vs) == {RULE_RETIRED_ACTOR}


def test_an_unusable_severity_falls_back_to_major_rather_than_crashing():
    judge = StubJudge(ContinuityReportDraft(findings=[
        ContinuityFindingDraft(canon_fact="a", prose_claim="b", severity="치명적")]))
    vs = check_continuity(judge, _draft("x"), _beats(), Canon())
    assert vs[0].severity == "major"


def test_incomplete_findings_are_dropped():
    judge = StubJudge(ContinuityReportDraft(findings=[
        ContinuityFindingDraft(canon_fact="", prose_claim="b", severity="major"),
        ContinuityFindingDraft(canon_fact="a", prose_claim="", severity="major")]))
    assert check_continuity(judge, _draft("x"), _beats(), Canon()) == []


def test_the_judge_sees_canon_and_prose_but_not_the_plan_or_its_rationale():
    """Judge independence at context level (invariant #3): it must not be shown
    what the draft was TRYING to do, or it will agree with the attempt."""
    canon = _canon(케이타=CharacterCard(
        name="케이타", status="active", immutable_descriptors=["왼쪽 귀 위 흉터"],
        known_facts=[KnownFact(fact="등급증을 받지 못했다", learned_episode=1)]))
    canon.world_rules.append(WorldRule(text="원격 보정은 불가능하다"))
    judge = StubJudge(ContinuityReportDraft(findings=[]))
    check_continuity(judge, _draft("본문입니다."), _beats(["케이타"]), canon)
    sent = judge.prompts[-1]
    assert "왼쪽 귀 위 흉터" in sent          # canon reaches the judge
    assert "등급증을 받지 못했다" in sent
    assert "원격 보정은 불가능하다" in sent
    assert "본문입니다." in sent
    assert "진전" not in sent and "절단" not in sent   # the plan's intent does not


def test_findings_are_ordered_worst_first():
    judge = StubJudge(ContinuityReportDraft(findings=[
        ContinuityFindingDraft(canon_fact="a", prose_claim="b", severity="minor"),
        ContinuityFindingDraft(canon_fact="c", prose_claim="d", severity="blocker")]))
    vs = check_continuity(judge, _draft("x"), _beats(), Canon())
    assert [v.severity for v in vs] == ["blocker", "minor"]


def test_every_continuity_rule_explains_itself_in_the_ui():
    """테스트 피드백 3 applies to Track A too — a bare finding teaches nothing."""
    from novel_agent.style import rule_meta
    for r in (RULE_RETIRED_ACTOR, RULE_GLOSSARY_DRIFT, RULE_PLANNED_ABSENT,
              RULE_CANON_CONTRADICTION):
        m = rule_meta(r)
        assert m and m.why and m.fix, r


# ── the gate must WITHHOLD, not merely label ─────────────────────────────────
def test_a_blocked_episode_must_not_be_committed_to_canon():
    """Regression: the gate computed a verdict and then committed anyway, so a
    contradiction was written into canon and its 떡밥 planted — the failure was
    labelled while the damage went through. A retry then re-planted the same
    seeds under fresh ids, pushing 완결 further away on every attempt.

    Asserted at the decision level the endpoint uses, so it holds without a full
    LLM chain: `committed` must be false whenever continuity blocks.
    """
    canon = _canon(현무=CharacterCard(name="현무", status="dead"))
    findings = deterministic_findings(_draft("현무가 웃었다."), _beats(), canon)
    blocked = blocks_acceptance(findings)
    # mirrors web/app.py: committed = not blocked and (result.passed if result else True)
    for style_gate_passed in (True, False):
        committed = not blocked and style_gate_passed
        assert committed is False, "a canon break must never reach the ledgers"

    clean = deterministic_findings(_draft("케이타가 걸었다."), _beats(), Canon())
    assert (not blocks_acceptance(clean) and True) is True   # clean prose commits
