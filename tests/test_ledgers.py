"""Behavior tests for the cross-episode control ledgers (DESIGN §3)."""
from novel_agent.artifacts import BeatType, PlannedSeed, SeedMagnitude
from novel_agent.ledgers import ForeshadowLedger, RhythmState

F, P, S, R, C = (
    BeatType.FRUSTRATION, BeatType.PAYOFF, BeatType.SETUP,
    BeatType.REVEAL, BeatType.CLIFFHANGER,
)


def test_blocks_setup_heavy_episode_when_frustration_exceeds_cap():
    r = RhythmState(max_consecutive_frustration=2, target_catharsis_cadence=99)
    r.record_episode([F, F, F])
    assert r.frustration_debt == 3
    assert r.blocks_setup_heavy_episode() is True


def test_payoff_beat_pays_down_debt_and_resets_cadence_counter():
    r = RhythmState(max_consecutive_frustration=2, target_catharsis_cadence=3)
    r.record_episode([F, F, F])          # debt 3, blocking
    r.record_episode([P, S])             # payoff lands
    assert r.frustration_debt == 2
    assert r.episodes_since_payoff == 0
    assert r.blocks_setup_heavy_episode() is False


def test_blocks_setup_heavy_episode_when_no_payoff_within_cadence_window():
    r = RhythmState(max_consecutive_frustration=99, target_catharsis_cadence=3)
    r.record_episode([S])
    r.record_episode([S])
    assert r.blocks_setup_heavy_episode() is False
    r.record_episode([S])                # 3 episodes, no payoff
    assert r.episodes_since_payoff == 3
    assert r.blocks_setup_heavy_episode() is True


def test_pacing_directive_is_korean_and_reflects_debt():
    r = RhythmState(max_consecutive_frustration=1, target_catharsis_cadence=99)
    assert "양호" in r.pacing_directive()
    r.record_episode([F, F])
    assert "사이다" in r.pacing_directive()


def test_due_returns_overdue_unpaid_seeds_only():
    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="x", description="숨겨진 혈통", due_by_ep=5), episode=1)
    assert led.due(4) == []
    assert led.due(5) == [seed]
    led.pay(seed.seed_id, episode=5)
    assert led.due(5) == []


def test_completion_blocked_until_every_major_seed_is_paid():
    led = ForeshadowLedger()
    major = led.plant(
        PlannedSeed(proposed_seed_id="m", description="흑막의 정체", magnitude=SeedMagnitude.MAJOR),
        episode=1,
    )
    led.plant(PlannedSeed(proposed_seed_id="n", description="사소한 복선"), episode=1)  # minor, unpaid
    assert led.completion_ready() is False
    led.pay(major.seed_id, episode=30)
    assert led.completion_ready() is True   # unpaid MINOR does not block completion


def test_minted_seed_ids_are_unique():
    led = ForeshadowLedger()
    a = led.plant(PlannedSeed(proposed_seed_id="a", description="복선 A"), episode=1)
    b = led.plant(PlannedSeed(proposed_seed_id="b", description="복선 B"), episode=1)
    assert a.seed_id != b.seed_id
    assert set(led.seeds) == {a.seed_id, b.seed_id}
