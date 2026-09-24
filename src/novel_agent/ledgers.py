"""Cross-episode control ledgers — the two things that make it a *web novel*
rather than generic serialized text (DESIGN §1, §3).

These are domain entities: the business logic lives on the model (pure,
in-memory, unit-testable in milliseconds), the store only persists them.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .artifacts import BeatType, ForeshadowSeed, PlannedSeed, SeedMagnitude, SeedStatus

# 고구마 (frustration) accrues debt; 사이다/reveal (payoff) pays it down.
_FRUSTRATION_BEATS = {BeatType.FRUSTRATION}
_PAYOFF_BEATS = {BeatType.PAYOFF, BeatType.REVEAL}


class RhythmState(BaseModel):
    """사이다/고구마 rhythm controller with a rolling frustration-debt meter.

    Blocks a new setup-heavy episode until an owed payoff ships. Config is
    seeded from GenreProfile (cadence + max consecutive frustration).
    """
    frustration_debt: int = 0
    max_consecutive_frustration: int = 2
    target_catharsis_cadence: int = 3       # a payoff must land at least every N eps
    episodes_since_payoff: int = 0
    beat_log: list[list[BeatType]] = Field(default_factory=list)
    # Whether a payoff actually LANDED in the prose, per episode, as judged by
    # the craft reader. None = not judged, fall back to the beat tags. Kept
    # beside the log rather than folded into it: the meters are recomputed from
    # the log, so a verdict held anywhere else is lost on the next episode.
    payoff_log: list[bool | None] = Field(default_factory=list)

    def record_episode(self, beats: list[BeatType], *,
                       episode: int | None = None,
                       payoff_landed: bool | None = None) -> None:
        """Record one accepted episode's beat tags and re-derive the meters.

        This used to fold straight into the counters, which has no inverse: a
        re-committed episode could only ever be added a second time, doubling
        the debt that drives pacing_directive. The log already holds the whole
        history, so the meters are computed from it and re-recording an episode
        replaces its entry instead.
        """
        if episode is not None and episode - 1 < len(self.beat_log):
            self.beat_log[episode - 1] = list(beats)
            self.payoff_log[episode - 1] = payoff_landed
        else:
            self.beat_log.append(list(beats))
            self.payoff_log.append(payoff_landed)
        # stores written before payoff_log existed
        while len(self.payoff_log) < len(self.beat_log):
            self.payoff_log.append(None)

        debt = since = 0
        for i, tags in enumerate(self.beat_log):
            frustration = sum(1 for b in tags if b in _FRUSTRATION_BEATS)
            debt += frustration
            # A planner label is an intention; the judge read the prose. Live
            # 2화 tagged a `reveal` the prose never delivered, and the meter
            # reset to 부채 0 while the craft judge reported four straight
            # 고구마 beats with no 사이다.
            judged = self.payoff_log[i] if i < len(self.payoff_log) else None
            landed = judged if judged is not None else any(
                b in _PAYOFF_BEATS for b in tags)
            if landed:
                debt = max(0, debt - frustration - 1)
                since = 0
            else:
                since += 1
        self.frustration_debt, self.episodes_since_payoff = debt, since

    def blocks_setup_heavy_episode(self) -> bool:
        """True when an owed payoff must land before another setup-heavy episode."""
        return (
            self.frustration_debt > self.max_consecutive_frustration
            or self.episodes_since_payoff >= self.target_catharsis_cadence
        )

    def pacing_directive(self) -> str:
        """Korean directive injected into the ContextPack volatile suffix."""
        if self.blocks_setup_heavy_episode():
            return (
                "[페이싱] 고구마가 누적되었습니다. 이번 화에는 반드시 사이다(보상) "
                "장면을 넣고, 새 떡밥·setup 위주 전개는 미루세요."
            )
        return "[페이싱] 사이다 리듬 양호 — 계획대로 전개하세요."


class ForeshadowLedger(BaseModel):
    """떡밥 장부 — setup→payoff tracking with due-by episodes.

    Canonicalizer mints canonical seed_ids on commit (invariant #8).
    Completion gates on zero unpaid *major* seeds (DESIGN §3, §7).
    """
    seeds: dict[str, ForeshadowSeed] = Field(default_factory=dict)
    next_seq: int = 1

    def mint_seed_id(self) -> str:
        sid = f"seed-{self.next_seq:04d}"
        self.next_seq += 1
        return sid

    def plant(self, planned: PlannedSeed, episode: int) -> ForeshadowSeed:
        """Mint a canonical id for a planner-proposed seed and record it as planted.

        Re-planting the same thread for the same episode returns the existing
        seed: a console retry used to mint a second id for it, and every
        duplicate MAJOR pushed completion_ready further out.
        """
        for existing in self.seeds.values():
            if existing.planted_ep == episode and existing.description == planned.description:
                return existing
        seed = ForeshadowSeed(
            seed_id=self.mint_seed_id(),
            description=planned.description,
            magnitude=planned.magnitude,
            planted_ep=episode,
            due_by_ep=planned.due_by_ep,
            status=SeedStatus.PLANTED,
        )
        self.seeds[seed.seed_id] = seed
        return seed

    def reinforce(self, seed_id: str, episode: int) -> None:
        seed = self.seeds[seed_id]
        seed.reinforced_in.append(episode)
        if seed.status == SeedStatus.PLANTED:
            seed.status = SeedStatus.REINFORCED

    def pay(self, seed_id: str, episode: int) -> None:
        """Close a seed, recording WHEN — the episode argument used to be
        dropped, so plant-to-payoff distance could not be measured after the
        fact and "is a 2화 payoff too fast?" could only be answered by reading
        the planner's intent."""
        seed = self.seeds[seed_id]
        seed.status = SeedStatus.PAID
        seed.paid_ep = episode

    def _open(self, seed: ForeshadowSeed) -> bool:
        return seed.status not in (SeedStatus.PAID, SeedStatus.ABANDONED)

    def ripening(self, current_ep: int) -> list[ForeshadowSeed]:
        """Open seeds due NEXT episode — the last chance to touch one before
        it has to pay off.

        due() only fires on the episode a seed is ALREADY due, so a seed
        planted in 1화 and due in 3화 was shown to nobody in 2화: 던지기 →
        침묵 → 회수. With no touch in between, a short 떡밥 reads as a
        sentence answering itself rather than a thread.
        """
        return [
            s for s in self.seeds.values()
            if self._open(s) and s.due_by_ep == current_ep + 1
        ]

    def due(self, current_ep: int) -> list[ForeshadowSeed]:
        """Open seeds whose payoff is due by the current episode."""
        return [
            s for s in self.seeds.values()
            if self._open(s) and s.due_by_ep is not None and s.due_by_ep <= current_ep
        ]

    def unpaid_major(self) -> list[ForeshadowSeed]:
        return [
            s for s in self.seeds.values()
            if s.magnitude == SeedMagnitude.MAJOR and self._open(s)
        ]

    def completion_ready(self) -> bool:
        """A finite story may end only with zero unpaid major seeds."""
        return len(self.unpaid_major()) == 0
