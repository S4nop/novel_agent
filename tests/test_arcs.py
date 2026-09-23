"""The L2 ArcPlanner (DESIGN §L2) — the overall picture that 떡밥 come from.

The bug these guard: seed_arc_map took an `llm` and never called it. It returned
one hardcoded Arc with episode_span="1-15" and the literal payoff "주인공이 처음
으로 판을 뒤집는다", rebuilt every episode and never persisted. The planner saw
two strings from it, so north_star.central_twist never reached the node that
invents 떡밥 — threads were thrown with no picture of where the story goes.
"""
from novel_agent.artifacts import Arc, ArcMap
from novel_agent.nodes import arc_for_episode, plan_arcs
from novel_agent.schemas import ArcDraft, ArcMapDraft, PlannedThreadDraft

from .factories import canon, genre_profile, north_star


class ScriptedLLM:
    def __init__(self, obj):
        self.obj, self.prompts = obj, []

    def structured(self, messages, schema):
        self.prompts.append(messages[-1]["content"])
        return self.obj

    def text(self, messages, *, max_tokens=8192):  # pragma: no cover
        raise NotImplementedError


def _draft(spans=((1, 10), (11, 20), (21, 30)), threads=(("정체", "major", 3),)):
    return ArcMapDraft(
        arcs=[ArcDraft(goal=f"목표{i}", antagonist="적", climax=f"클라이맥스{i}",
                       payoff=f"보상{i}", ending_hook="훅", start_ep=a, end_ep=b)
              for i, (a, b) in enumerate(spans, 1)],
        threads=[PlannedThreadDraft(description=d, magnitude=m, pays_off_in_arc=n)
                 for d, m, n in threads],
    )


def _plan(llm, total=30):
    return plan_arcs(llm, north_star=north_star(), profile=genre_profile(),
                     canon=canon(), total_episodes=total)


# ── the plan covers the serial ───────────────────────────────────────────────
def test_the_arc_plan_covers_every_episode_of_the_serial():
    am = _plan(ScriptedLLM(_draft()))
    covered = [e for a in am.arcs for e in range(a.start_ep, a.end_ep + 1)]
    assert covered == list(range(1, 31))


def test_a_gap_left_by_the_model_is_repaired_rather_than_failing_the_run():
    """A hole means some episode has no arc, and arc_for_episode would return
    None for it — the planner would silently lose its picture mid-serial."""
    am = _plan(ScriptedLLM(_draft(spans=((1, 10), (15, 30)))))
    covered = [e for a in am.arcs for e in range(a.start_ep, a.end_ep + 1)]
    assert covered == list(range(1, 31))


def test_an_overshooting_last_arc_is_trimmed_to_the_serial_length():
    am = _plan(ScriptedLLM(_draft(spans=((1, 10), (11, 45)))))
    assert am.arcs[-1].end_ep == 30


def test_the_plan_records_the_length_it_was_built_for():
    assert _plan(ScriptedLLM(_draft()), total=30).total_episodes == 30


# ── what the planner is told ─────────────────────────────────────────────────
def test_the_arc_planner_is_shown_the_central_twist_every_thread_must_feed():
    llm = ScriptedLLM(_draft())
    _plan(llm)
    assert north_star().central_twist in llm.prompts[-1]


def test_only_the_first_two_arcs_are_asked_for_in_detail():
    """DESIGN §L2: current + next detailed, the rest loglines. Detailing arc 5
    at episode 0 maximises the staleness that makes mid-run re-planning a
    problem, and the author has to read whatever is produced."""
    am = _plan(ScriptedLLM(_draft()))
    assert [a.detailed for a in am.arcs] == [True, True, False]


# ── threads carry an id the planner can name ─────────────────────────────────
def test_every_planned_thread_gets_a_stable_id():
    """Without one, the only channel from 'the plan proposed this thread' back to
    'the planner planted it' is fuzzy matching on LLM-rewritten Korean prose."""
    am = _plan(ScriptedLLM(_draft(threads=(("정체", "major", 3), ("배신", "minor", 2)))))
    assert [t.thread_id for t in am.threads] == ["thread-01", "thread-02"]


def test_a_thread_is_bound_to_the_arc_that_pays_it_off():
    am = _plan(ScriptedLLM(_draft(threads=(("정체", "major", 3),))))
    assert am.threads[0].pays_off_in_arc == 3


def test_a_thread_pointing_past_the_last_arc_is_pulled_back_to_it():
    am = _plan(ScriptedLLM(_draft(threads=(("정체", "major", 9),))))
    assert am.threads[0].pays_off_in_arc == 3


# ── the active arc is derived, never stored ──────────────────────────────────
def test_the_arc_for_an_episode_is_read_off_the_spans():
    """Persisting an 'active' status would be a second source of truth that goes
    stale whenever the driver retries the same episode."""
    am = ArcMap(arcs=[Arc(goal="1부", start_ep=1, end_ep=10),
                      Arc(goal="2부", start_ep=11, end_ep=20)], total_episodes=20)
    assert arc_for_episode(am, 1).goal == "1부"
    assert arc_for_episode(am, 10).goal == "1부"
    assert arc_for_episode(am, 11).goal == "2부"


def test_an_episode_past_the_plan_falls_to_the_last_arc():
    """A run continued past its planned length must not lose its picture."""
    am = ArcMap(arcs=[Arc(goal="1부", start_ep=1, end_ep=10)], total_episodes=10)
    assert arc_for_episode(am, 44).goal == "1부"


def test_an_empty_plan_has_no_arc_for_any_episode():
    assert arc_for_episode(ArcMap(), 1) is None


# ── persistence: the plan is setup, not accumulation ─────────────────────────
def _store(tmp_path, arc_map=None):
    from novel_agent.canon_store import CanonStore
    from .factories import voice_bible
    s = CanonStore(tmp_path / "novel")
    s.initialize(genre_profile=genre_profile(), north_star=north_star(),
                 canon=canon(), voice_bible=voice_bible(), arc_map=arc_map)
    return s


def test_a_store_built_without_a_plan_reports_no_plan_rather_than_raising(tmp_path):
    """All 18 stores on disk predate this, and _read is a bare read_text — a
    raising loader inside commit_episode_state would fire after the gate passed
    and after the ledgers were written, leaving a half-committed episode."""
    assert _store(tmp_path).load_arc_map() is None


def test_the_plan_survives_a_round_trip(tmp_path):
    am = _plan(ScriptedLLM(_draft()))
    reloaded = _store(tmp_path, arc_map=am).load_arc_map()
    assert reloaded == am


def test_a_reset_keeps_the_plan_but_forgets_which_threads_were_planted(tmp_path):
    """The plan is the author's setup. Which of its threads the serial got
    around to planting is accumulation, and must rewind with the rest."""
    am = _plan(ScriptedLLM(_draft()))
    s = _store(tmp_path, arc_map=am)
    live = s.load_arc_map()
    live.threads[0].planted_as = "seed-0001"
    s.save_arc_map(live)

    s.reset_serial()

    after = s.load_arc_map()
    assert [a.goal for a in after.arcs] == [a.goal for a in am.arcs]
    assert [t.planted_as for t in after.threads] == [""]


# ── what reaches the EpisodePlanner ──────────────────────────────────────────
def _plan_ep(llm, arc_map, episode=14, total=30):
    from novel_agent.artifacts import Summary
    from novel_agent.ledgers import ForeshadowLedger, RhythmState
    from novel_agent.nodes import plan_episode
    return plan_episode(
        llm, episode_number=episode, profile=genre_profile(), north_star=north_star(),
        canon=canon(), arc_map=arc_map, rhythm=RhythmState(),
        foreshadow=ForeshadowLedger(), summary=Summary(), total_episodes=total)


def _beats_llm():
    from novel_agent.schemas import BeatDraft, BeatSheetDraft
    return ScriptedLLM(BeatSheetDraft(
        opening_hook="h", the_one_progression="p", closing_cliffhanger="c",
        entities_present=[], beats=[BeatDraft(text="b", beat_type="payoff")],
        seeds_to_plant=[]))


def test_the_episode_planner_is_told_which_arc_it_is_writing_inside():
    am = _plan(ScriptedLLM(_draft()))
    llm = _beats_llm()
    _plan_ep(llm, am, episode=14)
    sent = llm.prompts[-1]
    assert "2부" in sent and "11-20화 중 14화" in sent
    assert am.arcs[1].climax in sent


def test_the_episode_planner_is_shown_the_central_twist():
    """Every major 떡밥 is supposed to feed it, and the node that invents them
    could not see it."""
    from novel_agent.artifacts import Summary
    from novel_agent.ledgers import ForeshadowLedger, RhythmState
    from novel_agent.nodes import plan_episode

    ns = north_star(central_twist="길동은 사실 관아가 심은 미끼였다")
    llm = _beats_llm()
    plan_episode(llm, episode_number=3, profile=genre_profile(), north_star=ns,
                 canon=canon(), arc_map=_plan(ScriptedLLM(_draft())),
                 rhythm=RhythmState(), foreshadow=ForeshadowLedger(),
                 summary=Summary(), total_episodes=30)
    assert ns.central_twist in llm.prompts[-1]


def test_an_episode_still_plans_when_the_store_has_no_arc_plan():
    """18 stores on disk predate L2 and must keep working."""
    from novel_agent.nodes import effective_arc_map, seed_arc_map
    llm = _beats_llm()
    am = effective_arc_map(None, llm, north_star())
    assert am.arcs == seed_arc_map(llm, north_star()).arcs
    _plan_ep(llm, am)                       # renders without raising


def test_a_stored_plan_wins_over_the_fallback():
    from novel_agent.nodes import effective_arc_map
    am = _plan(ScriptedLLM(_draft()))
    assert effective_arc_map(am, _beats_llm(), north_star()) is am


# ── the serial length: flag vs plan ──────────────────────────────────────────
def test_the_stored_plan_supplies_the_length_when_the_flag_is_omitted():
    from novel_agent.driver import resolve_target_episodes
    am = _plan(ScriptedLLM(_draft()), total=30)
    assert resolve_target_episodes(None, am) == (30, "")


def test_an_explicit_flag_wins_but_says_so_when_it_contradicts_the_plan():
    """Running a 60-episode length against arcs planned for 30 is not something
    to do silently: the loop, is_final and the convergence window all move."""
    from novel_agent.driver import resolve_target_episodes
    am = _plan(ScriptedLLM(_draft()), total=30)
    got, warning = resolve_target_episodes(60, am)
    assert got == 60
    assert "30" in warning and "60" in warning


def test_no_flag_and_no_plan_falls_back_to_the_default_length():
    from novel_agent.driver import RunConfig, resolve_target_episodes
    assert resolve_target_episodes(None, None) == (RunConfig().target_episodes, "")
