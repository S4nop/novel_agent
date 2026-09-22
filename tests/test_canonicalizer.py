"""Behavior tests for the write-path commit step.

The bug these guard: rhythm debt and foreshadow deadlines were loaded every
episode and never saved, so pacing state silently reset each time. For an
unattended multi-episode run that is fatal — nothing accumulates.
"""
from novel_agent.artifacts import Beat, BeatSheet, BeatType, Draft, PlannedSeed, SeedMagnitude
from novel_agent.canon_store import CanonStore
from novel_agent.canonicalizer import commit_episode_state, episode_hash

from .factories import canon, genre_profile, north_star, voice_bible

F, P, S = BeatType.FRUSTRATION, BeatType.PAYOFF, BeatType.SETUP


def _store(tmp_path) -> CanonStore:
    s = CanonStore(tmp_path / "novel")
    s.initialize(genre_profile=genre_profile(), north_star=north_star(),
                 canon=canon(), voice_bible=voice_bible())
    return s


def _beats(n=1, beats=None, plant=(), pay=()):
    return BeatSheet(
        episode_number=n,
        opening_hook=f"{n}화 훅",
        the_one_progression=f"{n}화 진전",
        beats=[Beat(text="b", beat_type=t) for t in (beats or [S, P])],
        seeds_to_plant=[PlannedSeed(proposed_seed_id=f"p{i}", description=d,
                                    magnitude=SeedMagnitude.MAJOR, due_by_ep=n + 3)
                        for i, d in enumerate(plant)],
        seeds_to_pay=list(pay),
        closing_cliffhanger=f"{n}화 절단",
    )


def test_frustration_debt_accumulates_across_episodes(tmp_path):
    """The core regression: state must survive to the next episode."""
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="1화"), _beats(1, [F, F]))
    assert s.load_rhythm().frustration_debt == 2

    commit_episode_state(s, Draft(episode_number=2, prose="2화"), _beats(2, [F]))
    assert s.load_rhythm().frustration_debt == 3      # accumulated, not reset


def test_payoff_beat_pays_down_persisted_debt(tmp_path):
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="1화"), _beats(1, [F, F, F]))
    assert s.load_rhythm().blocks_setup_heavy_episode() is True

    commit_episode_state(s, Draft(episode_number=2, prose="2화"), _beats(2, [P]))
    assert s.load_rhythm().blocks_setup_heavy_episode() is False


def test_planted_seeds_persist_and_become_due_later(tmp_path):
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="1화"),
                         _beats(1, plant=["흑막의 정체"]))
    ledger = s.load_foreshadow()
    assert len(ledger.seeds) == 1
    seed = next(iter(ledger.seeds.values()))
    assert seed.planted_ep == 1 and seed.due_by_ep == 4
    assert ledger.due(4) == [seed]           # the planner will now see it as overdue


def test_paying_a_seed_persists_and_unblocks_completion(tmp_path):
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="1화"), _beats(1, plant=["떡밥"]))
    seed_id = next(iter(s.load_foreshadow().seeds))
    assert s.load_foreshadow().completion_ready() is False

    commit_episode_state(s, Draft(episode_number=2, prose="2화"),
                         _beats(2, pay=[seed_id]))
    assert s.load_foreshadow().completion_ready() is True


def test_summary_accumulates_so_the_next_episode_has_context(tmp_path):
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="1화"), _beats(1))
    commit_episode_state(s, Draft(episode_number=2, prose="2화"), _beats(2))
    story = s.load_summary().story_so_far
    assert "1화 진전" in story and "2화 진전" in story


def test_episode_is_persisted_with_a_content_hash_and_beat_tags(tmp_path):
    s = _store(tmp_path)
    rec = commit_episode_state(s, Draft(episode_number=1, prose="본문입니다"),
                               _beats(1, [S, P]))
    assert s.load_episode(1).prose == "본문입니다"
    assert rec.accepted_draft_hash == episode_hash("본문입니다")
    assert rec.beat_tags == [S, P]
    assert s.latest_episode_number() == 1


def test_unknown_seed_id_in_pay_list_is_ignored_not_fatal(tmp_path):
    """An unattended run must not crash because the planner hallucinated an id."""
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="1화"),
                         _beats(1, pay=["seed-does-not-exist"]))
    assert s.load_foreshadow().seeds == {}


# ── re-committing an episode (console retry, crash-resume) ───────────────────
# The console's 화 field is initialised to 1 and never written back, so pressing
# 집필 a second time re-commits episode 1. Every ledger folded it in twice.

def test_committing_the_same_episode_twice_leaves_one_episodes_worth_of_state(tmp_path):
    s = _store(tmp_path)
    beats = _beats(1, [F, F], plant=["떡밥A"])
    draft = Draft(episode_number=1, prose="1화")

    commit_episode_state(s, draft, beats)
    commit_episode_state(s, draft, beats)

    assert s.load_rhythm().frustration_debt == 2
    assert s.load_rhythm().beat_log == [[F, F]]
    assert [x.description for x in s.load_foreshadow().seeds.values()] == ["떡밥A"]
    assert s.load_summary().story_so_far == "1화: 1화 진전"


def test_re_drafting_an_episode_replaces_its_contribution(tmp_path):
    """A retry re-plans, so the new take can carry different beats. Its
    predecessor's tags must not linger next to it in the log."""
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="a"), _beats(1, [F, F]))
    commit_episode_state(s, Draft(episode_number=2, prose="b"), _beats(2, [F]))

    commit_episode_state(s, Draft(episode_number=1, prose="a2"), _beats(1, [P]))

    r = s.load_rhythm()
    assert r.beat_log == [[P], [F]]
    assert r.frustration_debt == 1


def test_re_committing_an_episode_does_not_duplicate_its_summary_line(tmp_path):
    """story_so_far is fed into every later plan, so a duplicated line teaches
    the planner the same episode happened twice."""
    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="a"), _beats(1))
    commit_episode_state(s, Draft(episode_number=2, prose="b"), _beats(2))
    commit_episode_state(s, Draft(episode_number=1, prose="a2"), _beats(1))

    assert s.load_summary().story_so_far == "1화: 1화 진전\n2화: 2화 진전"


def test_a_reinforced_seed_records_the_episode_that_touched_it(tmp_path):
    """reinforce() had no caller anywhere in src/, so reinforced_in was
    structurally always empty and SeedStatus.REINFORCED unreachable."""
    from novel_agent.artifacts import SeedStatus

    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="a"), _beats(1, plant=["떡밥A"]))
    sid = next(iter(s.load_foreshadow().seeds))

    b = _beats(2)
    b.seeds_to_reinforce = [sid]
    commit_episode_state(s, Draft(episode_number=2, prose="b"), b)

    seed = s.load_foreshadow().seeds[sid]
    assert seed.reinforced_in == [2]
    assert seed.status is SeedStatus.REINFORCED


def test_a_reinforced_seed_can_still_be_paid_off(tmp_path):
    from novel_agent.artifacts import SeedStatus

    s = _store(tmp_path)
    commit_episode_state(s, Draft(episode_number=1, prose="a"), _beats(1, plant=["떡밥A"]))
    sid = next(iter(s.load_foreshadow().seeds))
    b2 = _beats(2); b2.seeds_to_reinforce = [sid]
    commit_episode_state(s, Draft(episode_number=2, prose="b"), b2)

    commit_episode_state(s, Draft(episode_number=3, prose="c"), _beats(3, pay=[sid]))

    assert s.load_foreshadow().seeds[sid].status is SeedStatus.PAID
