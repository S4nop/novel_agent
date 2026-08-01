"""Behavior tests for the ContextPackBuilder (DESIGN §1, §3)."""
from novel_agent.artifacts import CharacterCard, EpisodeRecord, KnownFact
from novel_agent.context_pack import ContextPackBuilder, TokenBudget
from novel_agent.ledgers import ForeshadowLedger, RhythmState
from novel_agent.artifacts import PlannedSeed, SeedMagnitude, BeatType

from .factories import beat_sheet, canon, genre_profile, north_star, summary, voice_bible


def _build(builder=None, *, current_episode=1, previous_episode=None,
           foreshadow=None, rhythm=None, the_canon=None, summ=None):
    builder = builder or ContextPackBuilder()
    return builder.build(
        genre_profile=genre_profile(),
        north_star=north_star(),
        voice_bible=voice_bible(),
        canon=the_canon or canon(),
        beat_sheet=beat_sheet(current_episode),
        foreshadow=foreshadow or ForeshadowLedger(),
        rhythm=rhythm or RhythmState(),
        summary=summ or summary(),
        current_episode=current_episode,
        previous_episode=previous_episode,
    )


def test_system_prompt_instructs_native_korean():
    pack = _build()
    assert "한국어로만" in pack.system
    assert "번역투" in pack.system


def test_episode_one_builds_without_previous_episode_or_summary():
    pack = _build(current_episode=1, previous_episode=None)
    assert "직전" not in pack.volatile_suffix          # no previous-episode block
    assert "지금까지의 이야기" not in pack.volatile_suffix  # empty summary omitted
    assert "1화 집필" in pack.volatile_suffix


def test_prefix_is_byte_stable_when_only_volatile_state_changes():
    before = _build().cached_prefix

    # mutate only things that must NOT reach the cache-stable prefix:
    evolved = canon()
    evolved.characters["김현우"].known_facts.append(KnownFact(fact="새 사실", learned_episode=2))
    evolved.characters["김현우"].current_location = "다른 곳"
    evolved.characters["엑스트라"] = CharacterCard(name="엑스트라", is_main_cast=False)

    after = _build(
        the_canon=evolved,
        current_episode=7,
        previous_episode=EpisodeRecord(episode_number=6, prose="이전 화 본문", accepted_draft_hash="h"),
        summ=summary(story_so_far="많은 일이 있었다"),
        rhythm=RhythmState(frustration_debt=5),
    ).cached_prefix

    assert after == before


def test_due_foreshadow_appears_in_suffix():
    led = ForeshadowLedger()
    led.plant(PlannedSeed(proposed_seed_id="p", description="정체불명의 표식", due_by_ep=3), episode=1)
    pack = _build(current_episode=3, foreshadow=led)
    assert "회수해야 할 떡밥" in pack.volatile_suffix
    assert "정체불명의 표식" in pack.volatile_suffix


def test_pacing_directive_reflects_frustration_debt():
    calm = _build(rhythm=RhythmState()).volatile_suffix
    assert "양호" in calm
    tense = _build(rhythm=RhythmState(frustration_debt=9, max_consecutive_frustration=1)).volatile_suffix
    assert "사이다" in tense


def test_oversized_previous_episode_is_trimmed_to_budget_and_prefix_preserved():
    budget = TokenBudget(prefix_max=50, suffix_max=400, total_max=100_000)
    builder = ContextPackBuilder(budget=budget)          # default char-count counter
    huge = EpisodeRecord(episode_number=5, prose="가" * 5000, accepted_draft_hash="h")

    constrained = _build(builder=builder, current_episode=6, previous_episode=huge)
    unconstrained = _build(current_episode=6, previous_episode=huge)

    assert constrained.suffix_tokens <= 400
    assert constrained.cached_prefix == unconstrained.cached_prefix  # prefix never trimmed
