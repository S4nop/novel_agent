"""Read the episode waiting for approval, then accept or reject it.

    python scripts/review_episode.py --run data/arc-v8              # read it
    python scripts/review_episode.py --run data/arc-v8 --accept
    python scripts/review_episode.py --run data/arc-v8 --reject

Accepting advances every ledger and extracts the canon delta — that is the
point at which the episode becomes something later episodes are written from.
Rejecting touches nothing, so the next run rewrites that episode number.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from novel_agent.canon_store import CanonStore  # noqa: E402
from novel_agent.driver import accept_pending, reject_pending  # noqa: E402
from novel_agent.llm import Usage, build_llm  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="project dir")
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--full", action="store_true", help="print the whole episode")
    a = ap.parse_args()

    store = CanonStore(pathlib.Path(a.run) / "_novel")
    pending = store.pending_episode()
    if pending is None:
        raise SystemExit(f"{a.run}: 승인 대기 중인 화가 없습니다.")

    n = pending.draft.episode_number
    if a.accept and a.reject:
        raise SystemExit("--accept 와 --reject 는 함께 쓸 수 없습니다.")

    if not (a.accept or a.reject):
        print(f"\n{'='*70}\n{n}화 · {pending.draft.char_count}자 · 문체 {pending.score}/100"
              f"\n{'='*70}")
        print(f"훅   : {pending.beats.opening_hook}")
        print(f"진전 : {pending.beats.the_one_progression}")
        print(f"절단 : {pending.beats.closing_cliffhanger}")
        if pending.findings:
            print(f"\n■ 지적 {len(pending.findings)}건 (게이트는 통과)")
            for f in pending.findings:
                print(f"    {f}")
        prose = pending.draft.prose
        print(f"\n{'-'*70}")
        print(prose if a.full else prose[:1200] + ("\n…(--full 로 전체)" if len(prose) > 1200 else ""))
        print(f"{'-'*70}")
        print("\n승인: --accept   반려: --reject")
        return

    if a.reject:
        reject_pending(store)
        print(f"■ {n}화 반려. 캐논·원장 변경 없음 — 다음 실행이 {n}화를 다시 씁니다.")
        return

    usage = Usage()
    accept_pending(build_llm(usage=usage), store)
    print(f"■ {n}화 승인 — 원장 반영 + 캐논 추출 완료. 다음 실행은 {n + 1}화부터입니다.")
    print(f"■ 비용: {usage.calls} calls · ₩{usage.krw:.0f}")


if __name__ == "__main__":
    main()
