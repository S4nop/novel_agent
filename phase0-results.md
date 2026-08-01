# Phase 0 — execution results (2026-07-29)

Model: **Gemini 3.6 Flash** (single-model commitment, DESIGN §5).
Generated through the real pipeline (canon store → ContextPackBuilder → Drafter),
not a hand-held chat session. Reproduce with `python scripts/phase0_prose_spike.py`.

## Task 5 — content-fit test (in Korean): ✅ PASS

- Gemini **accepts relaxed safety settings** and writes unsanitized dark Korean
  content (사이다 revenge, graphic violence, villain-adjacent cruelty) — verified live.
- ⚠️ **Verified correction to the plan:** safety settings are **NOT** supported on the
  OpenAI-compat endpoint — it returns `400 Unknown name "safety_settings"`.
  The adapter therefore uses the **native `google-genai` SDK**, where
  `safetySettings` / `responseSchema` / `countTokens` are first-class.
  (This invalidates the earlier "migration = base_url swap" assumption.)
- Relaxed: HARASSMENT / HATE_SPEECH / DANGEROUS_CONTENT. Never SEXUALLY_EXPLICIT.

## Task 4 — prose spike + Flash tier bake-off: generation ✅ / blind rank PENDING

Both tiers produced a full-length episode 1 to the beat sheet, in target length,
at trivial cost (~₩62/episode).

| Metric | gemini-3.6-flash | gemini-3.5-flash |
|---|---|---|
| Length | 5,924자 (114% of 5,200 target) | 6,050자 (116%) |
| Paragraphs / avg length | 139 / 41자 | 207 / 28자 |
| **Canon location honored** (원룸) | ✅ 4 uses, 0 off-canon | ❌ 0 uses — relocated him to a 지하실 |
| **Invented facts** | none | ❌ "스물네 살" (age not in canon) |
| Immutable descriptor (흉터) | 3 uses | 2 uses |
| Cliffhanger as planned | ✅ | ✅ |
| Cost / episode | ₩62 | ₩62 |

**Verdict: 3.6 Flash wins on canon fidelity** — the dimension the whole
architecture exists to protect. 3.5 Flash writes punchier mobile-formatted
paragraphs (avg 28자 vs 41자) but broke canon twice in a single episode.
→ **Stay on `gemini-3.6-flash`.** Consider borrowing 3.5's shorter-paragraph
rhythm via the VoiceBible/style spec rather than the model.

Qualitatively, 3.6 Flash **used the ContextPack's canon**: it opened in the
VoiceBible's exact cadence ("천장이 낮았다. / 25년 전의 천장이었다."), worked in the
immutable descriptor (왼쪽 눈썹 흉터), used the glossary terms (게이트/각성자/포식),
and landed the planned cliffhanger. The write-path architecture demonstrably
steers the prose.

**Still outstanding (human judgment, cannot be self-assessed):** the blind rank
vs (i) random no-name free-board 화1s (must-beat, top quartile) and (ii) 2–3 known
hits (ceiling, no stop authority), ≥3 raters, pre-registered threshold. Drafts for
ranking: `data/phase0/ep01_*.txt`. **The go/no-go is not decided until that runs.**

## Re-run on a user-supplied premise (2026-07-29) — the obviousness was an INPUT problem

The first spike's episode read as generic. Diagnosis: **the premise and beat sheet
were hand-authored by the developer**, and were the most over-farmed 남성향 template
(회귀 + F급 최약체 + 흡수 능력 + 미래지식 + 서열역전). The model executed a cliché
faithfully. That spike therefore validated *prose mechanics*, not distinctiveness —
a methodological confound, since regression-to-genre-mean is the plan's #1 named risk.

Re-ran the same pipeline with the author's own idea — **"네오 조선의 흑인 홍길동, 코믹"** —
and with the setup chain actually built, so **nothing creative is hand-authored**:

    Idea → GenreProfile (inferred) → 3× NorthStar candidates → Canon + VoiceBible
         → BeatSheet → Draft   [7 calls, ₩235 total]

Results (`data/run/`, reproduce with `scripts/run_setup.py`):

- **Genre inference worked**: "네오 조선 SF 코미디 활빈 액션", 15+, 사이다 every ≤3 eps,
  max 1 consecutive 고구마. Notably it *independently* added
  "인종/신분 차별을 진지한 신파로 풀어내는 전개" to `forbidden_anti_patterns` — i.e. it chose
  satire over melodrama on its own, which is the right craft call for this premise.
- **Best-of-N produced genuinely distinct structures**, not three coats of paint:
  (1) 활빈 heist, (2) a *legal* tax-repossession civil servant mistaken for the
  legendary outlaw, (3) hacking nobles' cyborg bodies into forced philanthropy.
  Candidate 2 is arguably the freshest premise of the three.
- **Canon carried real constraints**: 3-minute nano-core overheat limit, 족보-data
  security gating, an 80%-redistribution-in-24h 활빈당 rule — all with costs, so
  payoffs aren't deus ex machina.
- **Episode 1: 5,574자**, avg 35자 paragraphs (mobile-shaped), and canon-faithful:
  조최숙 ×21, 족보 ×8, 활빈당 ×6, the 195cm immutable descriptor ×2, voice tics
  에헴 ×5 / "아버지를 아버지라" ×2 / 형님 ×5, the 3-minute power cost referenced,
  and it landed the planned cliffhanger.

**Conclusion:** same pipeline, same model, distinctive premise → distinctive output.
This is direct evidence for the design's core claim: *the scaffolding guarantees
consistency and trope-correctness; distinctiveness comes from the premise and voice* —
i.e. from the human taste-owner, which is why the premise gate is BLOCKING.

## Load-bearing finding for Phase 1a

**Neither model self-reported invented facts.** The Drafter is instructed to emit
`[[FACT: …]]` rather than invent canon; 3.5 Flash invented an age and a location
and emitted **zero** fact requests. So:

- Drafter self-reporting is **unreliable** — it cannot be the continuity safeguard.
- The **Track A continuity checker is therefore load-bearing, not optional**, and
  correctly sits in Phase 1a scope. Invariant #9's fact gate catches only what the
  model volunteers; canon contradictions must be caught by checking the draft
  against canon.

## Cost note

Thinking tokens dominate short calls (e.g. 1,316 thinking vs 125 output on a
3-sentence probe) and bill as output. Full episode: ~1.1k in / ~3.8k out / ~1.9k
thinking ≈ ₩62. Cost remains trivial vs the plan's ₩700–1,800/episode estimate —
the economics memo's "distribution, not cost, is the constraint" holds.
