"""Behavior tests for the setup/planning nodes — LLM faked at the boundary.

These cover the DTO→artifact mapping, which is where a silent corruption of
canon would come from (the LLM speaks shallow DTOs; the domain uses richer
structures — see schemas.py).
"""
import pytest

from novel_agent.artifacts import BeatType, ContentRating, SeedMagnitude, Summary
from novel_agent.ledgers import ForeshadowLedger, RhythmState
from novel_agent.nodes import (
    infer_genre_profile,
    init_canon_and_voice,
    plan_episode,
    seed_arc_map,
    to_north_star,
)
from novel_agent.schemas import (
    BeatDraft,
    BeatSheetDraft,
    CanonInitDraft,
    CharacterDraft,
    GenreProfileDraft,
    GlossaryDraft,
    NorthStarDraft,
    SeedDraft,
)

from .factories import genre_profile, north_star


class ScriptedLLM:
    """Boundary fake: returns a queued DTO per structured() call."""

    def __init__(self, *returns):
        self.queue = list(returns)
        self.prompts: list[str] = []

    def structured(self, messages, schema):
        self.prompts.append(messages[-1]["content"])
        return self.queue.pop(0)

    def text(self, messages, *, max_tokens=8192):  # pragma: no cover
        raise NotImplementedError


def _gp_draft(**kw):
    base = dict(
        audience="남성향", content_rating="15+", sub_genre="코믹 사극 판타지",
        trope_checklist=["신분 상승", "의적"], pov="3인칭", tense="과거",
        register_baseline="", target_catharsis_cadence=2,
        max_consecutive_frustration_beats=1, forbidden_anti_patterns=["설정 나열"],
        inference_notes="코믹 + 사극 신호",
    )
    return GenreProfileDraft(**{**base, **kw})


def test_genre_inference_maps_rating_and_injects_platform_length():
    profile, notes = infer_genre_profile(ScriptedLLM(_gp_draft()), "네오 조선 코믹")
    assert profile.content_rating is ContentRating.T15
    assert profile.sub_genre == "코믹 사극 판타지"
    # platform-keyed default injected by code, not inferred by the LLM
    assert profile.episode_length_target == 5200
    assert profile.register_baseline == "문어체 서술 + 짧은 문단"
    assert notes == "코믹 + 사극 신호"


def test_genre_inference_never_yields_explicit_rating():
    """Explicit-19+ is out of scope; any mature signal clamps to MATURE."""
    profile, _ = infer_genre_profile(ScriptedLLM(_gp_draft(content_rating="19+ 성인")), "x")
    assert profile.content_rating is ContentRating.MATURE


def test_genre_inference_clamps_nonsense_cadence_to_at_least_one():
    profile, _ = infer_genre_profile(
        ScriptedLLM(_gp_draft(target_catharsis_cadence=0, max_consecutive_frustration_beats=0)), "x"
    )
    assert profile.target_catharsis_cadence == 1
    assert profile.max_consecutive_frustration_beats == 1


def test_north_star_carries_genre_profile_version_stamp():
    """Invariant #10 — a later L0 change must be detectable."""
    gp = genre_profile()
    gp.version = 7
    ns = to_north_star(
        NorthStarDraft(
            title="t", premise="p", core_conflict="c", protagonist_edge="e",
            central_twist="tw", intended_ending="end", episode_engine="eng",
            power_system="ps", hard_rules=["r1"],
        ),
        gp,
    )
    assert ns.genre_profile_version == 7
    assert ns.hard_rules == ["r1"]


def _canon_draft():
    return CanonInitDraft(
        characters=[
            CharacterDraft(
                name="홍길동", is_main_cast=True, immutable_descriptors=["큰 키"],
                speech_register="능청", honorific_pattern="반말", speech_tics=["허참"],
                exemplar_lines=["그래서 뭐."], personality="능글", goals=["신분 타파"],
                secrets=["서얼"], current_location="한양", condition="정상", power_level="하급",
            ),
            CharacterDraft(
                name="포교", is_main_cast=False, immutable_descriptors=[],
                speech_register="딱딱", honorific_pattern="존댓말", speech_tics=[],
                exemplar_lines=[], personality="고집", goals=[], secrets=[],
                current_location="한양", condition="정상", power_level="평범",
            ),
        ],
        hard_world_rules=["신분은 문서로만 증명된다"],
        soft_world_rules=["소문은 빠르다"],
        glossary=[GlossaryDraft(term="Neo-Joseon", canonical_form="네오 조선", notes="")],
        voice_spec="짧은 문장, 능청스러운 리듬",
        voice_exemplars=["문이 열렸다."],
    )


def test_canon_init_flattens_voice_into_character_and_flags_rule_hardness():
    canon, voice = init_canon_and_voice(
        ScriptedLLM(_canon_draft()), "idea", genre_profile(), north_star()
    )
    hero = canon.characters["홍길동"]
    assert hero.is_main_cast is True
    assert hero.voice.honorific_pattern == "반말"
    assert hero.voice.speech_tics == ["허참"]
    assert [c.name for c in canon.main_cast()] == ["홍길동"]      # supporting cast excluded
    assert [r.hard for r in canon.world_rules] == [True, False]
    assert canon.glossary[0].canonical_form == "네오 조선"
    assert voice.spec.startswith("짧은 문장")


def _bs_draft(**kw):
    base = dict(
        opening_hook="hook", the_one_progression="prog", closing_cliffhanger="cliff",
        entities_present=["홍길동"],
        beats=[BeatDraft(text="b1", beat_type="payoff"),
               BeatDraft(text="b2", beat_type="이상한값")],
        seeds_to_plant=[SeedDraft(description="정체", magnitude="major", due_by_ep=10),
                        SeedDraft(description="소문", magnitude="minor", due_by_ep=0)],
    )
    return BeatSheetDraft(**{**base, **kw})


def _plan(llm, rhythm=None, foreshadow=None, episode=1):
    ns = north_star()
    return plan_episode(
        llm, episode_number=episode, profile=genre_profile(), north_star=ns,
        canon=init_canon_and_voice(ScriptedLLM(_canon_draft()), "i", genre_profile(), ns)[0],
        arc_map=seed_arc_map(llm, ns), rhythm=rhythm or RhythmState(),
        foreshadow=foreshadow or ForeshadowLedger(), summary=Summary(),
    )


def test_planned_beats_map_types_and_unknown_type_falls_back_to_setup():
    bs = _plan(ScriptedLLM(_bs_draft()))
    assert bs.beat_types() == [BeatType.PAYOFF, BeatType.SETUP]


def test_planned_seeds_carry_magnitude_and_drop_zero_due_date():
    bs = _plan(ScriptedLLM(_bs_draft()))
    major, minor = bs.seeds_to_plant
    assert (major.magnitude, major.due_by_ep) == (SeedMagnitude.MAJOR, 10)
    assert (minor.magnitude, minor.due_by_ep) == (SeedMagnitude.MINOR, None)
    assert major.proposed_seed_id != minor.proposed_seed_id      # planner proposes, canonicalizer mints


def test_planner_prompt_carries_pacing_directive_when_payoff_is_owed():
    llm = ScriptedLLM(_bs_draft())
    starved = RhythmState(max_consecutive_frustration=1, target_catharsis_cadence=99,
                          frustration_debt=5)
    _plan(llm, rhythm=starved)
    assert "사이다" in llm.prompts[-1]


def test_planner_prompt_lists_overdue_foreshadows():
    from novel_agent.artifacts import PlannedSeed

    led = ForeshadowLedger()
    led.plant(PlannedSeed(proposed_seed_id="x", description="사라진 호패", due_by_ep=3), episode=1)
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, foreshadow=led, episode=3)
    assert "사라진 호패" in llm.prompts[-1]


def test_overdue_foreshadows_are_shown_with_their_id_so_a_payoff_is_declarable():
    """Without the id in the prompt the planner has no handle to pay a 떡밥 with,
    so seeds_to_pay could only ever come back empty."""
    from novel_agent.artifacts import PlannedSeed

    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="x", description="사라진 호패",
                                 due_by_ep=3), episode=1)
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, foreshadow=led, episode=3)
    assert f"[{seed.seed_id}]" in llm.prompts[-1]


def test_a_declared_payoff_reaches_the_beat_sheet():
    from novel_agent.artifacts import PlannedSeed

    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="x", description="사라진 호패",
                                 due_by_ep=3), episode=1)
    draft = _bs_draft()
    draft.seeds_to_pay = [seed.seed_id]
    bs = _plan(ScriptedLLM(draft), foreshadow=led, episode=3)
    assert bs.seeds_to_pay == [seed.seed_id]


def test_a_hallucinated_seed_id_is_dropped_rather_than_carried():
    """An unattended run must not record a payoff against a 떡밥 that never existed."""
    draft = _bs_draft()
    draft.seeds_to_pay = ["seed-that-never-existed"]
    bs = _plan(ScriptedLLM(draft), foreshadow=ForeshadowLedger(), episode=3)
    assert bs.seeds_to_pay == []


def test_planner_inherits_length_and_pov_from_genre_profile():
    bs = _plan(ScriptedLLM(_bs_draft()))
    gp = genre_profile()
    assert bs.length_target == gp.episode_length_target
    assert bs.pov == gp.pov
