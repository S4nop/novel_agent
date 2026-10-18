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
    DeviationDraft,
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


def _bs_draft(seed_due=10, **kw):
    base = dict(
        opening_hook="hook", the_one_progression="prog", closing_cliffhanger="cliff",
        entities_present=["홍길동"],
        beats=[BeatDraft(text="b1", beat_type="payoff"),
               BeatDraft(text="b2", beat_type="이상한값")],
        seeds_to_plant=[SeedDraft(description="정체", magnitude="major", due_by_ep=seed_due),
                        SeedDraft(description="소문", magnitude="minor", due_by_ep=0)],
    )
    return BeatSheetDraft(**{**base, **kw})


def _plan(llm, rhythm=None, foreshadow=None, episode=1, extra_directive="",
          is_final=False, total_episodes=None):
    ns = north_star()
    return plan_episode(
        llm, episode_number=episode, profile=genre_profile(), north_star=ns,
        canon=init_canon_and_voice(ScriptedLLM(_canon_draft()), "i", genre_profile(), ns)[0],
        arc_map=seed_arc_map(llm, ns), rhythm=rhythm or RhythmState(),
        foreshadow=foreshadow or ForeshadowLedger(), summary=Summary(),
        extra_directive=extra_directive, is_final=is_final,
        total_episodes=total_episodes,
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


def test_a_bracketed_seed_id_is_resolved_not_dropped():
    """Regression: the prompt renders "- [seed-0001] …", so a model copying what
    it sees returns the bracketed token — bare membership silently dropped it,
    producing the exact "떡밥 left open" outcome the filter was meant to prevent."""
    from novel_agent.artifacts import PlannedSeed

    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="x", description="사라진 호패",
                                 due_by_ep=3), episode=1)
    for written in (f"[{seed.seed_id}]", f"  {seed.seed_id} ",
                    seed.seed_id.upper(), f"{seed.seed_id} (사라진 호패)"):
        draft = _bs_draft()
        draft.seeds_to_pay = [written]
        bs = _plan(ScriptedLLM(draft), foreshadow=led, episode=3)
        assert bs.seeds_to_pay == [seed.seed_id], written


def test_a_major_seed_without_a_deadline_gets_one_so_it_can_ever_be_paid():
    """Regression: due() skips due_by_ep=None, so such a seed's id was never
    shown to the planner, while unpaid_major() counted it forever —
    completion_ready() could never become true for the life of the run."""
    from novel_agent.artifacts import SeedMagnitude
    from novel_agent.schemas import SeedDraft

    draft = _bs_draft()
    draft.seeds_to_plant = [SeedDraft(proposed_seed_id="p1", description="흑막",
                                      magnitude="major", due_by_ep=0)]
    bs = _plan(ScriptedLLM(draft), episode=1)
    major = bs.seeds_to_plant[0]
    assert major.magnitude is SeedMagnitude.MAJOR
    assert major.due_by_ep is not None and major.due_by_ep > 1


def test_episode_one_is_told_to_ground_the_reader():
    """Reader feedback: 1화 read like jumping into the middle of a novel. The
    planner prompt, restraint rules and drafter rules are all tuned for
    mid-serial, where the reader already has the world — nothing knew that
    episode 1 has a different job."""
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, episode=1)
    sent = llm.prompts[-1]
    assert "1화" in sent and "처음 봅니다" in sent
    assert "중간처럼 시작하지 마세요" in sent


def test_the_orientation_duty_fades_after_the_opening_episodes():
    """A serial that keeps re-establishing its world never gets going."""
    from novel_agent.nodes import opening_directive

    assert opening_directive(1) and opening_directive(2)
    assert opening_directive(3) == "" and opening_directive(40) == ""


def test_orientation_does_not_displace_the_pacing_directive():
    """Both must reach the planner — one is about the reader, one about rhythm."""
    llm = ScriptedLLM(_bs_draft())
    starved = RhythmState(max_consecutive_frustration=1, target_catharsis_cadence=99,
                          frustration_debt=5)
    _plan(llm, rhythm=starved, episode=1)
    sent = llm.prompts[-1]
    assert "처음 봅니다" in sent and "사이다" in sent


# ── 떡밥 심기 / 수렴 (검수 2, 10, 5) ─────────────────────────────────────────

def test_the_planner_is_told_to_plant_seeds():
    """No prompt ever asked for seeds_to_plant, so across every store on disk
    the ledger had minted zero seeds — next_seq was still 1. Everything
    downstream (due(), unpaid_major(), completion_ready()) was therefore
    vacuous and the driver declared 완결 for a story it had tracked no threads
    in."""
    llm = ScriptedLLM(_bs_draft())
    _plan(llm)
    assert "seeds_to_plant" in llm.prompts[-1]


def test_a_converging_run_is_shown_the_id_of_a_major_seed_it_must_pay():
    """The convergence directive calls a MAJOR payoff mandatory, but due()
    filters by deadline and a seed planted late has a due_by_ep past the
    target — so the id was never rendered and the payoff was undeclarable."""
    from novel_agent.artifacts import PlannedSeed, SeedMagnitude

    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="x", description="검왕의 정체",
                                 magnitude=SeedMagnitude.MAJOR, due_by_ep=34), episode=25)
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, foreshadow=led, episode=28, extra_directive="[수렴 구간] 회수하세요")
    assert f"[{seed.seed_id}]" in llm.prompts[-1]


def test_a_seed_whose_deadline_has_not_arrived_stays_hidden_mid_run():
    """Showing every open seed every episode would nag the planner into paying
    threads off early — the block is a deadline list, not an inventory."""
    from novel_agent.artifacts import PlannedSeed, SeedMagnitude

    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="x", description="검왕의 정체",
                                 magnitude=SeedMagnitude.MAJOR, due_by_ep=34), episode=25)
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, foreshadow=led, episode=28)
    assert f"[{seed.seed_id}]" not in llm.prompts[-1]


def test_the_final_episode_is_not_told_to_stop_mid_beat():
    """The 완결 directive orders everything resolved while the static cut rule
    in the same template demands an unresolved stop. Two contradictory
    instructions landed in one rendered prompt on the target episode."""
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, is_final=True)
    assert "마지막 비트는 진행 중인 상태로 끊습니다" not in llm.prompts[-1]


def test_a_normal_episode_is_still_told_where_to_cut():
    llm = ScriptedLLM(_bs_draft())
    _plan(llm)
    assert "마지막 비트는 진행 중인 상태로 끊습니다" in llm.prompts[-1]


def test_the_planner_is_shown_a_seed_ripening_for_the_next_episode():
    from novel_agent.artifacts import PlannedSeed

    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="x", description="사라진 호패",
                                 due_by_ep=3), episode=1)
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, foreshadow=led, episode=2)
    assert f"[{seed.seed_id}]" in llm.prompts[-1]


def test_the_planner_is_told_how_long_the_serial_is():
    """Without a denominator the model was asked to name a payoff deadline with
    no idea whether the story runs 10 episodes or 200 — and for episodes 1-24
    of a 30-episode run, nothing in the prompt carried the number."""
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, episode=3, total_episodes=30)
    assert "30" in llm.prompts[-1]


def test_a_major_seed_without_a_deadline_is_anchored_on_the_serial_not_the_cadence():
    """The fallback was episode + catharsis_cadence*3, so the 사이다 rhythm
    decided how long the story's spine could stay open: a 30화 serial with
    cadence 2 gave its premise-level thread a 7화 deadline."""
    llm = ScriptedLLM(_bs_draft(seed_due=0))
    bs = _plan(llm, episode=1, total_episodes=30)
    major = next(s for s in bs.seeds_to_plant if s.magnitude is SeedMagnitude.MAJOR)
    assert major.due_by_ep == 30


def test_a_seed_due_before_it_is_planted_is_pushed_past_the_current_episode():
    """due() would surface it as already overdue on the episode it appears."""
    llm = ScriptedLLM(_bs_draft(seed_due=2))
    bs = _plan(llm, episode=5, total_episodes=30)
    assert all(s.due_by_ep is None or s.due_by_ep > 5 for s in bs.seeds_to_plant)


# ── per-episode directions (coworker feedback 3-2) ──────────────────────────
def _directions_draft(n=3):
    from novel_agent.schemas import DirectionDraft, DirectionsDraft
    return DirectionsDraft(options=[
        DirectionDraft(title=f"방향{i}", synopsis=f"줄거리{i}", key_scene=f"장면{i}",
                       cut_point=f"절단{i}") for i in range(1, n + 1)])


def _propose(llm, episode=3, summary=None, foreshadow=None):
    from novel_agent.nodes import propose_directions
    ns = north_star(central_twist="길동은 관아가 심은 미끼였다")
    return propose_directions(
        llm, episode_number=episode, profile=genre_profile(), north_star=ns,
        canon=init_canon_and_voice(ScriptedLLM(_canon_draft()), "i", genre_profile(), ns)[0],
        arc_map=seed_arc_map(llm, ns), rhythm=RhythmState(),
        foreshadow=foreshadow or ForeshadowLedger(),
        summary=summary or Summary(story_so_far="1화: 첫 승소\n2화: 보복이 시작된다"))


def test_three_directions_are_proposed_for_the_author_to_choose_from():
    opts = _propose(ScriptedLLM(_directions_draft(3)))
    assert [o.title for o in opts] == ["방향1", "방향2", "방향3"]
    assert opts[0].synopsis and opts[0].key_scene and opts[0].cut_point


def test_extra_directions_beyond_three_are_dropped():
    assert len(_propose(ScriptedLLM(_directions_draft(5)))) == 3


def test_directions_are_drawn_from_what_the_planner_would_see():
    """A direction the planner cannot honour — off the arc, ignoring what the
    story already did, blind to an open thread — is a choice that will not
    survive into the episode."""
    from novel_agent.artifacts import PlannedSeed

    led = ForeshadowLedger()
    led.plant(PlannedSeed(proposed_seed_id="x", description="사라진 호패", due_by_ep=3), episode=1)
    llm = ScriptedLLM(_directions_draft())
    _propose(llm, episode=3, foreshadow=led)
    sent = llm.prompts[-1]
    assert "2화: 보복이 시작된다" in sent          # story so far
    assert "길동은 관아가 심은 미끼였다" in sent     # central twist
    assert "사라진 호패" in sent                    # a 떡밥 due now
    assert "3화" in sent


def test_an_empty_proposal_is_an_error_not_a_silent_blank():
    with pytest.raises(ValueError):
        _propose(ScriptedLLM(_directions_draft(0)))


def test_the_chosen_direction_binds_the_episode_plan():
    llm = ScriptedLLM(_bs_draft())
    ns = north_star()
    plan_episode(
        llm, episode_number=2, profile=genre_profile(), north_star=ns,
        canon=init_canon_and_voice(ScriptedLLM(_canon_draft()), "i", genre_profile(), ns)[0],
        arc_map=seed_arc_map(llm, ns), rhythm=RhythmState(), foreshadow=ForeshadowLedger(),
        summary=Summary(), chosen_direction="덕배가 먼저 배신한다 — 카심은 그걸 모른 채 변론한다")
    sent = llm.prompts[-1]
    assert "작가가 고른 이번 화 방향" in sent
    assert "덕배가 먼저 배신한다" in sent


def test_without_a_chosen_direction_the_planner_is_free():
    llm = ScriptedLLM(_bs_draft())
    _plan(llm, episode=2)
    assert "덕배가 먼저 배신한다" not in llm.prompts[-1]


# ── intended deviations (의도된 예외) ───────────────────────────────────────
def _dev(**kw):
    base = dict(character="홍길동", setting="허참 하며 말끝을 흐린다", reason_kind="감정",
                reason="첫 패배의 충격으로 말버릇도 나오지 않는다")
    return DeviationDraft(**{**base, **kw})


def test_the_planner_sees_the_habits_it_would_be_breaking():
    llm = ScriptedLLM(_bs_draft())
    _plan(llm)
    sent = llm.prompts[-1]
    assert "큰 키" in sent and "허참" in sent and "반말" in sent and "능청" in sent
    assert "존댓말" in sent       # a supporting character's pattern too


def test_a_declared_deviation_reaches_the_beat_sheet():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[_dev()])))
    assert [(d.character, d.setting, d.reason_kind, d.reason) for d in bs.deviations] == [
        ("홍길동", "허참 하며 말끝을 흐린다", "감정", "첫 패배의 충격으로 말버릇도 나오지 않는다")]


def test_a_deviation_missing_its_reason_is_dropped_rather_than_excused():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[
        _dev(reason="  "), _dev(character=""), _dev(setting=""), _dev()])))
    assert len(bs.deviations) == 1


def test_an_unknown_reason_kind_is_normalised_to_other():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[_dev(reason_kind="그냥")])))
    assert bs.deviations[0].reason_kind == "기타"


def test_only_two_deviations_are_excused_per_episode():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[
        _dev(setting=f"습관{i}") for i in range(3)])))
    assert [d.setting for d in bs.deviations] == ["습관0", "습관1"]


def test_a_clue_deviation_must_be_paid_off_so_it_is_no_free_pass():
    bs = _plan(ScriptedLLM(_bs_draft(seed_due=10, deviations=[_dev(reason_kind="떡밥")])),
               episode=3)
    seed = next(s for s in bs.seeds_to_plant if s.description.startswith("의도된 예외"))
    assert seed.description == ("의도된 예외: 홍길동 — 허참 하며 말끝을 흐린다: "
                                "첫 패배의 충격으로 말버릇도 나오지 않는다")
    assert seed.magnitude is SeedMagnitude.MINOR
    assert seed.due_by_ep == 15          # the end of the current arc


def test_a_clue_deviation_past_the_last_arc_is_due_three_episodes_later():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[_dev(reason_kind="떡밥")])), episode=20)
    seed = next(s for s in bs.seeds_to_plant if s.description.startswith("의도된 예외"))
    assert seed.due_by_ep == 23


def test_a_clue_deviation_is_never_due_after_the_serial_ends():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[_dev(reason_kind="떡밥")])),
               episode=19, total_episodes=20)
    seed = next(s for s in bs.seeds_to_plant if s.description.startswith("의도된 예외"))
    assert seed.due_by_ep == 20


def test_a_clue_deviation_in_the_final_episode_is_not_excused_as_a_clue():
    # Nothing can pay a 떡밥 after the last episode, so "떡밥이라서" would be a free pass.
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[_dev(reason_kind="떡밥")])),
               episode=20, is_final=True, total_episodes=20)
    assert [d.reason_kind for d in bs.deviations] == ["기타"]
    assert not any(s.description.startswith("의도된 예외") for s in bs.seeds_to_plant)


def test_only_the_first_two_clue_deviations_plant_distinct_seeds():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[
        _dev(reason_kind="떡밥", setting=f"습관{i}") for i in range(3)])))
    seeds = [s for s in bs.seeds_to_plant if s.description.startswith("의도된 예외")]
    assert [s.description.split(" — ")[1].split(":")[0] for s in seeds] == ["습관0", "습관1"]
    assert len({s.proposed_seed_id for s in seeds}) == 2


def test_a_deviation_reason_spanning_lines_is_kept_on_one_line():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[_dev(reason="첫줄\n  둘째줄")])))
    assert bs.deviations[0].reason == "첫줄 둘째줄"


def test_an_emotional_deviation_plants_no_seed():
    bs = _plan(ScriptedLLM(_bs_draft(deviations=[_dev(reason_kind="감정")])))
    assert not any(s.description.startswith("의도된 예외") for s in bs.seeds_to_plant)


def test_a_clue_deviation_is_not_planted_twice_when_the_planner_already_planted_it():
    desc = ("의도된 예외: 홍길동 — 허참 하며 말끝을 흐린다: "
            "첫 패배의 충격으로 말버릇도 나오지 않는다")
    bs = _plan(ScriptedLLM(_bs_draft(
        seeds_to_plant=[SeedDraft(description=desc, magnitude="minor", due_by_ep=0)],
        deviations=[_dev(reason_kind="떡밥")])))
    assert [s.description for s in bs.seeds_to_plant].count(desc) == 1
