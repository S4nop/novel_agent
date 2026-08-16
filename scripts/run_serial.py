"""Run a locked project from its next episode toward 완결, unattended.

    # after setup (run_setup.py) has locked premise + canon + voice:
    python scripts/run_serial.py --run data/verify-run --episodes 30 --budget 50000

Every stopping condition is explicit — budget, circuit breaker, 완결, target.
The run resumes from whatever the store already holds, so an interrupted run
picks up where it stopped instead of rewriting committed episodes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from novel_agent.canon_store import CanonStore  # noqa: E402
from novel_agent.driver import RunConfig, run_serial  # noqa: E402
from novel_agent.interview import Answer  # noqa: E402
from novel_agent.llm import Usage, build_llm  # noqa: E402
from novel_agent.style import forbidden_terms_from  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="project dir created by run_setup.py")
    ap.add_argument("--episodes", type=int, default=30, help="target episode count (완결 목표)")
    ap.add_argument("--budget", type=float, default=100_000, help="hard ₩ ceiling for the run")
    ap.add_argument("--max-failures", type=int, default=3, help="circuit breaker")
    ap.add_argument("--iterations", type=int, default=3, help="revise passes per episode")
    ap.add_argument("--converge-within", type=int, default=5,
                    help="episodes before target at which to start closing threads")
    ap.add_argument("--answers", default=None, help="interview answers, for 금기어 enforcement")
    a = ap.parse_args()

    run = pathlib.Path(a.run)
    store = CanonStore(run / "_novel")
    if not (run / "_novel" / "canon.json").exists():
        raise SystemExit(f"{run} has no locked canon — run scripts/run_setup.py first")

    forbidden: list[str] = []
    if a.answers:
        prior = [Answer(topic=x["topic"], question=x.get("question", ""),
                        answer=x["answer"], hard_rule=bool(x.get("hard_rule", False)))
                 for x in json.loads(pathlib.Path(a.answers).read_text(encoding="utf-8"))]
        forbidden = forbidden_terms_from(
            hard_rules=[x.answer for x in prior if x.hard_rule],
            anti_patterns=store.load_genre_profile().forbidden_anti_patterns)

    usage = Usage()
    llm = build_llm(usage=usage)
    start = store.latest_episode_number() + 1
    print(f"■ {run} · {start}화부터 {a.episodes}화까지 · 예산 ₩{a.budget:,.0f} · "
          f"연속 실패 {a.max_failures}회 시 중단")
    if forbidden:
        print(f"■ 금기어 {len(forbidden)}건: {', '.join(forbidden[:6])}")

    report = run_serial(llm, store, usage=usage, config=RunConfig(
        target_episodes=a.episodes, max_krw=a.budget,
        max_consecutive_failures=a.max_failures, revise_iterations=a.iterations,
        converge_within=a.converge_within, forbidden_terms=forbidden))

    # The store keeps episodes as JSON under _novel/episodes/; export the
    # committed ones as plain .txt so they are actually readable.
    print(f"\n{'='*70}")
    for o in report.outcomes:
        mark = "✓" if o.committed else "✗"
        line = f"  {mark} {o.episode:>3}화 · {o.chars:>5}자 · 문체 {o.score:>3}"
        if o.committed:
            rec = store.load_episode(o.episode)
            if rec is not None:
                path = run / f"ep{o.episode:02d}.txt"
                path.write_text(rec.prose, encoding="utf-8")
                line += f" → {path.name}"
        if o.reason:
            line += f" · {o.reason}"
        print(line)
    print(f"{'='*70}")
    print(f"■ 커밋된 화: {report.committed_episodes}/{len(report.outcomes)} 시도")
    print(f"■ 중단 사유: {report.stopped_because}")
    print(f"■ 완결 여부: {'완결' if report.completed else '미완결'}")
    print(f"■ 비용: {usage.calls} calls · ₩{usage.krw:,.0f} · 캐시 적중 {usage.cache_hit_rate:.0%}")


if __name__ == "__main__":
    main()
