"""The unattended serial loop (클로드 제안 4, 6, 7).

Every test here is about a STOPPING condition. An unattended run that cannot
say why it stopped is the failure mode that costs money and canon, so the
reason is asserted as a value, never inferred.
"""
import pytest

from novel_agent.artifacts import PlannedSeed, SeedMagnitude
from novel_agent.canon_store import CanonStore
from novel_agent.driver import RunConfig, convergence_directive, run_serial
from novel_agent.llm import LLMRefusal, Usage
from novel_agent.schemas import (
    BeatDraft,
    BeatSheetDraft,
    CanonDeltaDraft,
    ContinuityFindingDraft,
    ContinuityReportDraft,
    SeedDraft,
)

from .factories import canon, genre_profile, north_star, voice_bible

# Sized to actually clear the gate: 5,212자 against the 5,200 target, balanced
# dialogue, style 100. A shorter fixture fails on LENGTH and every "did it
# commit?" assertion silently becomes a test of the length rule instead.
CLEAN = "\n\n".join(['"가진 놈 것만 훔친다."', "놈이 칼자루를 쥐었다. 골목이 조용해졌다.",
                     '"규칙인가?"', "봉출은 세 걸음 물러섰다. 담벼락이 등에 닿았다."] * 66)
DIRTY = '"오이오이!! 정말 대단하다고?!"\n' + "역시 나였다. 나는 분노했다.\n" * 5


class FakeLLM:
    """Boundary fake. `contradiction` plants a continuity blocker; `refuse`
    raises the way a real refusal does."""

    def __init__(self, prose=CLEAN, contradiction=False, refuse=False, seeds=()):
        self.prose, self.contradiction, self.refuse = prose, contradiction, refuse
        self.seeds = list(seeds)
        self.usage = Usage()
        self.directives: list[str] = []

    def structured(self, messages, schema):
        if self.refuse:
            raise LLMRefusal("declined")
        if schema is BeatSheetDraft:
            self.directives.append(messages[-1]["content"])
            return BeatSheetDraft(
                opening_hook="h", the_one_progression="p", closing_cliffhanger="c",
                entities_present=[], beats=[BeatDraft(text="b", beat_type="payoff")],
                seeds_to_plant=[SeedDraft(description=d, magnitude="major", due_by_ep=99)
                                for d in self.seeds])
        if schema is ContinuityReportDraft:
            return ContinuityReportDraft(findings=[ContinuityFindingDraft(
                canon_fact="캐논", prose_claim="본문", severity="blocker")]
                if self.contradiction else [])
        if schema is CanonDeltaDraft:
            return CanonDeltaDraft()
        raise AssertionError(schema)

    def text(self, messages, *, max_tokens=8192):
        if self.refuse:
            raise LLMRefusal("declined")
        return self.prose


def _store(tmp_path) -> CanonStore:
    s = CanonStore(tmp_path / "novel")
    s.initialize(genre_profile=genre_profile(), north_star=north_star(),
                 canon=canon(), voice_bible=voice_bible())
    return s


def _cfg(**kw) -> RunConfig:
    base = dict(target_episodes=2, revise_iterations=1, max_krw=1e9)
    return RunConfig(**{**base, **kw})


# ── the happy path ───────────────────────────────────────────────────────────
def test_it_writes_episodes_until_the_target_and_commits_each(tmp_path):
    s = _store(tmp_path)
    r = run_serial(FakeLLM(), s, config=_cfg(target_episodes=3))
    assert r.committed_episodes == 3
    assert [o.episode for o in r.outcomes] == [1, 2, 3]
    assert s.latest_episode_number() == 3


def test_it_resumes_from_what_the_store_already_holds(tmp_path):
    """An interrupted run must not rewrite committed episodes."""
    s = _store(tmp_path)
    run_serial(FakeLLM(), s, config=_cfg(target_episodes=2))
    r = run_serial(FakeLLM(), s, config=_cfg(target_episodes=4))
    assert [o.episode for o in r.outcomes] == [3, 4]     # not 1..4


# ── 완결 (제안 6) ────────────────────────────────────────────────────────────
def test_reaching_the_target_with_no_unpaid_major_seeds_is_완결(tmp_path):
    s = _store(tmp_path)
    r = run_serial(FakeLLM(), s, config=_cfg(target_episodes=2))
    assert r.completed is True
    assert "완결" in r.stopped_because


def test_an_unpaid_major_seed_prevents_a_완결_verdict(tmp_path):
    """A finite story may not end on an open major thread — it stops and says so
    rather than silently declaring itself finished."""
    s = _store(tmp_path)
    r = run_serial(FakeLLM(seeds=["흑막의 정체"]), s, config=_cfg(target_episodes=2))
    assert r.completed is False
    assert "미회수 주요 떡밥" in r.stopped_because


def test_convergence_pressure_is_silent_early_and_escalates_near_the_end():
    cfg = RunConfig(target_episodes=30, converge_within=5)
    assert convergence_directive(3, cfg, unpaid_major=2) == ""      # mid-serial
    near = convergence_directive(27, cfg, unpaid_major=2)
    assert "수렴" in near and "회수" in near
    last = convergence_directive(30, cfg, unpaid_major=0)
    assert "마지막" in last and "새 떡밥을 심지 마세요" in last


def test_the_convergence_directive_reaches_the_planner(tmp_path):
    s = _store(tmp_path)
    llm = FakeLLM()
    run_serial(llm, s, config=_cfg(target_episodes=2, converge_within=5))
    assert any("수렴" in p or "완결 화" in p for p in llm.directives)


# ── budget cap (제안 7) ──────────────────────────────────────────────────────
def test_the_budget_ceiling_stops_the_run_before_spending_more(tmp_path):
    s = _store(tmp_path)
    llm = FakeLLM()
    llm.usage.add(input_tokens=10_000_000, output_tokens=10_000_000)   # already over
    r = run_serial(llm, s, config=_cfg(target_episodes=5, max_krw=1000))
    assert r.outcomes == []                      # stopped before the first episode
    assert "예산 상한" in r.stopped_because


def test_the_budget_is_checked_before_each_episode_not_only_at_the_start(tmp_path):
    s = _store(tmp_path)
    llm = FakeLLM()

    class Metered(FakeLLM):
        def text(self, messages, *, max_tokens=8192):
            self.usage.add(input_tokens=200_000, output_tokens=200_000)
            return CLEAN

    m = Metered()
    r = run_serial(m, s, config=_cfg(target_episodes=10, max_krw=3000))
    assert 0 < len(r.outcomes) < 10
    assert "예산 상한" in r.stopped_because


# ── circuit breaker (제안 7) ─────────────────────────────────────────────────
def test_consecutive_gate_failures_trip_the_breaker(tmp_path):
    """A serial that cannot pass its own gate is not producing episodes worth
    keeping — stopping beats writing 30 bad ones."""
    s = _store(tmp_path)
    r = run_serial(FakeLLM(prose=DIRTY), s,
                   config=_cfg(target_episodes=20, max_consecutive_failures=3))
    assert len(r.outcomes) == 3
    assert "서킷 브레이커" in r.stopped_because
    assert r.committed_episodes == 0


def test_a_continuity_blocked_episode_is_never_committed(tmp_path):
    s = _store(tmp_path)
    r = run_serial(FakeLLM(contradiction=True), s,
                   config=_cfg(target_episodes=5, max_consecutive_failures=2))
    assert r.committed_episodes == 0
    assert s.latest_episode_number() == 0          # canon untouched
    assert all("연속성 차단" in o.reason for o in r.outcomes)


def test_repeated_refusals_trip_the_breaker_rather_than_crashing(tmp_path):
    """An unattended run must survive a provider declining."""
    s = _store(tmp_path)
    r = run_serial(FakeLLM(refuse=True), s,
                   config=_cfg(target_episodes=10, max_consecutive_failures=2))
    assert "서킷 브레이커" in r.stopped_because
    assert all("거부" in o.reason for o in r.outcomes)


def test_the_breaker_resets_after_a_passing_episode(tmp_path):
    """Isolated failures must not accumulate into a false trip."""
    s = _store(tmp_path)

    class Flaky(FakeLLM):
        n = 0
        def text(self, messages, *, max_tokens=8192):
            Flaky.n += 1
            return DIRTY if Flaky.n == 1 else CLEAN

    r = run_serial(Flaky(), s, config=_cfg(target_episodes=3, max_consecutive_failures=2))
    assert r.committed_episodes >= 1
    assert "서킷 브레이커" not in r.stopped_because


# ── reporting ────────────────────────────────────────────────────────────────
def test_the_report_always_says_why_it_stopped(tmp_path):
    s = _store(tmp_path)
    for cfg in (_cfg(target_episodes=1),
                _cfg(target_episodes=5, max_krw=1),
                _cfg(target_episodes=5, max_consecutive_failures=1)):
        r = run_serial(FakeLLM(prose=CLEAN), _store(tmp_path / str(id(cfg))), config=cfg)
        assert r.stopped_because, "a run must never stop without a reason"


def test_a_failed_episode_is_retried_and_never_leaves_a_hole(tmp_path):
    """Measured on a live run: episode 2 failed the style gate, the driver moved
    on, and the store ended up with episodes 1 and 3 — then declared 완결. A
    serial with a missing episode cannot be published, so a failure must never
    advance the cursor."""
    s = _store(tmp_path)

    class FailsOnce(FakeLLM):
        seen = 0
        def text(self, messages, *, max_tokens=8192):
            FailsOnce.seen += 1
            # fail the 2nd episode's first draft, then recover
            return DIRTY if FailsOnce.seen == 3 else CLEAN

    r = run_serial(FailsOnce(), s, config=_cfg(target_episodes=3,
                                               max_consecutive_failures=3))
    committed = sorted(o.episode for o in r.outcomes if o.committed)
    assert committed == [1, 2, 3], f"gap in the serial: {committed}"
    assert s.load_episode(2) is not None


def test_the_breaker_names_the_episode_it_gave_up_on(tmp_path):
    """Retrying forever on one bad episode is the failure mode the breaker
    exists for — and the operator needs to know WHICH episode."""
    s = _store(tmp_path)
    r = run_serial(FakeLLM(prose=DIRTY), s,
                   config=_cfg(target_episodes=5, max_consecutive_failures=2))
    assert all(o.episode == 1 for o in r.outcomes)     # retried ep1, never advanced
    assert "1화에서 연속 실패" in r.stopped_because
    assert s.latest_episode_number() == 0


def test_완결_cannot_be_declared_with_a_missing_episode(tmp_path):
    """The live run declared 완결 while holding only episodes 1 and 3."""
    s = _store(tmp_path)
    r = run_serial(FakeLLM(prose=DIRTY), s,
                   config=_cfg(target_episodes=3, max_consecutive_failures=2))
    assert r.completed is False
    assert r.committed_episodes == 0


def test_a_rejected_episode_keeps_its_prose_and_its_evidence(tmp_path):
    """A live run blocked 1화 on a canon contradiction and the driver discarded
    4,687자 of prose, leaving the author a rule name and no way to check it. The
    author cannot judge, or correct canon, from that."""
    s = _store(tmp_path)
    r = run_serial(FakeLLM(contradiction=True), s,
                   config=_cfg(target_episodes=1, max_consecutive_failures=1))
    o = r.outcomes[0]
    assert o.committed is False
    assert o.prose, "a rejected draft must still be readable"
    assert any("캐논" in f for f in o.findings), "and diagnosable"


def test_a_committed_episode_also_carries_its_findings(tmp_path):
    """Passing is not the same as clean — minor findings are still worth seeing."""
    s = _store(tmp_path)
    r = run_serial(FakeLLM(), s, config=_cfg(target_episodes=1))
    assert r.outcomes[0].committed is True
    assert r.outcomes[0].prose


def test_an_account_failure_stops_immediately_with_the_real_reason(tmp_path):
    """A live run hit "credit balance is too low" and crashed with a traceback.
    Retrying is pointless and the breaker is the wrong response — every
    remaining episode fails identically, so "연속 실패 3회" would bury the actual
    cause behind a misleading one."""
    from novel_agent.llm import LLMUnavailable

    class Broke(FakeLLM):
        def text(self, messages, *, max_tokens=8192):
            raise LLMUnavailable("credit balance is too low")

    s = _store(tmp_path)
    r = run_serial(Broke(), s, config=_cfg(target_episodes=10, max_consecutive_failures=3))
    assert r.outcomes == []                       # no retries burned
    assert "API 사용 불가" in r.stopped_because
    assert "credit balance" in r.stopped_because  # the operator sees what to fix
