# Web-Novel Writing Agent — Design

An AI agent that writes serialized web novels from **any user's freeform idea, in any genre**, as a human-in-the-loop **co-writer that earns autonomy per arc**. Genre is a *runtime input the agent infers from the idea* (data), never hardcoded.

This document has two parts:
- **Part ① — The Agent Workflow**: how the agent operates, end to end, for any idea. *(This is the core deliverable.)*
- **Part ② — Build, Validation & Market**: how to build, validate, and ship it (engineering shape, testing, Phase 0, roadmap, economics).

### Three principles that drive the design
1. **Consistency is a *write-path* problem.** Never re-read prior episodes. After each *accepted* episode, extract structured state into a canon store and query that. Re-feeding prior episodes is O(n²) and attention-degrading (even Claude Sonnet 5's 1M-token window fills around ep 120–160 at 5,000–5,500자/episode).
2. **The reward signal (retention / 연독률) is the real unsolved problem, not the plumbing.** Scaffolding prevents *crashes*; it does not produce a *voice worth paying for*. That needs a calibrated human taste-owner + a retention signal validated on real data.
3. **The agent's real market edge is anti-연중, not "better prose."** The harshest failure mode in Korean web serials is stalling in the first ~30 episodes; unbroken daily output with an enforced buffer attacks exactly that. Generation cost is trivial — **distribution is the binding constraint.**

---

# Part ① — The Agent Workflow (genre-agnostic)

## 1. Shared data artifacts (the vocabulary)

Every component reads/writes these named artifacts. **All genre-specific behavior lives inside `GenreProfile` as data** — no component branches on genre.

| Artifact | What it is |
|---|---|
| `Idea` | User's freeform premise + answers to clarifying questions (treated as untrusted input) |
| `GenreProfile` (L0) | audience, **content_rating (전연령/15+; explicit-19+ out of scope)** → drives rubric/register (one model serves all ratings), sub-genre, trope checklist, episode-length target, POV, tense, register, target catharsis cadence, max consecutive frustration beats, forbidden anti-patterns. Per-field `{value, confidence, source, evidence_span}`, versioned |
| `NorthStar` (L1) | premise, core conflict, protagonist edge, central twist, intended ending, repeatable "episode engine", constrained power/rule system |
| `Canon` | authoritative story bible: character cards (incl. voice card), world/rules, glossary; versioned facts `{value, source_episode, version}` |
| `VoiceBible` | locked authorial-voice spec + exemplar passages (human-approved) |
| `ForeshadowLedger` | setup→payoff rows `{seed_id, planted_ep, reinforced_in[], intended_payoff, due_by_ep, magnitude:major|minor, status}` |
| `RhythmState` | cross-episode catharsis / frustration-debt state |
| `ArcMap` (L2) | arcs `{goal, antagonist, climax, payoff, ending hook, span, status}` |
| `BeatSheet` (L3) | one episode's plan `{opening hook, beats, the one progression, seeds_to_plant (proposed_seed_id), seeds_to_pay, closing cliffhanger, length target, POV, entities present}` |
| `ContextPack` | assembled, budgeted context for drafting ONE episode |
| `Draft` / `RevisedDraft` | episode prose |
| `FactRequest` | drafter's request for a canon fact it needs but lacks (`blocking:true` halts the episode) |
| `ReviewReport` | **unified** `{findings[]{finding_id, track, severity:blocker\|major\|minor, evidence_span, suggested_direction, target_dimension}, gate_decision{status:pass\|fail\|escalate, blocking_finding_ids[]}}` |
| `AcceptedEpisode` | final accepted prose + `{accepted_draft_hash, human_edited}` (produced by the human-accept gate) |
| `CanonDelta` | committed state changes + reconciliation `{planned_but_absent[], unplanned_but_present[]}` |
| `Summary` | named derived rolling summaries, multi-resolution (arc-level + story-so-far) |
| `EpisodeRecord` | committed episode + metadata |
| `RePlanDirective` | arc-maintenance's instruction to re-plan `{for_component, reason}` |

## 2. End-to-end flow

```
USER IDEA
   │
   ▼  ── SETUP (once) ─────────────────────────────────────────────
 [IdeaIntake+GenreInference] → GenreProfile      ▣ human-confirm gate
 [NorthStarArchitect] best-of-N → NorthStar      ▣ PREMISE gate (lock 1)
 [Canon+VoiceBible Init] → Canon v1, VoiceBible   ▣ SETUP gate (lock voice)
        └ also creates empty ForeshadowLedger + RhythmState
   │
   ▼  ── ARC PLANNING (just-in-time, per arc) ────────────────────
 [ArcPlanner] → ArcMap (current+next detailed)    ▣ arc-turn gate
   │
   ▼  ── EPISODE LOOP (per episode) ──────────────────────────────
 [EpisodePlanner] → BeatSheet (rolls 3–5 ahead; queries Canon store directly)
 [ContextPackBuilder]  (code) → ContextPack
 [Drafter] → Draft (+ FactRequest[])
        └ blocking FactRequest → ▣ fact gate → scoped canon amendment
          (via Canonicalizer, single writer) → rebuild pack → re-draft
 [Review & Gate]  Continuity ‖ Craft ‖ Repetition ‖ lints → ReviewReport
 [Reviser]  bounded K, keep-best  ⟲ (re-runs review each iteration)
        └ non-converged / gate=escalate → human gate opens in ESCALATED mode
        ▣ HUMAN-ACCEPT gate (Co-writer) → AcceptedEpisode
        └ reject(notes) → Reviser (notes = blocker findings, fresh K);
          2nd reject → EpisodePlanner re-beat
 [Canonicalizer] → CanonDelta → commit (single writer)
        └ updates ForeshadowLedger, RhythmState, Summary, EpisodeRecord
   │  (loop)
   ▼  ── MAINTENANCE (every 10–20 eps) ───────────────────────────
 [ArcAuditor] over Summary+ledgers+tension curve
        → RePlanDirective (→ ArcPlanner)  |  CompletionCertificate
```

**Maps to the original 7-step workflow** *(1 outline+setup → 2 review setup → 3 write ep 1 → 4 review it → 5 write next episode from setup+outline+prior episodes → 6 review it → 7 repeat until complete)*: step 1 → Setup (L0/L1/Canon); step 2 → the three setup gates; steps 3&5 → EpisodePlanner+ContextPackBuilder+Drafter (write-path canon, *not* re-reading prior episodes); steps 4&6 → the in-loop Review & Gate + Reviser + Canonicalizer; step 7 → ArcAuditor's completion check.

## 3. Component contracts

Format: **kind · model · human gate**. `→` outputs. Genre-agnostic unless noted; every LLM component reads genre from `GenreProfile`.

**Model:** every LLM role below runs on **Claude Sonnet 5** (single-model, §5).

**IdeaIntake & GenreInference** — *hybrid · Claude Sonnet 5 · BLOCKING (confirm profile)*
The one place genre is decided. Infers audience/sub-genre from the idea's own signals in **open vocabulary** (no genre menu, no genre switch in code); asks ≤3 clarifying questions only for load-bearing low-confidence fields; derives trope checklist / cadence / anti-patterns as data. Code injects only *platform*-keyed defaults (episode length, register baseline). In: `Idea`, platform config, schema. → `GenreProfile`, `ClarifyingQuestions`, enriched `Idea`.
*Guardrail:* the only genre-adjacent code value is platform config, and inferred `register` wins over the platform baseline on conflict.

**NorthStarArchitect** — *hybrid · Claude Sonnet 5 · BLOCKING (premise lock)*
Best-of-N diverse `NorthStar` candidates; human locks exactly one. Stamps `genre_profile_version`. In: `Idea`, `GenreProfile`. → `NorthStar`, archived candidate set.

**Canon & VoiceBible Initializer** — *hybrid · Claude Sonnet 5 · BLOCKING (setup/voice lock)*
Builds initial `Canon` (character + voice cards, world/rules, glossary) and best-of-N `VoiceBible` the human locks. **Also initializes empty, versioned `ForeshadowLedger` + `RhythmState`** and stamps `genre_profile_version` onto Canon v1 + VoiceBible. In: `Idea`, `NorthStar`, `GenreProfile`. → `Canon` v1, `VoiceBible`, `SetupGateDecision` (unblocks **ArcPlanner**, not episode planning directly).

**ArcPlanner** — *hybrid · Claude Sonnet 5 · BLOCKING (arc-turn)*
Just-in-time: fully details current + next arc, rest as loglines. In: `NorthStar`, `GenreProfile`, `Canon` (from store), existing `ArcMap`, `ForeshadowLedger`, `RhythmState`, **`RePlanDirective`** (from ArcAuditor). → `ArcMap` (updated), rationale.

**EpisodePlanner** — *hybrid · Claude Sonnet 5 · gate: conditional — a BeatSheet containing an arc-turn beat pauses at a BLOCKING gate node*
Produces the next 3–5 `BeatSheet`s; enforces due foreshadows + cadence. **Queries the Canon store directly** (shared retrieval interface — not via ContextPackBuilder). Emits `seeds_to_plant` with `proposed_seed_id`. In: `ArcMap`, `NorthStar`, `GenreProfile`, `RhythmState`, `ForeshadowLedger`, `Canon`, `CanonDelta.planned_but_absent/unplanned_but_present` (divergence signal), pending BeatSheets. → `BeatSheet(s)`, planner report.

**ContextPackBuilder** — *code (no LLM) · no gate*
Deterministic assembly within a fixed token budget: **cache-stable prefix** (system + `NorthStar` hard rules + locked `VoiceBible` + immutable main-cast descriptors + glossary + genre rubric from `GenreProfile`) + **volatile suffix** (`Summary`, current-status `Canon` slice, K=1 previous episode verbatim, `BeatSheet`, due foreshadows, `RhythmState`-derived pacing directives). Episode 1 runs with `Summary` empty and no previous episode — deterministic omission, not an error. In: `GenreProfile`, `NorthStar`, `VoiceBible`, `Canon`, `ForeshadowLedger`, `BeatSheet`, `RhythmState`, `Summary`. → `ContextPack`.

**Drafter** — *llm · Claude Sonnet 5 (streaming) · no gate (accept gate is downstream)*
Renders one `Draft` to the `BeatSheet` in the locked voice. Invents no canon: emits `FactRequest[]` for anything missing (`blocking:true` → orchestrator halts the episode before review). In: `ContextPack` (carries BeatSheet/VoiceBible/GenreProfile/Canon slices). → `Draft`, `FactRequest[]`, coverage self-report.

**Review & Gate** — *hybrid · Continuity: Claude Sonnet 5 · Craft: Claude Sonnet 5 (separate blind call — see invariant #3) · Repetition: code · gate routes the human touchpoint*
Three mutually-blind tracks + lints, deterministically aggregated into the **unified `ReviewReport`**. Continuity = objective canon-anchored, hard-block on contradiction. Craft = genre rubric from `GenreProfile`, evidence-before-score. Repetition = prose/phrase fingerprints with a trope whitelist (fingerprint phrasing, not beat structure). In: `Draft`/`RevisedDraft`, `BeatSheet`, `Canon`, `GenreProfile`, `VoiceBible`, `ForeshadowLedger`, `RhythmState`. → `ReviewReport` (with `gate_decision`).

**Reviser** — *hybrid · Claude Sonnet 5 · no gate (automated inner loop)*
Bounded K (2–3), targets specific findings, **keeps the best version seen** (re-runs the review stack each iteration; never blindly accepts the last). Consumes the same unified `ReviewReport`. On non-convergence or `gate_decision=escalate` → the human-accept gate opens in **escalated mode** (payload: blocking findings + best-version-seen); no canon commit until a human resolves. In: `Draft`, `ReviewReport`, `BeatSheet`, `ContextPack`, `VoiceBible`. → `RevisedDraft`, revision result.

**Human-Accept Gate** — *orchestrator step · BLOCKING in Co-writer mode*
Resume payload: `{accept | accept_with_edits | reject(notes)}`. Accept → `AcceptedEpisode` (`prose + accepted_draft_hash + human_edited`). **Reject** → notes become blocker findings and the episode re-enters the Reviser with a fresh K budget; a second reject routes to EpisodePlanner for a re-beat. Also receives **escalated** episodes (gate `escalate` / Reviser non-convergence) with blocking findings + best-version-seen.
*Autonomy ladder:* **Co-writer** (default) — this gate blocks every episode. **Showrunner** — arc plans + flagged/escalated episodes block; routine prose goes to an ASYNC retro-approval queue with 1-in-N spot checks. **Autopilot** — premise/arc gates only; never auto-publishes live. Promotion is earned per arc (e.g. a full arc with zero human-caught blockers); any canon contradiction demotes one level.

**Canonicalizer** — *hybrid · Claude Sonnet 5 extraction · no gate (runs on accept)*
Single writer to `Canon`. Extracts `CanonDelta` (append-only; **assigns canonical `seed_id` from the planner's `proposed_seed_id` and records the mapping**; persists `magnitude:major|minor` onto ledger rows). Emits reconciliation `{planned_but_absent, unplanned_but_present}` in `CanonDelta`. Owns the **Summarizer** sub-step producing the named `Summary`. In: `AcceptedEpisode`, `BeatSheet`, `ReviewReport`, current `Canon`/`ForeshadowLedger`/`RhythmState`, `GenreProfile`. → `CanonDelta`, updated `Canon`/`ForeshadowLedger`/`RhythmState`/`Summary`, `EpisodeRecord`, conflict escalation. **On extraction conflict** the commit is withheld: the episode enters `accepted-uncommitted` and a human gate resolves (amend canon | edit episode). `human_edited=true` episodes get a fast continuity re-check before commit — human edits must not bypass the canon check.

**ArcAuditor / Re-planner / Completion** — *hybrid · Claude Sonnet 5 · BLOCKING for replan/complete/escalate*
Low-frequency governor over `Summary` + ledgers + code-computed tension curve (never raw prose). Triggers re-planning; evaluates finite-story completion (plan exhausted + climax resolved + **zero unpaid `major` seeds**). In: `ArcMap`, `ForeshadowLedger`, `RhythmState`, `Summary`, `GenreProfile`, `NorthStar`, BeatSheet queue, episode counter. → audit report, `RePlanDirective`, completion certificate.

## 4. Composition invariants

The integration pass across the contracts surfaced these gaps; the following invariants resolve them and must hold in code (#13–14 were added later — from the stack analysis and the final review):

1. **Genre lives only in `GenreProfile`.** No genre enum or switch anywhere; components read the profile. (Only platform config is code-supplied; inferred `register` wins on conflict.)
2. **Single-writer canon.** Only the Canonicalizer mutates `Canon`; it is append-only (payoffs inserted into *upcoming* episodes, never retro-edits).
3. **judge independence (weakened by single-model, §5).** Model-level judge≠drafter no longer holds — everything is Claude Sonnet 5. Independence drops to **context-level**: the CraftJudge runs in a separate, blind Claude call (no author rationale) with a different prompt — no separate model exists under the single-model rule. Known cost of the single-model choice.
4. **Ledgers exist before first read.** Canon-init creates empty versioned `ForeshadowLedger` + `RhythmState` + `Summary` at setup PASS; ContextPackBuilder tolerates empty `Summary` / absent previous episode for episode 1.
5. **One `ReviewReport` schema** shared by producer (Review&Gate) and consumer (Reviser); the gate enum maps explicitly to `pass|fail|escalate`.
6. **`Summary` is a first-class artifact** with a defined shape, owned by the Canonicalizer step — not an implicit sub-step two components secretly depend on.
7. **`AcceptedEpisode` is a distinct artifact** from `RevisedDraft` (the human-accept gate stamps prose + hash + edit flag); the Canonicalizer consumes it, not the raw revised draft.
8. **`seed_id` authority:** planner proposes, Canonicalizer mints canonical ids + records the mapping.
9. **Blocking `FactRequest`s halt the episode** before review/accept — a **fact gate** (human, or EpisodePlanner for derivable facts) supplies the missing fact; it is committed as a scoped canon amendment **via the Canonicalizer** (single-writer preserved), the ContextPack is rebuilt, and the Drafter re-runs. Never caught incidentally as a continuity "unverifiable."
10. **`genre_profile_version` is stamped** on NorthStar, Canon v1, and VoiceBible so a later L0 change is detectable.
11. **Setup unblocks ArcPlanner**, which unblocks EpisodePlanner (episode planning needs an ArcMap).
12. **Canon retrieval is a shared store interface** both EpisodePlanner and ContextPackBuilder call — no downstream/circular dependency.
13. **No expensive work before an `interrupt()`.** LangGraph re-runs an interrupted node from its top on resume, so LLM generation lives in its own node that checkpoints *before* the human-gate node; anything before an `interrupt()` must be idempotent. (See §5 stack.)
14. **Replanning invalidates downstream work.** On an accepted `RePlanDirective`, all pending `BeatSheet`s and unaccepted drafts past the replan point are invalidated and regenerated; `AcceptedEpisode`s are immutable.

---

# Part ② — Build, Validation & Market

## 5. Engineering shape

**Orchestrator-in-code, LLMs as thin stateless functions.** Plain code owns loops, gates, persistence, caching, budget. Every LLM call is `f(inputs) → validated output`. State lives in owned structures. This is what makes the system testable, resumable, debuggable.

**Pricing & strategy (Claude Sonnet 5, verified 2026-08):** $3.00 / $15.00 per 1M tok standard (introductory $2 / $10 through 2026-08-31; Batch API −50%). Prompt caching is **explicit**: cache reads bill at 0.1×, cache writes at 1.25×, minimum cacheable prefix ~1024 tok, 5-minute TTL by default. Strategy: **Claude Sonnet 5 for every role** (drafting, reasoning, judging, extraction, summaries); put the single `cache_control` breakpoint on the ContextPack's cache-stable prefix and keep that prefix byte-stable — caching is a *prefix match*, so one nondeterministic byte voids the discount for the run. **Measured 2026-08:** structured and plain-text calls maintain **separate cache entries** even with an identical prefix, because the JSON schema renders ahead of `system` (4,752-token prefix → 4,750 cached for `text()` vs 5,842 for `structured()`). So each prefix version pays *two* cache writes, not one, and both then read at 0.1× on every later call — verified 100% hit across text→text, structured→structured, and structured→text. A cold project therefore shows 0% for its first few calls; that is normal, not drift. Adaptive thinking is on by default and shares the `max_tokens` budget with visible text, so size budgets with headroom and tune depth with `effort` rather than a token budget (`budget_tokens` is a 400 on this model). Re-baseline token budgets with `count_tokens` on real Korean text — the tokenizer differs from Gemini's. Embeddings (Phase 1b+): Anthropic ships none, so use a Korean-tuned embedder (or Voyage/OpenAI) when Phase 2 retrieval lands.

**Model: Claude Sonnet 5 only (single-provider commitment).** The system uses **one model — Claude Sonnet 5 — for every LLM role**; no multi-model bake-off. One model serves all ratings (`content_rating` drives rubric/register, *not* drafter routing), one API, one provider. Chosen for Claude's Korean-prose strength and near-Opus quality at Sonnet cost. The provider adapter (`llm.py`) still exposes `gemini` and `openai`, so the swap path stays one `.env` edit.

**Scope consequence (unchanged by the switch):** Anthropic's usage policy prohibits sexually explicit content regardless of framing, so the **explicit-19+ (성인 로판/BL) segment remains OUT OF SCOPE** — the same boundary Google's policy imposed. The product covers 전연령/15+ including dark/violent/suggestive-mature content. Unlike Gemini there is **no safety-settings layer to configure**: dark genre content needs no special request configuration, and `FICTION_SAFETY_SETTINGS` now applies to `provider=gemini` only.

⚠️ **Re-validate the dark end of the range.** The Phase 0 content-fit test (§7) passed *on Gemini with relaxed filters*. Claude has no equivalent knob, and its refusal behavior on 사이다 revenge / villain POV / graphic violence is a different distribution — re-run that test before trusting the 15+ 마이너 tier. A decline arrives as **HTTP 200 with `stop_reason: "refusal"`**, not an exception, so the adapter checks it before reading content (`AnthropicLLM._check_stop`); an unattended run must log and escalate refusals rather than silently write a short episode.

⚠️ **Risks this single-model choice concentrates:** (1) **Korean prose** — the product's #1 capability rides entirely on Claude Sonnet 5. Claude ranks at the top of Korean benchmarks, but the specific job here (long-form 웹소설 register, not chat) is **unverified for this model** — the Phase 0 prose spike must be re-run (§7). (2) **Judge independence** — invariant #3 can't hold at the *model* level; review independence drops to *context-level* (a separate blind Claude call with no author rationale), no cross-model check. (3) **Cost is ~2× the prior plan** at list price ($3/$15 vs $1.50/$7.50) — the per-episode estimate in §7 is restated accordingly; explicit prompt caching is now the main lever and must be monitored (`Usage.cache_hit_rate`), not assumed.

**Store:** Phase 1 = a git-backed repo of JSON/markdown files (`Bible.md`, `arcs/*.json`, `episodes/NNN.md`) — no DB, no vector search. Add SQLite + embeddings only when canon measurably outgrows the ContextPack budget (~Phase 2, past ~ep 50–100).

### Technical stack (verified 2026-07 against current docs)

**Language / API:** Python 3.11+, **FastAPI** + **Pydantic v2**, **pydantic-settings** for config/secrets (`BaseSettings` moved out of core v2). Packaging: `uv`.

**Pydantic model per artifact = single source of truth.** Each shared artifact (§1) is one Pydantic model backing the FastAPI request/`response_model`, persistence, *and* the LLM structured-output schema. Structured output (`messages.parse()` with a Pydantic model on Anthropic; `responseSchema` on Gemini) accepts a JSON-Schema **subset** — audit for `oneOf`/`anyOf` unions, `$ref`, and deep nesting. **Keep the LLM-facing schema shallow** (no self-referential models; nesting ≤2–3 levels) and enforce rich constraints in Pydantic validators *after* parsing (model a would-be-recursive outline as a flat list with parent-id refs, not a tree).

**Orchestration: LangGraph** — nodes = the 12 components in §3, edges = the flow. **LangGraph owns only control-flow, gates, checkpointing, streaming, and durable execution — never domain logic.** Canon diff/merge, ledger transitions, ContextPackBuilder assembly, and gate aggregation stay plain Python the nodes call, so the deterministic core is unit-testable without the graph (§6). Call **Claude Sonnet 5 directly via the official `anthropic` SDK inside nodes** (LangGraph is model-agnostic; do *not* pull in LangChain LLM wrappers/LCEL — that fights the "thin stateless function" rule and our precise control of cache-breakpoint placement and effort). Still keep the LLM call behind a thin **provider adapter** (`llm.py`) — committing to Claude doesn't mean hardcoding it; the adapter preserves a one-line swap path (provider + key + model) should Phase 0 validation fail.

**Human gates = LangGraph `interrupt()`** (`from langgraph.types import interrupt`), resumed via `Command(resume=…)`. Current HITL API; `interrupt_before/after` are now debugging breakpoints, not approvals. Every BLOCKING gate in Part ① (profile confirm, premise lock, voice lock, arc-turn, arc-turn beat sheets (conditional), fact gate, human-accept, replan/complete) is an `interrupt()`; FastAPI surfaces the payload and resumes with the user's decision.

**⚠️ Load-bearing node rule (now composition invariant #13):** on resume, LangGraph **re-runs the interrupted node from its top** — code before `interrupt()` executes again. So **never put an expensive/non-idempotent LLM call in the same node as an `interrupt()`.** Drafter/Reviser run in their own nodes that complete + checkpoint *before* a separate human-gate node interrupts.

**Checkpointer (durable, or "resume" is meaningless):** `interrupt()` needs a checkpointer + a `thread_id` (one per novel). Episodes are gated over days/weeks with restarts between, so **MemorySaver/InMemorySaver is tests-only** (RAM, lost on restart). Use `SqliteSaver` (`langgraph-checkpoint-sqlite`; `from_conn_string("checkpoints.sqlite")` — a file, not `:memory:`; `setup()` auto-runs; note `from_conn_string` is a **context manager** — for a long-lived FastAPI server open it in the app lifespan, or construct `SqliteSaver(sqlite3.connect(path, check_same_thread=False))` directly) for Phase 1 single-node; `PostgresSaver` (`langgraph-checkpoint-postgres`; call `.setup()` once, `autocommit=True`, `row_factory=dict_row`) for Phase 2+ multi-worker; `.aio` async variants if the app is async. **Two separate persistence concerns:** the *checkpointer* = orchestration/resume state; the *canon store* = the novel's authoritative content (git files → SQLite/Postgres + pgvector; embeddings via a Korean-tuned embedder — Anthropic ships none).

**Anthropic API specifics (verified 2026-08 against the SDK, `anthropic` 0.121):**
- Client: official **`anthropic` SDK** (`anthropic.Anthropic(api_key=…)`), `model="claude-sonnet-5"`. Context window: **1M input / 128k output**.
- Structured output: `client.messages.parse(..., output_format=<PydanticModel>)` → `resp.parsed_output` (validated for you; JSON-Schema subset — see above).
- **Prompt caching is explicit**: a `cache_control: {"type": "ephemeral"}` block on the system message (the ContextPack stable prefix). Render order is `tools → system → messages`, and any byte change invalidates everything after it. Verify with `usage.cache_read_input_tokens`; a persistent zero means a silent invalidator.
- **Adaptive thinking is on by default** and is capped by `max_tokens` *together with* visible text — `text()` therefore requests `max_tokens + THINKING_HEADROOM`. Depth is set by `output_config={"effort": …}` (`low`…`max`, default `high`). Thinking tokens are already inside `usage.output_tokens` — do not bill them twice.
- **Rejected parameters (400):** `temperature`, `top_p`, `top_k` at non-default values, and `thinking.budget_tokens`. Steer with the prompt; size with `effort`. Assistant-turn prefills also 400 — use structured output instead.
- Refusals: HTTP 200 with `stop_reason: "refusal"` + `stop_details.category`. Truncation is `stop_reason: "max_tokens"` — a different fix (raise the budget), so the adapter reports them separately.
- Token count: `client.messages.count_tokens(...).input_tokens`. Streaming: `client.messages.stream(...)` + `get_final_message()`; used for every prose call so long Korean episodes cannot hit an HTTP read timeout. Batch API: −50%, for Phase 3 buffer-ahead.
- No safety-settings equivalent, and **no embedding model** — see §5 notes above.

**Durable background work (buffer-ahead episodes):** a plain worker loop / FastAPI background task invokes the graph per pending episode; the durable checkpointer makes each run resumable. (LangGraph's durability modes only control *when* checkpoints persist — they are not a scheduler; no Celery/RQ needed at this scale.) LangGraph has **no built-in approval TTL** — the orchestrator must enforce "gate waiting N days → nudge/expire."

**Component → node mapping:**

| Component | Node kind | Stack notes |
|---|---|---|
| IdeaIntake/GenreInference | LLM + `interrupt()` | Claude structured output → `GenreProfile`; confirm gate |
| NorthStarArchitect | LLM + `interrupt()` | best-of-N; premise lock |
| Canon+VoiceBible init | LLM + `interrupt()` | voice lock; inits empty ledgers |
| ArcPlanner | LLM + `interrupt()` | arc-turn gate |
| EpisodePlanner | LLM (+ conditional gate node) | queries canon store directly; arc-turn BeatSheet pauses for approval |
| ContextPackBuilder | **code** | pure Python, no LLM |
| Drafter | LLM (own node) | Claude streaming draft; emits `FactRequest` |
| Review & Gate | LLM×2 + code | continuity/craft LLM, repetition n-grams |
| Reviser | LLM (own node) | bounded loop owned by code |
| Human-accept | `interrupt()` | produces `AcceptedEpisode` |
| Canonicalizer | LLM + code | Claude structured output → `CanonDelta`; single writer |
| ArcAuditor | LLM + `interrupt()` | replan/complete/escalate |

**`LLM + interrupt()` = a two-node pair** — the LLM node completes and checkpoints, then a *separate* gate node interrupts (invariant #13); never one node.

**Observability:** `structlog` + a token/cost accounting wrapper around the Anthropic client (`llm.Usage`, which prices the cache tiers separately; feeds the budget-cap rail); optionally LangSmith or OpenTelemetry GenAI conventions (`gen_ai.usage.*`), both framework-agnostic.

## 6. Testing

Deterministic core is the tested core: ContextPackBuilder, ledger state transitions, canon diff/merge, gate aggregation logic, retrieval key-lookups, rollback/invalidation — pure unit tests, real objects, no LLM mocks. Assert on structured outputs + canon state; never snapshot LLM prose or judge text. For generative/judge stages use a mutation/injected-defect suite as behavior tests (does the ContinuityChecker catch the planted contradiction?). One integration test per critical path (episode → canonicalize → next episode sees the update). Mock only the boundary: LLM HTTP calls and the clock.

## 7. Phase 0 — De-risk (no pipeline code; ~1–2 weeks)

*Genre/platform selection here is for **validation**, not the product — the product infers genre per user idea.*

> ⚠️ **Items 4 and 5 were measured on Gemini 3.6 Flash and are now STALE.** The
> model switched to Claude Sonnet 5 (2026-08, §5). Their ✅/⏳ markers below are
> kept as the historical record of what was actually run — they are **not**
> evidence about the current model. Both must be re-run on `claude-sonnet-5`
> before the go/no-go is meaningful. Item 7's cost figure is restated for the
> new price. Items 1, 2, 3, 6 are model-independent and stand.

1. **Platform-policy memo (✅ done — `phase0-market-memo.md`):** Novelpia = home base (open self-pub, AI-permissive, per-view payout). No legal/platform AI-disclosure mandate in ordinary serialization; positioning = honesty + quality, stay out of contests (AI hard-banned there). **⚠️ decisive open probe:** whether Novelpia's PLUS monetization gate de-facto rejects AI work + the current per-view rate (~4원/8원) — probe with a tiny live test before committing.
2. **Taste-owner calibration (load-bearing):** the builder is the taste-owner but not a heavy genre reader → calibrate first (blind-rank {hits, mid-list, flops}; must recover market order above chance). If it fails: recruit a genre-native reader, or downgrade the gate to a coarse obvious-failure filter + lean on multi-rater ranking + retention proxy + real readers.
3. **Genre-inference test (the product is genre-agnostic):** feed several freeform ideas; check the model reliably infers genre + loads the right L0 conventions. A wrong inference silently poisons every downstream rubric.
4. **Prose spike = Claude go/no-go.** *(🔁 MUST RE-RUN on `claude-sonnet-5`. Historical, on Gemini: generation DONE, blind rank PENDING — `phase0-results.md`; 3.6 Flash beat 3.5 Flash on canon fidelity, 3.5 broke canon twice in one episode.)* Generate the first 5–10 eps in Korean, best-of-N, on **`claude-sonnet-5`**, sweeping `effort` (`medium` / `high` / `xhigh`) as the tier bake-off — effort, not model choice, is now the quality/cost dial. Blind-rank vs (i) random no-name free-board 화1s (must-beat, top quartile, recognizability-screened) and (ii) 2–3 known hits (ceiling, *no stop authority*). ≥3 raters; pre-register the win threshold. **Go/no-go on the single-model bet**: if no effort level beats the no-name bar, stop and reassess. Claude tops Korean benchmarks, but long-form 웹소설 register is a different task from chat — still the riskiest assumption in the plan.
5. **Content-fit test (in Korean):** *(🔁 MUST RE-RUN on `claude-sonnet-5` — this is the switch's biggest open question. Historical, on Gemini: ✅ PASS with relaxed safety settings, `phase0-results.md`.)* confirm Claude writes the *in-scope* mature content — 사이다 revenge, villain POV, graphic violence, suggestive (non-explicit) romance — **without softening**. There is **no safety-settings knob on Anthropic**, so the model's own calibration is the whole story: measure the refusal rate (`stop_reason: "refusal"`) *and* the softening rate (does it write the scene but sand the edges off?). Softening is the likelier failure and the harder one to detect — a lint or judge rubric for it beats eyeballing. (Explicit-19+ is out of scope by policy on both providers — do not test or ship it.) If Claude sanitizes in-scope dark content, that is a scope constraint to record, and `provider=gemini` remains available for that tier.
6. **Retention-signal feasibility check:** per-episode cumulative views are public + scrapeable (existing 연독률 tools); verify that known winners vs flops are actually separable in the scraped 연독률 signal. (The **RetentionPredictor** — a Claude scorer of per-episode drop-off risk, calibrated on this signal and consulted by EpisodePlanner/ArcAuditor — is a Phase 2+ component; it must not gate anything until validated here.)
7. **Unit economics (✅ modeled — `phase0-market-memo.md`; ⚠️ price restated for Claude):** cost still trivial per episode (full loop — draft + parallel reviews + revise + canonicalize/summary — ≈ **₩1,400–3,600/ep** on Claude Sonnet 5 at $3/$15, roughly 2× the Gemini estimate; cache reads at 0.1× and Batch at −50% pull it back down, so the effective figure depends on the cache hit rate, which is now worth measuring rather than assuming). Even at the top of that range a 100-episode 완결 is ≈ ₩360k — **distribution is still the constraint** (no-name serial ≈ ₩0; winner-take-most; 70.8% of works earn <₩5M). Treat revenue as a distribution problem.

## 8. Roadmap

- **Phase 1a — true MVP (one validation genre, one arc ~15–30 eps, Co-writer; ~4–6 weeks solo).**
  *Component scope —* **IN (pipeline):** EpisodePlanner · ContextPackBuilder · Drafter · continuity gate + ONE revise iteration · n-gram repetition lint · Human-accept gate · Canonicalizer (incl. Summarizer). **Scripted one-shots with human file-editing (no pipeline UI):** IdeaIntake/GenreProfile · NorthStar · Canon+VoiceBible init · a single hand-approved ArcMap. **OUT (deferred):** CraftJudge (the human is the craft gate) · rhythm controller (manual beat-type tags instead) · embeddings · ArcAuditor · eps-1–3 sub-pipeline.
  *Artifacts:* file-based canon (character+voice cards, world/rules, glossary, foreshadow ledger) + auto `Summary` + stub `RhythmState`. **Manual beat-type tags** = the human tags each accepted episode's beats from a fixed vocabulary {setup, escalation, payoff/사이다, frustration/고구마, reveal, cliffhanger} on the BeatSheet, recorded into `RhythmState`, so cadence is trackable before the automated controller (1b). SqliteSaver-backed `interrupt()`/resume is **1a infrastructure** (required by §5's gates), not deferred machinery. Budget cap on.
  Include a **baseline arm** (~10 eps via the vanilla Claude app + a doc bible — same model, no apparatus). **Pass condition (set N first): apparatus ≤ N% of baseline editor-hours at ≥ equal blind rank.**
- **Phase 1b — earn the machinery (each gated on an *observed* 1a failure):** CraftJudge + full revise loop · rhythm controller · embedding near-dup · timeline/relationship/knowledge ledgers · eps-1–3 multi-candidate sub-pipeline · ArcAuditor + RePlanDirective machinery.
- **Publishing the pilot:** Novelpia, with a 10–20 ep buffer (daily cadence feeds ranking). Stopping at 15–30 eps reads as 연중 — frame as a completed 단편/중편 or commit to continuing. Scrape 연독률; set a minimum-reader threshold below which the signal is anecdote.
- **Phase 2 — v1 (multi-arc, Showrunner):** JIT arc re-planning + reconciliation, arc audits, Director's Notes steering, judge-validation harness. Pilot retention curve = sanity check on the predictor (statistical validation needs a portfolio).
- **Phase 3 — scale (portfolio, Autopilot-with-rails):** multi-novel infra, the Anthropic **Batch API** (−50%) for buffer-ahead generation, platform profiles, draft-queue publishing. Tradeoff: buffer depth ≤ feedback latency.

## 9. Bottom line

Build the **file-based memory layer + continuity gate first** (Phase 1a), but run **Phase 0 before any pipeline code**. The workflow (Part ①) guarantees a *consistent, well-paced, trope-correct* novel; whether it has a *voice worth paying for* rests on a calibrated taste-owner + a validated retention signal — and the whole apparatus must beat the null baseline (a human + the vanilla Claude app + a doc bible) on hours and blind-judged quality, or it isn't worth its complexity.
