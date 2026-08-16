"""Track B craft judging (클로드 제안 5).

Track A asks "is this true?" and hard-blocks. Track B asks "is this any good?"
and must not, because craft is a judgement. A subjective judge with blocking
power stops an unattended run on an opinion — so the cap at `major` is the
load-bearing property here, not the findings themselves.
"""
from novel_agent.artifacts import Canon, CharacterCard, Draft, VoiceBible, VoiceCard
from novel_agent.craft import RULE_CHARACTER, RULE_GENRE, RULE_PLOT, judge_craft
from novel_agent.schemas import CraftFindingDraft, CraftReportDraft

from .factories import genre_profile


class StubJudge:
    def __init__(self, report=None, boom=False):
        self.report, self.boom, self.prompts = report or CraftReportDraft(), boom, []

    def structured(self, messages, schema):
        self.prompts.append(messages[-1]["content"])
        if self.boom:
            raise RuntimeError("timeout")
        return self.report

    def text(self, messages, *, max_tokens=8192):
        raise AssertionError("craft judging must use structured output")


def _canon():
    return Canon(characters={"봉출": CharacterCard(
        name="봉출", voice=VoiceCard(speech_register="사무적 존댓말",
                                     honorific_pattern="-습니다",
                                     speech_tics=["부품값 얘기로 화제 전환"],
                                     exemplar_lines=["그건 도사가 아니라 접점 부식입니다."]))})


def _judge(report=None, boom=False, prose="본문입니다."):
    llm = StubJudge(report, boom)
    return llm, judge_craft(llm, Draft(episode_number=3, prose=prose),
                            genre_profile(), _canon(), VoiceBible(spec="단문 위주"))


def test_a_plot_finding_carries_its_evidence():
    _, vs = _judge(CraftReportDraft(findings=[CraftFindingDraft(
        dimension="plot", problem="갈등이 우연으로 해결됨",
        evidence="때마침 관리가 쓰러졌다", severity="major")]))
    assert vs[0].rule == RULE_PLOT
    assert "우연" in vs[0].evidence and "때마침" in vs[0].evidence


def test_findings_are_never_blockers_however_the_model_labels_them():
    """The cap is the point: a subjective judge must not halt an unattended run."""
    _, vs = _judge(CraftReportDraft(findings=[
        CraftFindingDraft(dimension="plot", problem="p", evidence="e", severity="blocker"),
        CraftFindingDraft(dimension="genre", problem="p", evidence="e", severity="치명적")]))
    assert {v.severity for v in vs} == {"major"}
    assert not any(v.severity == "blocker" for v in vs)


def test_a_claim_without_evidence_is_dropped():
    """Craft without a citation is an opinion, and opinions must not reach the
    reviser as instructions."""
    _, vs = _judge(CraftReportDraft(findings=[
        CraftFindingDraft(dimension="plot", problem="뭔가 별로임", evidence="", severity="major")]))
    assert vs == []


def test_an_unknown_dimension_is_dropped():
    _, vs = _judge(CraftReportDraft(findings=[
        CraftFindingDraft(dimension="맞춤법", problem="p", evidence="e", severity="major")]))
    assert vs == []


def test_a_judge_failure_is_silent_rather_than_fatal():
    _, vs = _judge(boom=True)
    assert vs == []


def test_a_clean_episode_produces_nothing():
    _, vs = _judge(CraftReportDraft(findings=[]))
    assert vs == []


def test_major_findings_sort_before_minor():
    _, vs = _judge(CraftReportDraft(findings=[
        CraftFindingDraft(dimension="genre", problem="p", evidence="e", severity="minor"),
        CraftFindingDraft(dimension="character", problem="p", evidence="e", severity="major")]))
    assert [v.severity for v in vs] == ["major", "minor"]


def test_the_judge_sees_the_voice_cards_and_genre_but_not_the_plan():
    """Invariant #3 at context level: shown what the episode was TRYING to do,
    a judge grades the attempt instead of the result."""
    llm, _ = _judge(prose="본문 내용입니다.")
    sent = llm.prompts[-1]
    assert "봉출" in sent and "사무적 존댓말" in sent      # voice reaches it
    assert "접점 부식" in sent                            # exemplar line too
    assert "본문 내용입니다." in sent
    assert "비트시트" not in sent and "the_one_progression" not in sent


def test_every_craft_rule_explains_itself():
    from novel_agent.style import rule_meta
    for r in (RULE_PLOT, RULE_CHARACTER, RULE_GENRE):
        m = rule_meta(r)
        assert m and m.why and m.fix, r
