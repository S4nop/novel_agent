"""Orchestration driver — episode 1 → 완결 without per-episode human input.

This is the 공장형 loop (클로드 제안 4, 6, 7). Everything up to now was a
button press per episode; this owns the whole serial.

Orchestrator-in-code (DESIGN §5): plain Python owns the loop, the gates, the
budget and the stopping conditions. Every LLM call stays a thin stateless
function. That is what makes an unattended run debuggable — when it stops, the
reason is a value in `RunReport`, not a guess.

THREE RAILS, because "runs unattended" and "runs away" are the same code path
without them:

  * budget cap — a hard ₩ ceiling checked BEFORE each episode, so a runaway
    loop costs one episode of overshoot, not a night's spend.
  * circuit breaker — N consecutive gate failures stops the run. A serial that
    cannot pass its own gate is not producing episodes worth keeping, and the
    canon damage compounds.
  * convergence — a target episode count plus forced major-seed payoff near the
    end, so the story aims at 완결 instead of running forever. The NorthStar
    prompt asks for an engine that can run 100+ episodes; without this the
    system has no reason to ever stop.

A blocked episode is NEVER committed (continuity is a hard gate), so a failing
run leaves canon exactly as it was rather than accumulating damage.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .artifacts import BeatSheet, Draft, Summary
from .canon_store import CanonStore
from .canonicalizer import canonicalize_episode, commit_episode_state
from .context_pack import ContextPackBuilder
from .continuity import blocks_acceptance, check_continuity, deterministic_findings
from .craft import judge_craft
from .drafter import draft_episode
from .llm import LLM, LLMRefusal, Usage
from .nodes import plan_episode, seed_arc_map
from .reviser import revise_draft
from .style import Violation, forbidden_terms_from, style_score


@dataclass
class RunConfig:
    """Every stopping condition is explicit — an unattended run must never rely
    on someone noticing."""
    target_episodes: int = 30
    max_krw: float = 100_000.0          # hard budget ceiling for the whole run
    max_consecutive_failures: int = 3   # circuit breaker
    revise_iterations: int = 3
    # Episodes before the target at which the planner is told to start closing
    # threads. Major seeds cannot all be paid in the final episode.
    converge_within: int = 5
    forbidden_terms: list[str] = field(default_factory=list)


@dataclass
class EpisodeOutcome:
    episode: int
    passed: bool
    committed: bool
    chars: int
    score: int
    continuity_blockers: int
    craft_findings: int = 0
    reason: str = ""
    # A rejected episode still has to be readable and diagnosable: the author
    # cannot judge, and cannot correct canon, from a rule name alone.
    prose: str = ""
    findings: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    """Why the run stopped is the most important field here."""
    outcomes: list[EpisodeOutcome] = field(default_factory=list)
    stopped_because: str = ""
    completed: bool = False             # reached 완결, not merely ran out of road
    krw: float = 0.0

    @property
    def committed_episodes(self) -> int:
        return sum(1 for o in self.outcomes if o.committed)


def convergence_directive(episode: int, cfg: RunConfig, unpaid_major: int) -> str:
    """Pressure toward an ending, escalating as the target approaches.

    Returns "" for most of the run — a serial that is told to wrap up from
    episode 1 has no middle.
    """
    remaining = cfg.target_episodes - episode
    if remaining > cfg.converge_within:
        return ""
    if remaining <= 0:
        return ("[완결 화] 이번 화가 마지막입니다. 남은 떡밥을 모두 회수하고 "
                "주인공의 목표에 결말을 내세요. 새 떡밥을 심지 마세요.")
    if unpaid_major:
        return (f"[수렴 구간] 완결까지 {remaining}화 남았습니다. 미회수 주요 떡밥이 "
                f"{unpaid_major}건 있습니다. 새 떡밥을 심지 말고, 이번 화에서 "
                f"최소 하나를 반드시 회수하세요.")
    return (f"[수렴 구간] 완결까지 {remaining}화 남았습니다. 새 떡밥을 심지 말고 "
            f"클라이맥스로 수렴시키세요.")


def _plan_and_write(llm: LLM, store: CanonStore, episode: int, cfg: RunConfig):
    """One episode, plan → draft → revise → continuity. No commits."""
    profile = store.load_genre_profile()
    ns = store.load_north_star()
    canon = store.load_canon()
    foreshadow = store.load_foreshadow()

    beats = plan_episode(
        llm, episode_number=episode, profile=profile, north_star=ns, canon=canon,
        arc_map=seed_arc_map(llm, ns), rhythm=store.load_rhythm(),
        foreshadow=foreshadow, summary=store.load_summary(),
        extra_directive=convergence_directive(episode, cfg, len(foreshadow.unpaid_major())),
    )
    prev = store.load_episode(episode - 1)
    pack = ContextPackBuilder().build(
        genre_profile=profile, north_star=ns, voice_bible=store.load_voice_bible(),
        canon=canon, beat_sheet=beats, foreshadow=foreshadow,
        rhythm=store.load_rhythm(), summary=store.load_summary(),
        current_episode=episode, previous_episode=prev,
    )
    draft = draft_episode(llm, pack, max_tokens=32768)
    result = revise_draft(
        llm, draft, pack, target_chars=beats.length_target,
        max_iterations=cfg.revise_iterations, forbidden_terms=cfg.forbidden_terms,
        extra_findings=lambda d: deterministic_findings(d, beats, canon),
    )
    continuity = check_continuity(llm, result.draft, beats, canon)
    # Track B is ADVISORY: it reports craft problems for the author and the
    # reviser but never gates, because a subjective judge with blocking power
    # halts an unattended run on an opinion.
    craft = judge_craft(llm, result.draft, profile, canon, store.load_voice_bible())
    return beats, result, continuity, craft


def run_serial(llm: LLM, store: CanonStore, *, config: RunConfig | None = None,
               usage: Usage | None = None) -> RunReport:
    """Write episodes until 완결, the target, the budget, or the breaker.

    Resumes from whatever the store already holds, so an interrupted run picks
    up where it stopped rather than rewriting committed episodes.
    """
    cfg = config or RunConfig()
    usage = usage or getattr(llm, "usage", None) or Usage()
    report = RunReport()
    consecutive_failures = 0
    start = store.latest_episode_number() + 1

    episode = start
    while episode <= cfg.target_episodes:
        # Budget is checked BEFORE spending, so the cap is a ceiling rather
        # than something noticed after the fact.
        if usage.krw >= cfg.max_krw:
            report.stopped_because = (
                f"예산 상한 도달: ₩{usage.krw:,.0f} ≥ ₩{cfg.max_krw:,.0f}")
            break

        try:
            beats, result, continuity, craft = _plan_and_write(llm, store, episode, cfg)
        except LLMRefusal as e:
            consecutive_failures += 1
            report.outcomes.append(EpisodeOutcome(
                episode=episode, passed=False, committed=False, chars=0, score=0,
                continuity_blockers=0, reason=f"모델 거부/빈 응답: {e}"))
            if consecutive_failures >= cfg.max_consecutive_failures:
                report.stopped_because = f"연속 실패 {consecutive_failures}회 — 서킷 브레이커"
                break
            continue        # retry the SAME episode; never leave a hole

        blocked = blocks_acceptance(continuity)
        passed = bool(result.passed) and not blocked
        if passed:
            commit_episode_state(store, result.draft, beats)
            canonicalize_episode(llm, store, result.draft)
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        report.outcomes.append(EpisodeOutcome(
            episode=episode, passed=passed, committed=passed,
            chars=result.draft.char_count, score=result.score,
            continuity_blockers=sum(1 for v in continuity if v.severity == "blocker"),
            craft_findings=len(craft),
            reason="" if passed else _why(result, continuity, blocked),
            prose=result.draft.prose,
            findings=[f"[{v.severity}] {v.rule} — {v.evidence}"
                      for v in list(continuity) + list(result.remaining) + list(craft)
                      if v.evidence]))

        if not passed:
            # Retry the SAME episode. Advancing would leave a hole in the
            # serial — a measured live run produced episodes 1 and 3 with no 2
            # and still declared 완결. A serial with a missing episode cannot
            # be published, so a failure must never move the cursor forward.
            if consecutive_failures >= cfg.max_consecutive_failures:
                report.stopped_because = (
                    f"{episode}화에서 연속 실패 {consecutive_failures}회 — 서킷 브레이커. "
                    "캐논이 망가졌거나 프롬프트 조정이 필요합니다.")
                break
            continue

        # 완결 check runs only after a committed episode: a story cannot end on
        # a draft that was rejected.
        if episode >= cfg.target_episodes:
            if store.load_foreshadow().completion_ready():
                report.completed = True
                report.stopped_because = f"완결 — {episode}화, 미회수 주요 떡밥 0건"
            else:
                unpaid = len(store.load_foreshadow().unpaid_major())
                report.stopped_because = (
                    f"목표 {cfg.target_episodes}화 도달했으나 미회수 주요 떡밥 {unpaid}건 — "
                    "완결 판정 불가. 회수 화를 더 쓰거나 사람이 확인하세요.")
            break
        episode += 1        # only a COMMITTED episode moves the cursor

    if not report.stopped_because:
        report.stopped_because = f"목표 {cfg.target_episodes}화까지 진행 완료"

    report.krw = usage.krw
    return report


def _why(result, continuity: list[Violation], blocked: bool) -> str:
    if blocked:
        names = [v.rule for v in continuity if v.severity == "blocker"]
        return "연속성 차단: " + ", ".join(names[:3])
    worst = [v.rule for v in result.remaining if v.severity in ("blocker", "major")]
    return f"게이트 미통과(문체 {result.score}) — " + ", ".join(worst[:3])
