"""Phase 0 task 4 — prose go/no-go + Flash tier bake-off (DESIGN §7).

Generates episode 1 through the REAL pipeline (canon store → ContextPackBuilder
→ Drafter) with each candidate model, so the blind rank judges what the actual
system produces — not a hand-held chat session.

    python scripts/phase0_prose_spike.py [--models a,b] [--out DIR]

Outputs one .txt per model for blind ranking, plus a cost/length report.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from novel_agent.artifacts import (  # noqa: E402
    Beat, BeatSheet, BeatType, Canon, CharacterCard, GenreProfile, GlossaryEntry,
    NorthStar, Summary, VoiceBible, VoiceCard,
)
from novel_agent.canon_store import CanonStore  # noqa: E402
from novel_agent.context_pack import ContextPackBuilder  # noqa: E402
from novel_agent.drafter import draft_episode  # noqa: E402
from novel_agent.llm import LLMRefusal, Usage, build_llm  # noqa: E402

# ── the validation premise (남성향 헌터/회귀 — first proving ground, DESIGN §7) ──
GENRE = GenreProfile(
    audience="남성향",
    sub_genre="현대 판타지 / 헌터물 · 회귀",
    trope_checklist=["회귀", "각성", "성장형 고유능력", "서열 역전", "히든 정보 선점"],
    episode_length_target=5200,
    pov="1인칭",
    tense="과거",
    register_baseline="문어체 서술 + 짧은 문단",
    target_catharsis_cadence=3,
    max_consecutive_frustration_beats=2,
    forbidden_anti_patterns=["장황한 설정 설명", "주인공의 무의미한 굴욕 반복", "번역투"],
)

NORTH_STAR = NorthStar(
    premise="25년 뒤 인류 멸망을 목격한 최약체 F급 헌터가 최초의 게이트가 열리기 3일 전으로 회귀한다",
    core_conflict="미래를 아는 단 한 사람 대 이미 정해진 파멸의 흐름",
    protagonist_edge="죽인 몬스터의 능력을 흡수하는 고유 능력 '포식' + 25년치 미래 지식",
    central_twist="회귀의 대가로 그가 구하려던 사람의 존재가 세계에서 지워졌다",
    intended_ending="멸망의 근원을 파괴하고, 지워진 존재를 되찾는다",
    episode_engine="미래 지식으로 다음 게이트를 선점 → 포식으로 성장 → 서열과 판도를 뒤집는다",
    power_system="각성 등급 F~S. '포식'은 처치한 개체의 스킬 하나를 흡수하되, 흡수마다 인간성이 마모된다",
    hard_rules=[
        "죽은 자는 어떤 수단으로도 되살아나지 않는다",
        "포식은 같은 개체에게 한 번만 발동한다",
        "미래 지식은 그가 직접 본 25년까지만 유효하다",
    ],
)

HERO = CharacterCard(
    name="서준혁",
    is_main_cast=True,
    immutable_descriptors=["짧은 검은 머리", "왼쪽 눈썹의 오래된 흉터", "마른 체구"],
    voice=VoiceCard(
        speech_register="건조하고 냉정, 감정을 삼키는 말투",
        honorific_pattern="속마음은 반말, 타인에게는 짧은 존댓말",
        speech_tics=["…쯧", "그래서, 결론은"],
        exemplar_lines=["이번엔 순서를 바꾼다.", "알고 있다. 그래서 여기 있다."],
    ),
    personality="냉정한 계산가지만 사람을 버리지 못한다",
    goals=["3일 뒤 첫 게이트를 독식한다"],
    secrets=["회귀자라는 사실", "멸망의 시작이 사람의 배신이었다는 것"],
    current_location="서울, 낡은 원룸",
    condition="회귀 직후 극심한 두통",
    power_level="F급 (미각성 상태)",
)

CANON = Canon(
    characters={HERO.name: HERO},
    glossary=[
        GlossaryEntry(term="Gate", canonical_form="게이트"),
        GlossaryEntry(term="Awakened", canonical_form="각성자"),
        GlossaryEntry(term="Predation", canonical_form="포식"),
    ],
)

VOICE = VoiceBible(
    spec=(
        "짧은 문단과 빠른 호흡. 감정은 설명하지 않고 행동과 감각으로 보여준다. "
        "정보는 필요한 순간에만 한 줄로 흘린다. 문장 끝을 끊어 긴장을 만든다."
    ),
    exemplar_passages=[
        "천장이 낮았다. 25년 전의 천장이었다.",
        "손끝이 떨렸다. 두려움이 아니라, 확인이었다.",
    ],
)

BEATS = BeatSheet(
    episode_number=1,
    opening_hook="눈을 떴을 때, 그는 25년 전 자신의 원룸 천장을 보고 있었다",
    the_one_progression="회귀를 확신하고, 3일 뒤 첫 게이트를 선점하기로 결심한다",
    beats=[
        Beat(text="멸망의 마지막 순간에서 눈을 뜬다 — 낯익은 천장, 낡은 휴대폰의 날짜", beat_type=BeatType.SETUP),
        Beat(text="회귀를 의심하다 뉴스와 날짜로 확인, 25년치 기억이 자산임을 자각", beat_type=BeatType.REVEAL),
        Beat(text="미래 지식으로 사소한 사건을 정확히 맞혀 자신의 기억을 검증한다", beat_type=BeatType.PAYOFF),
        Beat(text="3일 뒤 열릴 첫 게이트의 위치를 떠올리고 준비를 시작한다", beat_type=BeatType.ESCALATION),
    ],
    closing_cliffhanger="예정보다 하루 일찍, 창밖에서 균열이 벌어지는 소리가 들린다",
    length_target=5200,
    pov="1인칭",
    entities_present=["서준혁"],
)


def run(models: list[str], out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    store = CanonStore(out_dir / "_novel")
    store.initialize(genre_profile=GENRE, north_star=NORTH_STAR, canon=CANON, voice_bible=VOICE)

    pack = ContextPackBuilder().build(
        genre_profile=store.load_genre_profile(),
        north_star=store.load_north_star(),
        voice_bible=store.load_voice_bible(),
        canon=store.load_canon(),
        beat_sheet=BEATS,
        foreshadow=store.load_foreshadow(),
        rhythm=store.load_rhythm(),
        summary=Summary(),
        current_episode=1,
        previous_episode=None,
    )
    print(f"ContextPack — prefix {pack.prefix_tokens} / suffix {pack.suffix_tokens} chars\n")

    for model in models:
        usage = Usage()
        llm = build_llm(model=model, usage=usage)
        print(f"── {model} ──")
        try:
            draft = draft_episode(llm, pack, max_tokens=32768)
        except LLMRefusal as e:
            print(f"   REFUSED/FILTERED: {e}\n")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"   ERROR {type(e).__name__}: {str(e)[:200]}\n")
            continue

        path = out_dir / f"ep01_{model}.txt"
        path.write_text(draft.prose, encoding="utf-8")
        target = BEATS.length_target
        print(f"   길이: {draft.char_count}자 (목표 {target}, {draft.char_count/target:.0%})")
        print(f"   fact requests: {len(draft.fact_requests)}")
        print(f"   tokens in/out/thinking: {usage.input_tokens}/{usage.output_tokens}/{usage.thinking_tokens}")
        print(f"   cost: ${usage.usd:.4f} (₩{usage.krw:.0f})")
        print(f"   → {path}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="claude-sonnet-5")
    ap.add_argument("--out", default="data/phase0")
    a = ap.parse_args()
    run([m.strip() for m in a.models.split(",") if m.strip()], pathlib.Path(a.out))
