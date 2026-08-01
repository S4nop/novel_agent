# 웹소설 문체 규격 (Korean web-novel prose spec)

Derived 2026-07-29 from Korean 웹소설 practitioner sources after the author judged
our drafts 유치하고 오글거림. Enforced by `src/novel_agent/style.py` (mechanical
rules) and `src/novel_agent/prompts.py` (`STYLE_RULES`, in the drafting prompt).

## The root cause, per the sources

유치함은 어휘 문제가 아니라 **서술이 독자 대신 판단해 주는 것**이다. All six research
lenses converged on the same short list:

1. 감정을 이름으로 부르기 (`분노했다`, `두려웠다`)
2. 1인칭 화자의 자기 칭찬 서술 (`195센티미터의 우람한…`, `역시 나`)
3. 설명충 지문 / 인물 라벨링 (`전형적인 탐관오리였다`)
4. 부호·감탄사로 텐션 올리기 (`!!`, `?!`, `크아아아앙`, `오이오이`)
5. 빌드업 없는 선언조 대사 (`이 몸 혼자서 1만 명 몫을 보여주마`)
6. 개그에 작가와 인물이 먼저 웃기

## The comedy rule (highest leverage for this work)

> **작중 인물도 서술자도 개그에 동조하지 않는다. 웃는 것은 독자뿐이다.**

- 등장인물에게 그 상황은 심각하거나 짜증나거나 곤란한 일이어야 한다.
- 펀치라인 앞뒤로 문체·시제·톤을 바꾸지 않는다 (끝까지 덤덤한 평서체).
- 펀치라인 뒤에 해설·감상·리액션을 붙이지 않는다. 펀치라인이 문단의 마지막 문장이고 곧 끊는다.
- 웃음은 드립이 아니라 상황에서: 정보 비대칭 / 하필 지금 나타난 인물 / 목적과 수단의 낙차.
- 주인공은 웃기려 들지 않는다. 목표에 진지하게 매달리고 웃음은 그 부산물이다.

## Mechanically enforced rules (`style.py`)

| 규칙 | 한도 | 심각도 |
|---|---|---|
| 겹부호 `!!` `?!` `??` | 0 | blocker |
| 자기 칭찬 서술 (잘생긴/천재/역시 나/우람한…) | 0 | blocker |
| 라노벨체 감탄사 (오이오이/흐응/에엥/후후/헤에…) | 0 | blocker |
| 지문 내 느낌표 | 0 | major |
| 느낌표 총량 | ≤8 / 5,200자 | major |
| 강도부사 (정말/너무/매우/엄청/굉장히/완전히/무려) | 0 | major |
| 번역체 (~것이었다/~인 것이다/~하게 되었다/~에 의해/~로 인해) | 0 | major |
| 감정 직설 (분노했다/두려웠다/당황했다…) | 0 | major |
| 인물 라벨링 (전형적인/대표적인) | 0 | major |
| 설명충 관용구 (알다시피/~로 유명한/그 유명한) | 0 | major |
| 비장 선언 어휘 (하리라/운명/숙명/이 몸/보여주마) | 0 | major |
| 지문에 사극체 (하였다/이니라/노라) — 대사에만 허용 | 0 | major |
| 동일 종결어미 연속 | ≤2문장 | major |
| 다다다체 (평서 종결 연속) | ≤5문장 | major |
| 대사 줄 비중 | ≥40% | major |
| 연속 지문 줄 | ≤5줄 | major |
| 평균 문장 길이 | ≤35자 | major |
| 모음/자음 늘여쓰기 | ≤1 | major |
| `나는`으로 시작하는 문장 | ≤10% | minor |
| 50자 초과 문장 | ≤10% | minor |
| 벽돌 문단 (120자 초과) | 0 | minor |
| 물결표 `~`, 비표준 말줄임표 | 0 | minor |

`style_score` = 100 − (blocker×12 + major×5 + minor×2). 프로 기준 통과선 **85**.

## Judgment-only rules (not lintable — for the CraftJudge, Phase 1b)

- 설정은 지문으로 설명하지 말고, 인물이 규칙을 어겨 손해를 입는 장면으로 드러낸다.
- 악역 첫 등장은 구체적 행위 1개 + 숫자나 물건 1개로만.
- 서열·긴장은 존대/반말의 비대칭으로 처리하고 지문으로 해설하지 않는다.
- 대사만 골라 읽어도 누가 말하는지 알 수 있어야 한다 (인물별 종결어미 고정).
- 센 대사 앞에 주인공이 실제로 치른 손해가 있어야 한다 (빌드업 없는 명대사 금지).
- 1인칭 지문은 배경 묘사가 아니라 '이것이 나에게 무슨 뜻인가'라는 판단으로 채운다.

## Measured effect

Measured with the *calibrated* lint (see "Lint calibration" below):

| 단계 | 문체 점수 | 분량 | blockers |
|---|---|---|---|
| ① Before — generic prompt (`data/run/ep01.txt`) | **20/100** | 5,574자 | 2 |
| ② After — spec in prompt + revise loop (`data/run2/ep01.txt`) | **95/100 ✅** | 5,121자 | 0 |

Eliminated: 자기 칭찬 서술, 겹부호, 비장 선언 대사, 인물 라벨링, 번역체;
느낌표 71 → 0; 대사 줄 비중 26% → 40%+. Only one 다다다체 run remains.

## Lint calibration (three false-positive classes found and fixed)

The first version of the lint penalised *correct* prose. Each fix is grounded in
the same sources:

1. **Substring matching across morpheme boundaries.** `아주` matched inside
   `되찾아주셨다`. Standalone adverbs/interjections/labels now use a Hangul
   word-boundary matcher; verb-ending patterns (번역체, 사극체) keep substring matching.
2. **Scope.** 강도부사 / 자기 칭찬 / 라벨링 are *narration* faults. In dialogue
   `"시스테마가 아주 조선시대네."` is natural speech and good comedy — not a defect.
3. **Action staccato ≠ monotony.** The sources prescribe
   *"액션·반전 구간은 2~8자 극단 단문을 3~4개 연타"*, so a burst of short narration
   lines is craft. 다다다체 now counts only sentences ≥15자, and the narration-wall
   rule measures **characters (>300자)** rather than line count.

Lesson worth keeping: a style lint that is not itself validated will push the
reviser to fight good writing. The 80 → 95 jump came from fixing the *measurement*,
not the prose.

## Known floor of instruction-based revision

Three revise passes could not clear the remaining 다다다체 run. Mechanical rhythm
faults (counting consecutive endings, breaking narration blocks) are not reliably
fixable by instructing the model — they are candidates for a deterministic
rewrite pass or a targeted sentence-level edit, not more prompting.

## Sources

Korean practitioner/craft sources consulted (연재 커뮤니티 작법 팁, 편집자 조언,
나무위키 소설 작법/문체 · 웹소설/특징/서술 · 문장 부호, 대사 작법 가이드, 독자 토론).
Full source list with URLs: see the research record in the session transcript
(`webnovel-prose-craft-research` workflow, 6 lenses × ~12 rules each).
