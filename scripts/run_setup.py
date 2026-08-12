"""Run the setup chain from a user's freeform idea, then draft episode 1.

    python scripts/run_setup.py --idea "네오 조선의 흑인 홍길동, 코믹" [--pick 1] [--draft]

Everything creative is DERIVED from the idea by the agent — the developer
hand-authors nothing. Candidates for the premise gate are all printed; the
human picks (Co-writer mode). --pick preselects one so the run is unattended.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from novel_agent.artifacts import Summary  # noqa: E402
from novel_agent.canon_store import CanonStore  # noqa: E402
from novel_agent.context_pack import ContextPackBuilder  # noqa: E402
from novel_agent.drafter import draft_episode  # noqa: E402
from novel_agent.interview import (  # noqa: E402
    Answer,
    enrich_idea,
    generate_interview_questions,
    render_question,
    resolve_answer,
)
from novel_agent.llm import Usage, build_llm  # noqa: E402
from novel_agent.nodes import (  # noqa: E402
    generate_northstar_candidates,
    infer_genre_profile,
    init_canon_and_voice,
    plan_episode,
    seed_arc_map,
    to_north_star,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--idea", required=True)
    ap.add_argument("--pick", type=int, default=1, help="which NorthStar candidate to lock (1-based)")
    ap.add_argument("--candidates", type=int, default=3)
    ap.add_argument("--out", default="data/run")
    ap.add_argument("--draft", action="store_true", help="also draft episode 1")
    ap.add_argument("--interview", action="store_true",
                    help="interview the author before building the world (recommended)")
    ap.add_argument("--answers", default=None,
                    help="JSON file of prior interview answers [{topic, answer}] — "
                         "replays a completed interview without re-asking")
    ap.add_argument("--questions", type=int, default=10)
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    usage = Usage()
    llm = build_llm(usage=usage)

    print(f"\n{'='*70}\nIDEA: {a.idea}\n{'='*70}")

    # 0) Author interview  ▣ BLOCKING — the world is the author's call, not the model's
    idea = a.idea
    interview_answers: list[Answer] = []      # hard rules are read off this later
    if a.answers:
        import json

        # hard_rule must survive the replay file, or a prohibition the author
        # answered is demoted to a mere preference on the next run.
        prior = [Answer(topic=x["topic"], question=x.get("question", ""),
                        answer=x["answer"], hard_rule=bool(x.get("hard_rule", False)))
                 for x in json.loads(pathlib.Path(a.answers).read_text(encoding="utf-8"))]
        interview_answers = prior
        idea = enrich_idea(a.idea, prior)
        print(f"\n■ 작가가 정한 방향 {len(prior)}건 반영")
        for x in prior:
            print(f"    · {x.topic}: {x.answer[:70]}")
    elif a.interview:
        qs = generate_interview_questions(llm, a.idea, max_questions=a.questions)
        print(f"\n■ 설정 인터뷰 — {len(qs)}개 질문 (번호 입력 / 직접 입력 / 엔터=기본값)")
        answers: list[Answer] = []
        for i, q in enumerate(qs, 1):
            print(render_question(q, i, len(qs)))
            print(f"  └ 왜 묻는가: {q.why_it_matters}")
            try:
                raw = input("  답: ")
            except EOFError:
                raw = ""
            ans = resolve_answer(q, raw)
            answers.append(Answer(topic=q.topic, question=q.question, answer=ans,
                                  hard_rule=q.hard_rule))
            print(f"  → {ans}")
        interview_answers = answers
        idea = enrich_idea(a.idea, answers)
        (out).mkdir(parents=True, exist_ok=True)
        (out / "interview.md").write_text(
            "\n".join(f"## {x.topic}\nQ: {x.question}\nA: {x.answer}\n" for x in answers),
            encoding="utf-8",
        )
        print(f"\n■ 인터뷰 저장 → {out/'interview.md'}")

    # 1) IdeaIntake & GenreInference  ▣ human confirm
    profile, notes = infer_genre_profile(llm, idea)
    print("\n■ L0 GENRE PROFILE (추론)")
    print(f"  대상/등급 : {profile.audience} · {profile.content_rating.value}")
    print(f"  장르       : {profile.sub_genre}")
    print(f"  트로프     : {', '.join(profile.trope_checklist)}")
    print(f"  시점/시제  : {profile.pov} / {profile.tense}")
    print(f"  사이다 주기: {profile.target_catharsis_cadence}화 이내 · 최대 연속 고구마 {profile.max_consecutive_frustration_beats}")
    print(f"  금지        : {', '.join(profile.forbidden_anti_patterns)}")
    print(f"  추론 근거  : {notes}")

    # 2) NorthStarArchitect (best-of-N)  ▣ PREMISE GATE
    cands = generate_northstar_candidates(llm, idea, profile, n=a.candidates)
    print(f"\n■ L1 NORTH-STAR 후보 {len(cands)}개  (▣ 원래는 사람이 고르는 게이트)")
    for i, c in enumerate(cands, 1):
        mark = "◀ 선택" if i == a.pick else ""
        print(f"\n  [{i}] 《{c.title}》 {mark}")
        print(f"      전제   : {c.premise}")
        print(f"      갈등   : {c.core_conflict}")
        print(f"      엣지   : {c.protagonist_edge}")
        print(f"      엔진   : {c.episode_engine}")
        print(f"      반전   : {c.central_twist}")
    chosen = cands[a.pick - 1]
    north_star = to_north_star(chosen, profile)

    # 3) Canon & VoiceBible  ▣ voice lock
    canon, voice = init_canon_and_voice(llm, idea, profile, north_star)
    print(f"\n■ CANON + VOICE BIBLE")
    for name, c in canon.characters.items():
        tag = "주요" if c.is_main_cast else "조연"
        print(f"  [{tag}] {name} — {', '.join(c.immutable_descriptors) or '특징 미정'}")
        print(f"         말투: {c.voice.speech_register} / {c.voice.honorific_pattern} {c.voice.speech_tics}")
        if c.voice.exemplar_lines:
            print(f'         예시: "{c.voice.exemplar_lines[0]}"')
    print(f"  규칙: {[r.text for r in canon.world_rules if r.hard]}")
    print(f"  용어: {[(g.term, g.canonical_form) for g in canon.glossary]}")
    print(f"  문체: {voice.spec}")

    store = CanonStore(out / "_novel")
    store.initialize(genre_profile=profile, north_star=north_star, canon=canon, voice_bible=voice)

    # 4) EpisodePlanner
    beats = plan_episode(
        llm, episode_number=1, profile=profile, north_star=north_star, canon=canon,
        arc_map=seed_arc_map(llm, north_star), rhythm=store.load_rhythm(),
        foreshadow=store.load_foreshadow(), summary=Summary(),
    )
    print(f"\n■ L3 BEAT SHEET (1화)")
    print(f"  훅   : {beats.opening_hook}")
    for i, b in enumerate(beats.beats, 1):
        print(f"  {i}. [{b.beat_type.value}] {b.text}")
    print(f"  진전 : {beats.the_one_progression}")
    print(f"  절단 : {beats.closing_cliffhanger}")
    print(f"  떡밥 : {[(s.description, s.magnitude.value, s.due_by_ep) for s in beats.seeds_to_plant]}")

    if a.draft:
        pack = ContextPackBuilder().build(
            genre_profile=profile, north_star=north_star, voice_bible=voice, canon=canon,
            beat_sheet=beats, foreshadow=store.load_foreshadow(), rhythm=store.load_rhythm(),
            summary=Summary(), current_episode=1, previous_episode=None,
        )
        draft = draft_episode(llm, pack, max_tokens=32768)

        from novel_agent.reviser import revise_draft
        from novel_agent.style import forbidden_terms_from, lint_prose, style_score

        # The author's hard rules become a mechanical blocker (테스트 피드백 1-④):
        # without this the model can quietly ignore a prohibition and still score 100.
        forbidden = forbidden_terms_from(
            hard_rules=[x.answer for x in interview_answers if x.hard_rule],
            anti_patterns=profile.forbidden_anti_patterns,
        )
        if forbidden:
            print(f"\n■ 금기어 검사 대상 {len(forbidden)}건: {', '.join(forbidden[:6])}")
        before = style_score(draft.prose, target_chars=beats.length_target,
                             forbidden_terms=forbidden)
        print(f"\n■ 초고: {draft.char_count}자 · 문체 {before}/100 → 수정 루프 진입")
        result = revise_draft(llm, draft, pack, target_chars=beats.length_target,
                              forbidden_terms=forbidden)
        draft = result.draft
        path = out / "ep01.txt"
        path.write_text(draft.prose, encoding="utf-8")
        print(f"\n■ 1화 초고: {draft.char_count}자 (목표 {beats.length_target}) → {path}")
        if draft.fact_requests:
            print(f"  FACT 요청: {[f.question for f in draft.fact_requests]}")

        print(f"■ 수정 {result.iterations}회 · 문체 {before}/100 → {result.score}/100 · "
              f"{draft.char_count}자 · {'통과' if result.passed else '미통과(사람 확인 필요)'}")
        for v in result.remaining:
            print(f"    {v}")

    print(f"\n■ 비용: {usage.calls} calls · in {usage.input_tokens} / out {usage.output_tokens} "
          f"/ think {usage.thinking_tokens} · ${usage.usd:.4f} (₩{usage.krw:.0f})")
    # Cache tiers bill differently (read 0.1x, write 1.25x) — show them, or a
    # run that pays for writes it never reads back looks identical to a good one.
    print(f"■ 캐시: write {usage.cache_write_tokens} / read {usage.cached_tokens} "
          f"· 적중률 {usage.cache_hit_rate:.0%}")


if __name__ == "__main__":
    main()
