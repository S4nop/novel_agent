# 시작 가이드 — Getting Started

A practical guide to running this agent on **your own** idea, and to adapting it for a
different genre, language, or model. Read [`../DESIGN.md`](../DESIGN.md) for *why* it is
built this way; this document is *how to use it*.

---

> 🗺️ **Visual overview:** open [`workflow.html`](./workflow.html) in a browser for a
> one-page diagram of every stage, human gate, and what is / isn't built yet.

## 0. Read this first — what actually works today

Being honest up front saves you an afternoon.

| Capability | Status |
|---|---|
| Turn your freeform idea into genre profile / premise / canon / voice | ✅ works |
| Interview you about the world before inventing it | ✅ works |
| Plan and write **episode 1** in Korean, ~5,000자 | ✅ works |
| Mechanically lint prose for amateur/유치 habits (free, no tokens) | ✅ works |
| Bounded revise loop that improves the style score | ✅ works |
| Provider-agnostic model config (Anthropic / Gemini / any OpenAI-compatible) | ✅ works |
| **Write episode 2 informed by episode 1's *canon*** | ⚠️ **partial** |
| Continuity checking against canon (Track A) | ❌ not built |
| Craft judging by an LLM (Track B) | ❌ not built |
| Arc planning / arc audit / completion detection | ❌ not built (a 1-arc stub only) |
| LangGraph orchestration + durable checkpointing | ❌ not built |

**The important caveat:** the **Canonicalizer is not implemented**. When an episode is
written it is saved and the *next* episode does receive it verbatim (K=1), so short-range
continuity works. But nothing extracts state from it, so the canon (character locations,
learned facts, power levels, foreshadow ledger) does **not** advance. In practice: this is
a very good **first-episode / pilot** generator, not yet a 100-episode serial engine.
That gap is the single highest-value thing to build next.

---

## 1. Install

Requires Python 3.11+.

```bash
git clone <your-repo> && cd novel_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,llm,web]'
pytest                      # 133 tests, no API key needed
```

`pytest` passing without a key is intentional — the deterministic core (canon store,
ledgers, context pack, style lint) never touches the network.

---

## 2. Get a key and configure `.env`

```bash
cp .env.example .env
```

### Option A — Claude (the default)

Get a key at [console.anthropic.com](https://console.anthropic.com/settings/keys).

```ini
NOVEL_LLM_PROVIDER=anthropic
NOVEL_LLM_MODEL=claude-sonnet-5
NOVEL_LLM_API_KEY=sk-ant-...
NOVEL_PRICE_IN_PER_1M=3.00
NOVEL_PRICE_OUT_PER_1M=15.00
```

Optional: `NOVEL_LLM_EFFORT=high` (`low` | `medium` | `high` | `xhigh` | `max`) sets how
hard the model thinks. `high` is the default. Drop to `medium` to cut cost and latency on
a long run; the style score tells you quickly whether it hurt.

Three Claude-specific behaviors worth knowing, all handled in `llm.py`:

- **Thinking shares the output budget.** Adaptive thinking is on by default and counts
  against `max_tokens` along with the prose, so the adapter adds `THINKING_HEADROOM` to
  every request. If you see `max_tokens 초과로 응답이 잘렸습니다`, raise it or lower `effort`.
- **Prompt caching is explicit and fragile.** The story bible is sent as one cached
  system block, and cached tokens cost 10% of the normal input price. Caching is a
  *prefix match* — one nondeterministic byte (a timestamp, an unsorted dict) and the
  whole discount silently disappears. The cost panel shows the hit rate. **0% on the
  first few calls of a new project is normal** (each prefix has to be written before it
  can be read, and plan calls and prose calls cache separately because the JSON schema
  is part of the prefix). A rate still stuck at 0% after ~5 calls is the real warning.
- **A refusal is not an error.** It arrives as a normal `200` with
  `stop_reason: "refusal"`, so the adapter checks it before reading the text. Anthropic
  has no safety-settings knob — dark content needs no configuration, but explicit sexual
  content is prohibited by policy regardless of framing (already out of scope here).

### Option B — Gemini

Get a key at [aistudio.google.com](https://aistudio.google.com/apikey).

```ini
NOVEL_LLM_PROVIDER=gemini
NOVEL_LLM_MODEL=gemini-3.6-flash
NOVEL_LLM_API_KEY=AIza...
NOVEL_PRICE_IN_PER_1M=1.50
NOVEL_PRICE_OUT_PER_1M=7.50
```

> ⚠️ **Use a paid key.** The free tier is capped at ~20 requests/day per model (one
> episode costs 7–10), and per Google's terms **the free tier trains on your submitted
> content** — a real problem for a novel you intend to publish.

`provider=gemini` uses the native SDK deliberately: Gemini's OpenAI-compatible endpoint
**rejects `safety_settings`**, and you need those relaxed for dark genre fiction
(revenge, violence, villain POV). See `llm.py`.

### Option C — any OpenAI-compatible provider

```ini
NOVEL_LLM_PROVIDER=openai
NOVEL_LLM_MODEL=kimi-k3
NOVEL_LLM_PRESET=moonshot        # openai | moonshot | deepseek | upstage | openrouter
NOVEL_LLM_API_KEY=sk-...
NOVEL_PRICE_IN_PER_1M=3.00       # keep the cost meter honest
NOVEL_PRICE_OUT_PER_1M=15.00
```

Local models work too:

```ini
NOVEL_LLM_PROVIDER=openai
NOVEL_LLM_MODEL=qwen3
NOVEL_LLM_BASE_URL=http://localhost:11434/v1
NOVEL_LLM_API_KEY=ollama
NOVEL_PRICE_IN_PER_1M=0
NOVEL_PRICE_OUT_PER_1M=0
```

**Model choice matters more than anything else you configure.** The one capability that
decides everything is *native-fluent prose in your target language*. Test it before
committing (see §7).

---

## 3. First run — the web console

```bash
python -m novel_agent.web        # → http://127.0.0.1:8000
```

Work top to bottom. Each stage is a separate button so you can inspect and retry one
step **without re-spending tokens on the earlier ones**.

### 연결 테스트
Click it first. A green `OK` means provider + model id + key are all good. If it fails
here, nothing else will work — check §8.

### 1 · 아이디어
Type one line — a premise, a vibe, a mashup. Genuinely one line is enough:

> `네오 조선의 흑인 홍길동, 코믹`

Press **프로젝트 생성**. State persists to `data/projects/<id>/`, so you can close the
browser and come back.

### 2 · 설정 인터뷰 ← **the most important step**
Press **질문 생성**. The agent asks ~8–10 questions tailored to *your* idea — tone,
technology level, how much of the source material to keep, scale of conflict, rating,
and crucially **what must NOT exist in this world**.

Answer them, then **답변 저장**.

> **Do not skip this.** It is the difference between a good result and a bad one.
> Skipping it, the agent invents the world *elaborately* and it reads as childish. In our
> own testing, an un-interviewed run produced 38 invented setting terms
> (`상평통보 코인`, `넙적패드`…) and the author called the result "childish and
> cringeworthy." The same idea, *with* the interview, produced a 3-term glossary and a
> far better premise. **Your answers are the single biggest quality lever.**

Tips for answering well:
- Be concrete about what you **don't** want. "No X" constrains the model far better than "make it good."
- If an option nearly fits, type your own text in the free-input box instead of picking.
- The answers are saved; you can replay them later with the CLI (`--answers`).

### 3 · 장르 + 전제 후보
Press **L0 추론 + 후보 3개 생성**. You get an inferred genre profile (audience, rating,
사이다 cadence, forbidden anti-patterns) plus **3 structurally different premises**.

**Read all three and pick deliberately** — this is the premise gate, and it is yours, not
the model's. Click the candidate you want, then **선택한 전제로 캐논 생성**. That builds
the character cards, voice cards, world rules, and glossary.

Sanity check the canon before writing: a glossary of **3–5 terms is healthy; 20+ means
the model is over-inventing** — go back and tighten your interview answers.

### 4 · 에피소드 집필
Set 화 = 1, leave 수정 루프 on, press **집필**. Expect **1–3 minutes** (the revise loop
makes several calls).

You get the prose plus a style score, `초고 → 최종`, character count vs target, and every
violation.

### 문체 린트 — free, use it constantly
Paste **any** prose and score it instantly, no tokens. Use it to:
- calibrate: paste a published novel you admire and see what it scores
- check your own hand-edits before committing them
- compare two drafts objectively

---

## 4. Reading the style score

`style.py` implements ~22 mechanically checkable rules drawn from Korean 웹소설
practitioner sources (see [`style-spec.md`](./style-spec.md)).

| Score | Meaning |
|---|---|
| 85+ | passes the gate; publishable mechanics |
| 70–84 | readable but has habits a reader will notice |
| < 70 | amateur tells are conspicuous |

Severities: **blocker** (self-praise narration, `!!`, 라노벨체 감탄사) → **major**
(감정 직설, 번역체, 인물 라벨링, dialogue ratio < 40%) → **minor**.

The score is a *floor check, not a taste judge.* It cannot tell you whether the story is
enjoyable. **You still have to read it.** A 95/100 episode can be boring.

Two guardrails learned the hard way:
- The lint had **false positives** early on (matching `아주` inside `되찾아주셨다`; flagging
  action staccato as monotony). If it flags something you believe is good writing,
  **check the rule before obeying it** — and consider fixing the rule.
- The score is magnitude-aware on purpose: 27 exclamations and 12 must not score the same,
  or the revise loop can't see progress.

---

## 5. The CLI (repeatable runs)

```bash
# interactive interview, then setup + episode 1
python scripts/run_setup.py --idea "당신의 아이디어" --interview --draft --out data/mine

# replay saved answers (no re-asking) and pick candidate 2
python scripts/run_setup.py --idea "당신의 아이디어" \
    --answers data/answers.json --pick 2 --draft --out data/mine

# re-edit an existing episode from its saved canon store
python scripts/revise_episode.py --run data/mine --iterations 3
```

`data/answers.json` is a plain list of `{topic, question, answer}` — write it by hand
once and every future run inherits your preferences.

---

## 6. Adapting it to your project

### A different genre
Nothing to change. Genre is **runtime data**, not code — `GenreProfile` is inferred from
your idea, and every rubric reads from it. There is no genre `switch` anywhere.

### A different language
Three files carry Korean assumptions:
- `prompts.py` — `STYLE_RULES` (the craft directives) and the drafting system prompt
- `style.py` — the word lists and thresholds are Korean-specific
- `nodes.py` — `RESTRAINT` and the planner prompts

Translate the prompts and **rebuild the lint from your market's craft norms**. Do not
just translate the rules: what reads as amateur differs per market. Also re-check
`episode_length_target` (Korean web novels are measured in 자, 공백 포함).

### A different model
Edit `.env` only. If prose quality drops, that is the model — validate with §7 before
blaming the pipeline.

### Tuning the lint
Add or relax rules in `style.py`. Each rule is a few lines and has a unit test in
`tests/test_style.py`. **Add a test whenever you change a threshold** — that is what
caught the false positives.

---

## 6.5 프롬프트 튜닝 (for a PM / editor — no Python needed)

**Prompts are the main tuning surface of this product, and you do not need to read
code to change them.** All prompt text lives in [`../prompts/`](../prompts/) as plain
`.md` files — see [`prompts/_README.md`](../prompts/_README.md).

Two ways to edit:

1. **Web console → 프롬프트 편집 panel.** Pick a prompt, edit, save. Validation runs on
   save and it takes effect on the next run. Costs no tokens.
2. **Edit `prompts/*.md` directly**, then restart the server.

### The one rule

`${이름}` markers are **placeholders** — real values (the idea, the genre, the draft)
get substituted in at runtime. **Do not delete them.** If you do, the save is rejected
with a message naming the missing one, because a prompt that quietly lost `${idea}`
would degrade everything downstream with no error.

Everything else is free text. Write in Korean.

### Which file changes what

| File | Effect | Impact |
|---|---|---|
| `style_rules.md` | all the anti-유치함 craft rules | ★★★ highest |
| `drafter_system.md` | the writer's role, length, POV | ★★★ |
| `restraint.md` | how hard to suppress over-invented settings | ★★ |
| `northstar.md` | how premise candidates are generated | ★★ |
| `canon_init.md` | characters, world rules, glossary, voice | ★★ |
| `episode_plan.md` | beat sheet: hook, beats, cliffhanger | ★★ |
| `interview_request.md` | what the agent asks the author | ★★ |
| `genre_inference.md` | how genre is read from the idea | ★ |
| `revise_instruction.md` | how fixes are demanded in the revise loop | ★ |
| `analyst_system.md`, `interview_system.md`, `voice_spec_guidance.md` | role framing | ★ |

### A safe tuning loop

1. Copy `prompts/` somewhere and run with `NOVEL_PROMPTS_DIR=/path/to/copy` — the
   original set stays untouched, so you can A/B two prompt sets.
2. Generate an episode, then paste it into the **문체 린트** panel (free) for an
   objective before/after score.
3. Read it as well — the lint checks mechanics, not whether it is enjoyable.
4. To roll back: `git checkout prompts/`.

---

## 7. Validate your model before you trust it (do this once)

Adapted from the project's Phase 0 gate. Roughly an hour, and it prevents weeks of
misplaced effort:

1. **Prose test.** Generate episode 1 with your chosen model. Then generate the *same*
   episode with one alternative model. Blind-rank them against a handful of **real,
   no-name** works from your target platform (not bestsellers — those are edited and
   survivorship-biased). Your model should beat the no-name bar.
2. **Content test.** Confirm the model will write your genre's core content without
   softening. Refusals and quiet sanitizing are both failures.
3. **Length test.** Confirm it can hit your target length in one pass. Some models
   collapse to half-length under terse style instructions.

If your model fails #1 or #2, no amount of pipeline work fixes it.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `NOVEL_LLM_API_KEY is not set` | `.env` missing or wrong var name. Vars are `NOVEL_LLM_*` (not `NOVEL_GEMINI_*`). |
| `429 rate_limit_error` / `RESOURCE_EXHAUSTED` | Quota. On Anthropic, check your tier's limits in the console (Claude Sonnet 5 has its own pool). Gemini free tier = ~20 req/day **per model**, reset midnight **US Pacific**. |
| `400 … temperature` / `budget_tokens` | Sonnet 5 rejects sampling params and thinking budgets. Don't add them — steer with the prompt, size with `NOVEL_LLM_EFFORT`. |
| Cost panel shows a 0% cache hit rate | The stable prefix stopped being byte-stable, so every call pays full input price. Something nondeterministic leaked into the ContextPack prefix. |
| `503 UNAVAILABLE` | Provider demand spike. The adapter already retries with backoff; wait or switch model. |
| Empty / truncated reply | **Reasoning models spend `max_output_tokens` on thinking first.** A small budget returns garbage — raise `max_tokens`. |
| `모델이 응답을 거부했거나 비어 있음` (422) | Refusal or filter. On Anthropic the message carries `stop_reason=refusal` and a category; on Gemini check the safety settings in `llm.py`. Explicit sexual content is out of scope by policy on both. |
| Draft is half the target length | Style rules make models terse. The revise loop treats shortfall as a finding — raise `iterations`. |
| Glossary has 20+ invented terms | Interview answers were too loose. Explicitly state what must not exist. |
| Prose feels childish | Almost always the *setup*, not the prose: over-invented world, or a tone you never specified. Redo the interview. |
| Revise loop never improves | Check the score is moving at all. If it is stuck at the same number, a rule may be firing that the model cannot fix by instruction (e.g. rhythm counts). |

---

## 9. Where to extend next

In value order, with the design section that specifies each:

1. **Canonicalizer** (`DESIGN.md §3`) — extract a `CanonDelta` from an accepted episode
   and commit it. *This is what turns the tool into a serial engine.* The artifact and the
   single-writer store already exist; only the extraction node is missing.
2. **Track A continuity checker** (`§5`) — claims-vs-canon. Our testing showed the drafter
   invents facts and does **not** self-report them, so this is load-bearing, not optional.
3. **Human-accept gate + reject path** (`§3`, invariant #7).
4. **LangGraph orchestration + durable checkpointer** (`§5`) — needed for pause/resume
   across days. Note invariant #13: never put an expensive LLM call in the same node as an
   `interrupt()`.
5. **Arc planner / arc auditor** (`§4`) — for multi-arc stories and completion detection.

Read `DESIGN.md §4` ("Composition invariants") before adding anything — those 14 rules
were derived from an integration review that found 12 real ways the components fail to
compose.

---

## 10. Cost expectations

Measured on Gemini 3.6 Flash at $1.50/$7.50 (₩ at 1,400/USD). **On the current default,
Claude Sonnet 5 at $3/$15, roughly double these figures** before prompt caching — the
table has not been re-measured since the switch:

| Operation | Calls | Cost |
|---|---|---|
| Interview question generation | 1 | ~₩40 |
| Genre + 3 premise candidates | 4 | ~₩130 |
| Canon + voice bible | 1 | ~₩50 |
| Episode: plan + draft + 2–3 revise passes | 5–6 | ~₩200–430 |
| **Full setup + episode 1** | **~10** | **~₩430** (≈₩900 on Sonnet 5) |

Generation cost is trivial next to any commercial outcome. Per this project's market
research, **distribution — not cost — is the binding constraint.** Do not optimize the
wrong variable.
