"""Renaming a character the author has already built a story around.

A character's name is not one field: in a live store one supporting character's
name appeared in nine files — canon, its snapshot, the premise, the arc plan,
the voice bible, the 떡밥 ledger, the summary and every committed episode. A
rename that only touched the canon key would leave the planner reading the old
name from every other one of them.
"""
import pytest

from novel_agent.artifacts import (
    Arc, ArcMap, CharacterCard, EpisodeRecord, PendingEpisode, PlannedThread,
    Summary, VoiceBible,
)
from novel_agent.canon_edit import RenameError, rename_character
from novel_agent.canon_store import CanonStore

from .factories import beat_sheet, canon, genre_profile, hero, north_star


def _store(tmp_path):
    friend = CharacterCard(name="이현우", relationships={"김현우": "옛 동료"})
    hero_card = hero()
    hero_card.relationships = {"이현우": "의심"}
    s = CanonStore(tmp_path / "novel")
    s.initialize(
        genre_profile=genre_profile(),
        north_star=north_star(premise="김현우는 멸망을 보고 회귀한 헌터다",
                              central_twist="김현우가 바로 멸망의 원인이었다"),
        canon=canon(characters={"김현우": hero_card, "이현우": friend}),
        voice_bible=VoiceBible(spec="김현우의 독백은 짧게", exemplar_passages=[]),
        arc_map=ArcMap(total_episodes=10,
                       arcs=[Arc(goal="김현우가 첫 게이트를 차지한다", start_ep=1, end_ep=10)],
                       threads=[PlannedThread(thread_id="thread-01",
                                              description="김현우의 왼손 흉터의 유래")]))
    s.save_summary(Summary(story_so_far="", current_arc="김현우가 각성한다"))
    return s


def _everything(store) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in store.root.glob("*.json"))


def _commit(store, prose):
    store.commit_episode(EpisodeRecord(episode_number=1, prose=prose, accepted_draft_hash="h"))


# ── before episode 1: rename everywhere ──────────────────────────────────────
def test_a_rename_before_episode_one_reaches_every_setup_artifact(tmp_path):
    s = _store(tmp_path)
    rename_character(s, "김현우", "박철수")

    assert "김현우" not in _everything(s)
    assert "박철수는 멸망을 보고" in s.load_north_star().premise
    assert "박철수가 첫 게이트" in s.load_arc_map().arcs[0].goal
    assert "박철수의 독백" in s.load_voice_bible().spec
    assert "박철수" in s.load_canon().characters


def test_the_renamed_card_and_everyone_who_knows_them_follow(tmp_path):
    s = _store(tmp_path)
    rename_character(s, "김현우", "박철수")
    c = s.load_canon()
    assert c.characters["박철수"].name == "박철수"
    assert c.characters["이현우"].relationships == {"박철수": "옛 동료"}


def test_a_name_inside_a_different_name_is_left_alone(tmp_path):
    """Renaming 현우 must not turn 이현우 into 이철수."""
    s = _store(tmp_path)
    s.save_canon(s.load_canon().model_copy(update={"characters": {
        **s.load_canon().characters, "현우": CharacterCard(name="현우")}}))
    rename_character(s, "현우", "철수")
    assert "이현우" in s.load_canon().characters
    assert "김현우" in s.load_canon().characters


def test_nothing_is_kept_as_an_alias_when_no_episode_used_the_old_name(tmp_path):
    s = _store(tmp_path)
    report = rename_character(s, "김현우", "박철수")
    assert s.load_canon().characters["박철수"].aliases == []
    assert report.committed_episodes == 0


def test_the_reset_point_carries_the_new_name_too(tmp_path):
    """Otherwise a --reset would bring the old name back."""
    s = _store(tmp_path)
    rename_character(s, "김현우", "박철수")
    s.reset_serial()
    assert "박철수" in s.load_canon().characters
    assert "김현우" not in s.load_canon().characters


# ── after episodes exist: published prose is not rewritten ───────────────────
def test_committed_prose_keeps_the_old_name(tmp_path):
    s = _store(tmp_path)
    _commit(s, "김현우는 눈을 떴다.")
    rename_character(s, "김현우", "박철수")
    assert s.load_episode(1).prose == "김현우는 눈을 떴다."


def test_the_old_name_is_kept_as_an_alias_once_an_episode_used_it(tmp_path):
    """Canon has to still recognise the person the earlier episodes name."""
    s = _store(tmp_path)
    _commit(s, "김현우는 눈을 떴다.")
    report = rename_character(s, "김현우", "박철수")
    assert s.load_canon().characters["박철수"].aliases == ["김현우"]
    assert report.committed_episodes == 1
    assert report.warning


def test_a_rename_is_refused_while_an_episode_awaits_approval(tmp_path):
    """That draft was written with the old name and is about to become canon."""
    s = _store(tmp_path)
    s.hold_episode(PendingEpisode(draft=_draft(), beats=beat_sheet(1)))
    with pytest.raises(RenameError, match="승인"):
        rename_character(s, "김현우", "박철수")


# ── what is refused ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("new", ["이현우", "", "   ", "김현우"])
def test_a_name_that_is_taken_empty_or_unchanged_is_refused(tmp_path, new):
    s = _store(tmp_path)
    with pytest.raises(RenameError):
        rename_character(s, "김현우", new)


def test_a_name_another_character_answers_to_is_refused(tmp_path):
    s = _store(tmp_path)
    c = s.load_canon()
    c.characters["이현우"].aliases = ["검왕"]
    s.save_canon(c)
    with pytest.raises(RenameError, match="검왕"):
        rename_character(s, "김현우", "검왕")


def test_an_unknown_character_is_refused(tmp_path):
    with pytest.raises(RenameError):
        rename_character(_store(tmp_path), "없는사람", "박철수")


# ── preview ─────────────────────────────────────────────────────────────────
def test_a_preview_reports_what_would_change_and_writes_nothing(tmp_path):
    s = _store(tmp_path)
    before = _everything(s)
    report = rename_character(s, "김현우", "박철수", dry_run=True)

    assert _everything(s) == before
    files = dict(report.changes)
    assert files["north_star.json"] == 2
    assert files["arc_map.json"] == 2
    assert sum(files.values()) >= 8


def _draft():
    from novel_agent.artifacts import Draft
    return Draft(episode_number=1, prose="김현우는 눈을 떴다.")


def test_no_alias_is_kept_for_a_character_the_committed_episodes_never_named(tmp_path):
    """Seen live: a character added after 1화 was renamed and still got the old
    name kept as an alias and a warning about prose that never mentioned them."""
    s = _store(tmp_path)
    _commit(s, "이현우는 문을 열었다.")
    report = rename_character(s, "김현우", "박철수")
    assert s.load_canon().characters["박철수"].aliases == []
    assert report.warning == ""
