"""Re-lint and revise an already-drafted episode using its saved canon store.

    python scripts/revise_episode.py --run data/run2 [--model gemini-3.5-flash]

Useful on its own (a re-edit pass) and cheap: it reuses the persisted Canon /
VoiceBible / GenreProfile instead of re-running the whole setup chain.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from novel_agent.artifacts import Draft, Summary  # noqa: E402
from novel_agent.canon_store import CanonStore  # noqa: E402
from novel_agent.context_pack import ContextPackBuilder  # noqa: E402
from novel_agent.llm import Usage, build_llm  # noqa: E402
from novel_agent.nodes import plan_episode, seed_arc_map  # noqa: E402
from novel_agent.reviser import revise_draft  # noqa: E402
from novel_agent.style import lint_prose, style_score  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="a run directory produced by run_setup.py")
    ap.add_argument("--episode", type=int, default=1)
    ap.add_argument("--model", default=None)
    ap.add_argument("--iterations", type=int, default=2)
    a = ap.parse_args()

    run = pathlib.Path(a.run)
    src = run / f"ep{a.episode:02d}.txt"
    store = CanonStore(run / "_novel")

    profile = store.load_genre_profile()
    north_star = store.load_north_star()
    voice = store.load_voice_bible()
    canon = store.load_canon()

    usage = Usage()
    llm = build_llm(usage=usage, **({"model": a.model} if a.model else {}))

    draft = Draft(episode_number=a.episode, prose=src.read_text(encoding="utf-8"))
    target = profile.episode_length_target
    print(f"■ 원본: {draft.char_count}자 · 문체 {style_score(draft.prose, target_chars=target)}/100")
    for v in lint_prose(draft.prose, target_chars=target):
        print(f"    {v}")

    beats = plan_episode(
        llm, episode_number=a.episode, profile=profile, north_star=north_star, canon=canon,
        arc_map=seed_arc_map(llm, north_star), rhythm=store.load_rhythm(),
        foreshadow=store.load_foreshadow(), summary=store.load_summary(),
    )
    pack = ContextPackBuilder().build(
        genre_profile=profile, north_star=north_star, voice_bible=voice, canon=canon,
        beat_sheet=beats, foreshadow=store.load_foreshadow(), rhythm=store.load_rhythm(),
        summary=store.load_summary(), current_episode=a.episode, previous_episode=None,
    )

    result = revise_draft(llm, draft, pack, target_chars=target, max_iterations=a.iterations)
    out = run / f"ep{a.episode:02d}_revised.txt"
    out.write_text(result.draft.prose, encoding="utf-8")

    print(f"\n■ 수정 {result.iterations}회 → {result.draft.char_count}자 · "
          f"문체 {result.score}/100 · {'통과' if result.passed else '미통과'}")
    for v in result.remaining:
        print(f"    {v}")
    print(f"→ {out}")
    print(f"\n■ 비용: {usage.calls} calls · ${usage.usd:.4f} (₩{usage.krw:.0f})")


if __name__ == "__main__":
    main()
