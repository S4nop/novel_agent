"""Behavior tests for the cross-episode control ledgers (DESIGN §3)."""
from novel_agent.artifacts import BeatType, PlannedSeed, SeedMagnitude, SeedStatus
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


def test_debt_is_recomputed_from_the_beat_log_when_an_episode_is_re_recorded():
    """record_episode was a fold with no inverse, so a re-recorded episode
    could only ever be added again. The log holds the whole history, so the
    meter is derived from it instead."""
    r = RhythmState(max_consecutive_frustration=2, target_catharsis_cadence=99)
    r.record_episode([F, F], episode=1)
    r.record_episode([F], episode=2)
    assert r.frustration_debt == 3

    r.record_episode([P], episode=1)

    assert r.beat_log == [[P], [F]]
    assert r.frustration_debt == 1


def test_planting_the_same_seed_twice_for_one_episode_mints_one_id():
    led = ForeshadowLedger()
    seed = PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                       magnitude=SeedMagnitude.MAJOR, due_by_ep=5)
    first = led.plant(seed, episode=1)
    again = led.plant(seed, episode=1)

    assert first.seed_id == again.seed_id
    assert list(led.seeds) == [first.seed_id]
    assert led.next_seq == 2


def test_the_same_description_planted_in_a_later_episode_is_a_new_seed():
    """Dedupe must not swallow a thread the author deliberately re-plants."""
    led = ForeshadowLedger()
    seed = PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                       magnitude=SeedMagnitude.MAJOR, due_by_ep=9)
    led.plant(seed, episode=1)
    led.plant(seed, episode=4)

    assert len(led.seeds) == 2


# ── 흔들기: the missing middle between 던지기 and 회수 ────────────────────────

def test_a_seed_is_surfaced_for_reinforcement_the_episode_before_it_is_due():
    """due() only fires on the episode a seed is ALREADY due, so a seed planted
    in 1화 and due in 3화 was shown to nobody in 2화: 던지기 → 침묵 → 회수. With
    no touch in between a short 떡밥 reads as a sentence answering itself."""
    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                                 magnitude=SeedMagnitude.MINOR, due_by_ep=3), episode=1)

    assert [s.seed_id for s in led.ripening(2)] == [seed.seed_id]
    assert led.ripening(1) == []          # just planted, nothing to shake yet
    assert led.ripening(3) == []          # already due — due() owns it now


def test_a_paid_seed_is_never_offered_for_reinforcement():
    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                                 due_by_ep=3), episode=1)
    led.pay(seed.seed_id, episode=2)
    assert led.ripening(2) == []


# ── the rhythm controller believes the PLAN, not the prose ──────────────────

def test_a_payoff_the_prose_never_delivered_does_not_pay_down_the_debt():
    """Measured on a live 2화: beat_log [setup, escalation, frustration, reveal,
    escalation, cliffhanger] left the meter at 부채 0 · 무보상 0, while the craft
    judge — which reads the prose — reported '사이다 없이 고구마성 전개만 네 차례
    연속'. The label was enough to satisfy the controller."""
    r = RhythmState(max_consecutive_frustration=2, target_catharsis_cadence=99)
    r.record_episode([F, F], episode=1)
    r.record_episode([F, R], episode=2, payoff_landed=False)

    assert r.frustration_debt == 3
    assert r.episodes_since_payoff == 2


def test_a_delivered_payoff_still_pays_the_debt_down():
    r = RhythmState(max_consecutive_frustration=2, target_catharsis_cadence=99)
    r.record_episode([F, F], episode=1)
    r.record_episode([F, R], episode=2, payoff_landed=True)

    assert r.episodes_since_payoff == 0


def test_an_unjudged_episode_falls_back_to_its_beat_tags():
    """judge_craft is advisory and returns nothing on failure — a run must not
    lose its rhythm because one judge call errored."""
    r = RhythmState(max_consecutive_frustration=2, target_catharsis_cadence=99)
    r.record_episode([F, R], episode=1)
    assert r.episodes_since_payoff == 0


def test_the_verdict_survives_a_later_episode_being_recorded():
    """The meters are recomputed from the log, so the judged outcome has to be
    part of that log or the next episode silently restores the label."""
    r = RhythmState(max_consecutive_frustration=2, target_catharsis_cadence=99)
    r.record_episode([F, R], episode=1, payoff_landed=False)
    r.record_episode([F], episode=2)

    assert r.episodes_since_payoff == 2


def test_a_paid_seed_records_the_episode_that_paid_it():
    """pay() took an episode and dropped it, so plant-to-payoff distance could
    not be measured after the fact — the question "is 2화 too fast?" could only
    be answered by reading the planner's intent."""
    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                                 due_by_ep=9), episode=2)
    led.pay(seed.seed_id, episode=7)

    paid = led.seeds[seed.seed_id]
    assert (paid.planted_ep, paid.paid_ep) == (2, 7)


def test_an_abandoned_seed_stops_being_owed():
    """SeedStatus.ABANDONED was tested by _open() and assigned by nobody, so a
    seed could only ever be PLANTED, REINFORCED or PAID. A thread the author
    drops therefore had no exit: due() re-listed it every episode and, if
    major, unpaid_major() kept it so completion_ready() could never be True
    again."""
    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                                 magnitude=SeedMagnitude.MAJOR, due_by_ep=3), episode=1)
    assert led.completion_ready() is False

    led.abandon(seed.seed_id, episode=5)

    assert led.completion_ready() is True
    assert led.due(9) == []
    assert led.seeds[seed.seed_id].status is SeedStatus.ABANDONED


def test_abandoning_records_when_so_the_decision_is_auditable():
    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                                 due_by_ep=3), episode=1)
    led.abandon(seed.seed_id, episode=5)
    assert led.seeds[seed.seed_id].paid_ep is None
    assert led.seeds[seed.seed_id].abandoned_ep == 5


def test_a_paid_seed_is_not_quietly_reopened_by_abandoning_it():
    led = ForeshadowLedger()
    seed = led.plant(PlannedSeed(proposed_seed_id="p1", description="떡밥A",
                                 due_by_ep=3), episode=1)
    led.pay(seed.seed_id, episode=2)
    led.abandon(seed.seed_id, episode=5)
    assert led.seeds[seed.seed_id].status is SeedStatus.PAID
