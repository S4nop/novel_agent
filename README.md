# novel-agent

AI agent that writes serialized **Korean web novels (웹소설)**.

### 👉 New here? Start with the [**시작 가이드 / Getting Started**](./docs/GUIDE.md)
### 🗺️ Want the big picture first? Open [**docs/workflow.html**](./docs/workflow.html) — a one-page diagram of the whole pipeline (open it in a browser)

Also: architecture & rationale [`DESIGN.md`](./DESIGN.md) · prose rules
[`docs/style-spec.md`](./docs/style-spec.md) · Korean-market research
[`phase0-market-memo.md`](./phase0-market-memo.md) · measured results
[`phase0-results.md`](./phase0-results.md)

**Current state:** a strong **first-episode / pilot** generator (idea → interview → genre →
premise → canon → episode 1 with a style-linted revise loop) **plus a working
continuity gate**: Track A checks every draft against canon and hard-blocks a
contradiction, and settings you forbade in the interview are enforced as a blocker rule.
The Canonicalizer's deterministic half persists pacing debt, 떡밥 deadlines, the rolling
summary and episode records, and 떡밥 can now be paid off. Its LLM half — extracting a
`CanonDelta` from accepted prose — is still missing, so character/world facts do not yet
accumulate automatically. See the guide's status table.

- **Users are Korean → the agent speaks Korean** (interview, gates, prose).
- Human-in-the-loop **co-writer**: the author locks the premise, the voice, and the world.
- **Provider-agnostic** — the model is chosen entirely in `.env`.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,llm,web]'
cp .env.example .env            # then paste your API key
python -m novel_agent.web       # → http://127.0.0.1:8000
```

## Configuration (`.env`)

```ini
NOVEL_LLM_PROVIDER=anthropic       # anthropic (default) | gemini | openai
NOVEL_LLM_MODEL=claude-sonnet-5
NOVEL_LLM_API_KEY=...
# NOVEL_LLM_EFFORT=high            # low | medium | high | xhigh | max (anthropic only)
# NOVEL_LLM_PRESET=moonshot        # openai | moonshot | deepseek | upstage | openrouter
# NOVEL_LLM_BASE_URL=http://localhost:11434/v1   # or any custom endpoint
NOVEL_PRICE_IN_PER_1M=3.00         # cost meter, match your model
NOVEL_PRICE_OUT_PER_1M=15.00
```

The default is **Claude Sonnet 5** via the official `anthropic` SDK. Two things it does
that Gemini didn't: prompt caching is **explicit** (one `cache_control` breakpoint on the
ContextPack's stable prefix — watch `Usage.cache_hit_rate`), and adaptive thinking shares
the `max_tokens` budget with the prose, so the adapter reserves headroom. `provider=gemini`
still uses the native `google-genai` SDK deliberately: its OpenAI-compat endpoint **rejects
`safety_settings`**, which that provider needs for dark genre fiction. See `llm.py`.

## Test console (local web UI)

`python -m novel_agent.web` gives you the whole pipeline, one step per button, so you
can inspect and retry a stage without re-spending tokens on the earlier ones:

| Panel | What it does |
|---|---|
| 연결 테스트 | live call — verifies provider, model id, and key |
| 1 아이디어 | create/switch projects (state persists to disk) |
| 2 설정 인터뷰 | agent asks ~8-10 tailored questions; **your answers decide the world** |
| 3 장르 + 전제 후보 | L0 profile + 3 NorthStar candidates → you lock one → Canon + VoiceBible |
| 4 에피소드 집필 | plan → draft → bounded revise loop, with before/after style score |
| 문체 린트 | **costs nothing** — paste any prose and score it instantly |
| 비용 | running token/cost meter per project |

## CLI

```bash
# full setup + episode 1, replaying saved interview answers
python scripts/run_setup.py --idea "네오 조선의 흑인 홍길동, 코믹" \
    --answers data/answers.json --pick 2 --draft --out data/final

# interactive interview instead of a file
python scripts/run_setup.py --idea "..." --interview --draft

# re-edit an existing episode from its saved canon store
python scripts/revise_episode.py --run data/final --iterations 3
```

## Layout

```
src/novel_agent/
  artifacts.py     # §1 shared vocabulary (Pydantic, single source of truth)
  schemas.py       # SHALLOW LLM-facing DTOs (responseSchema is a JSON-Schema subset)
  canon_store.py   # file-based, single-writer, append-only canon
  ledgers.py       # RhythmState (사이다/고구마) + ForeshadowLedger (떡밥)
  context_pack.py  # deterministic pack: cache-stable prefix + volatile suffix
  interview.py     # author interview — the world is the author's call
  nodes.py         # IdeaIntake / NorthStar / Canon+Voice / EpisodePlanner (+ RESTRAINT)
  drafter.py       # ContextPack → Draft (+ [[FACT:]] extraction)
  reviser.py       # bounded revise loop, keep-best
  style.py         # Korean prose lint (Track C) + the author's 금기어 blocker
  continuity.py    # Track A — canon contradiction check, hard gate
  llm.py           # provider adapter (Anthropic / Gemini native / OpenAI-compatible)
  web/             # FastAPI test console + single-page UI
tests/             # 169 behavior tests, real objects, LLM faked at the HTTP boundary
```

## Develop

```bash
pytest                    # 169 tests, no API key needed (LLM faked at the seam)
```

The deterministic core (artifacts, canon store, ledgers, context pack, lint) never
touches the network, so it is fully testable offline.
