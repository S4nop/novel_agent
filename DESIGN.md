# Web-Novel Writing Agent — Design

An AI agent that writes serialized web novels from **any user's freeform idea, in any genre**, as a human-in-the-loop **co-writer that earns autonomy per arc**. Genre is a *runtime input the agent infers from the idea* (data), never hardcoded.

This document has two parts:
- **Part ① — The Agent Workflow**: how the agent operates, end to end, for any idea. *(This is the core deliverable.)*
- **Part ② — Build, Validation & Market**: how to build, validate, and ship it (engineering shape, testing, Phase 0, roadmap, economics).

### Three principles that drive the design
1. **Consistency is a *write-path* problem.** Never re-read prior episodes. After each *accepted* episode, extract structured state into a canon store and query that. Re-feeding prior episodes is O(n²) and attention-degrading (even Gemini 3.6 Flash's 1M-token window fills around ep 120–160 at 5,000–5,500자/episode).
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

**Model:** every LLM role below runs on **Gemini 3.6 Flash** (single-model, §5).

**IdeaIntake & GenreInference** — *hybrid · Gemini 3.6 Flash · BLOCKING (confirm profile)*
The one place genre is decided. Infers audience/sub-genre from the idea's own signals in **open vocabulary** (no genre menu, no genre switch in code); asks ≤3 clarifying questions only for load-bearing low-confidence fields; derives trope checklist / cadence / anti-patterns as data. Code injects only *platform*-keyed defaults (episode length, register baseline). In: `Idea`, platform config, schema. → `GenreProfile`, `ClarifyingQuestions`, enriched `Idea`.
*Guardrail:* the only genre-adjacent code value is platform config, and inferred `register` wins over the platform baseline on conflict.

**NorthStarArchitect** — *hybrid · Gemini 3.6 Flash · BLOCKING (premise lock)*
Best-of-N diverse `NorthStar` candidates; human locks exactly one. Stamps `genre_profile_version`. In: `Idea`, `GenreProfile`. → `NorthStar`, archived candidate set.

**Canon & VoiceBible Initializer** — *hybrid · Gemini 3.6 Flash · BLOCKING (setup/voice lock)*
Builds initial `Canon` (character + voice cards, world/rules, glossary) and best-of-N `VoiceBible` the human locks. **Also initializes empty, versioned `ForeshadowLedger` + `RhythmState`** and stamps `genre_profile_version` onto Canon v1 + VoiceBible. In: `Idea`, `NorthStar`, `GenreProfile`. → `Canon` v1, `VoiceBible`, `SetupGateDecision` (unblocks **ArcPlanner**, not episode planning directly).

**ArcPlanner** — *hybrid · Gemini 3.6 Flash · BLOCKING (arc-turn)*
Just-in-time: fully details current + next arc, rest as loglines. In: `NorthStar`, `GenreProfile`, `Canon` (from store), existing `ArcMap`, `ForeshadowLedger`, `RhythmState`, **`RePlanDirective`** (from ArcAuditor). → `ArcMap` (updated), rationale.

**EpisodePlanner** — *hybrid · Gemini 3.6 Flash · gate: conditional — a BeatSheet containing an arc-turn beat pauses at a BLOCKING gate node*
Produces the next 3–5 `BeatSheet`s; enforces due foreshadows + cadence. **Queries the Canon store directly** (shared retrieval interface — not via ContextPackBuilder). Emits `seeds_to_plant` with `proposed_seed_id`. In: `ArcMap`, `NorthStar`, `GenreProfile`, `RhythmState`, `ForeshadowLedger`, `Canon`, `CanonDelta.planned_but_absent/unplanned_but_present` (divergence signal), pending BeatSheets. → `BeatSheet(s)`, planner report.

**ContextPackBuilder** — *code (no LLM) · no gate*
Deterministic assembly within a fixed token budget: **cache-stable prefix** (system + `NorthStar` hard rules + locked `VoiceBible` + immutable main-cast descriptors + glossary + genre rubric from `GenreProfile`) + **volatile suffix** (`Summary`, current-status `Canon` slice, K=1 previous episode verbatim, `BeatSheet`, due foreshadows, `RhythmState`-derived pacing directives). Episode 1 runs with `Summary` empty and no previous episode — deterministic omission, not an error. In: `GenreProfile`, `NorthStar`, `VoiceBible`, `Canon`, `ForeshadowLedger`, `BeatSheet`, `RhythmState`, `Summary`. → `ContextPack`.

**Drafter** — *llm · Gemini 3.6 Flash (streaming) · no gate (accept gate is downstream)*
Renders one `Draft` to the `BeatSheet` in the locked voice. Invents no canon: emits `FactRequest[]` for anything missing (`blocking:true` → orchestrator halts the episode before review). In: `ContextPack` (carries BeatSheet/VoiceBible/GenreProfile/Canon slices). → `Draft`, `FactRequest[]`, coverage self-report.

**Review & Gate** — *hybrid · Continuity: Gemini 3.6 Flash · Craft: Gemini 3.6 Flash (separate blind call — see invariant #3) · Repetition: code · gate routes the human touchpoint*
Three mutually-blind tracks + lints, deterministically aggregated into the **unified `ReviewReport`**. Continuity = objective canon-anchored, hard-block on contradiction. Craft = genre rubric from `GenreProfile`, evidence-before-score. Repetition = prose/phrase fingerprints with a trope whitelist (fingerprint phrasing, not beat structure). In: `Draft`/`RevisedDraft`, `BeatSheet`, `Canon`, `GenreProfile`, `VoiceBible`, `ForeshadowLedger`, `RhythmState`. → `ReviewReport` (with `gate_decision`).

**Reviser** — *hybrid · Gemini 3.6 Flash · no gate (automated inner loop)*
Bounded K (2–3), targets specific findings, **keeps the best version seen** (re-runs the review stack each iteration; never blindly accepts the last). Consumes the same unified `ReviewReport`. On non-convergence or `gate_decision=escalate` → the human-accept gate opens in **escalated mode** (payload: blocking findings + best-version-seen); no canon commit until a human resolves. In: `Draft`, `ReviewReport`, `BeatSheet`, `ContextPack`, `VoiceBible`. → `RevisedDraft`, revision result.

**Human-Accept Gate** — *orchestrator step · BLOCKING in Co-writer mode*
Resume payload: `{accept | accept_with_edits | reject(notes)}`. Accept → `AcceptedEpisode` (`prose + accepted_draft_hash + human_edited`). **Reject** → notes become blocker findings and the episode re-enters the Reviser with a fresh K budget; a second reject routes to EpisodePlanner for a re-beat. Also receives **escalated** episodes (gate `escalate` / Reviser non-convergence) with blocking findings + best-version-seen.
*Autonomy ladder:* **Co-writer** (default) — this gate blocks every episode. **Showrunner** — arc plans + flagged/escalated episodes block; routine prose goes to an ASYNC retro-approval queue with 1-in-N spot checks. **Autopilot** — premise/arc gates only; never auto-publishes live. Promotion is earned per arc (e.g. a full arc with zero human-caught blockers); any canon contradiction demotes one level.

**Canonicalizer** — *hybrid · Gemini 3.6 Flash extraction · no gate (runs on accept)*
Single writer to `Canon`. Extracts `CanonDelta` (append-only; **assigns canonical `seed_id` from the planner's `proposed_seed_id` and records the mapping**; persists `magnitude:major|minor` onto ledger rows). Emits reconciliation `{planned_but_absent, unplanned_but_present}` in `CanonDelta`. Owns the **Summarizer** sub-step producing the named `Summary`. In: `AcceptedEpisode`, `BeatSheet`, `ReviewReport`, current `Canon`/`ForeshadowLedger`/`RhythmState`, `GenreProfile`. → `CanonDelta`, updated `Canon`/`ForeshadowLedger`/`RhythmState`/`Summary`, `EpisodeRecord`, conflict escalation. **On extraction conflict** the commit is withheld: the episode enters `accepted-uncommitted` and a human gate resolves (amend canon | edit episode). `human_edited=true` episodes get a fast continuity re-check before commit — human edits must not bypass the canon check.

**ArcAuditor / Re-planner / Completion** — *hybrid · Gemini 3.6 Flash · BLOCKING for replan/complete/escalate*
Low-frequency governor over `Summary` + ledgers + code-computed tension curve (never raw prose). Triggers re-planning; evaluates finite-story completion (plan exhausted + climax resolved + **zero unpaid `major` seeds**). In: `ArcMap`, `ForeshadowLedger`, `RhythmState`, `Summary`, `GenreProfile`, `NorthStar`, BeatSheet queue, episode counter. → audit report, `RePlanDirective`, completion certificate.

## 4. Composition invariants

The integration pass across the contracts surfaced these gaps; the following invariants resolve them and must hold in code (#13–14 were added later — from the stack analysis and the final review):

1. **Genre lives only in `GenreProfile`.** No genre enum or switch anywhere; components read the profile. (Only platform config is code-supplied; inferred `register` wins on conflict.)
2. **Single-writer canon.** Only the Canonicalizer mutates `Canon`; it is append-only (payoffs inserted into *upcoming* episodes, never retro-edits).
3. **judge independence (weakened by single-model, §5).** Model-level judge≠drafter no longer holds — everything is Gemini 3.6 Flash. Independence drops to **context-level**: the CraftJudge runs in a separate, blind Gemini call (no author rationale) with different sampling/prompt — no separate model exists under the single-model rule. Known cost of the single-model choice.
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

**Pricing & strategy (Gemini 3.6 Flash, verified 2026-07):** $1.50 / $7.50 per 1M tok (batch −50% → $0.75 / $3.75); **implicit prefix caching** on by default (surfaced as cached-token usage; explicit cache via `cached_content`). Strategy: **Gemini 3.6 Flash for every role** (drafting, reasoning, judging, extraction, summaries); lean on prefix caching for the repeated story-bible context. Note it is a *reasoning* model — thinking can't be fully disabled, so budget thinking tokens + latency per call. Re-baseline token budgets with `countTokens` on real Korean text. Embeddings (Phase 1b+): a Gemini embedding model or a Korean-tuned embedder.

**Model: Gemini 3.6 Flash only (single-provider commitment).** The system uses **one model — Gemini 3.6 Flash — for every LLM role**; no multi-model bake-off. One model serves all ratings (`content_rating` drives rubric/register, *not* drafter routing), one API, one provider. Chosen for Gemini's strong Korean-prose reputation + low price ($1.50/$7.50 per 1M tok) + a trivial OpenAI-compatible migration. **Scope consequence:** Google's usage policy bans explicit sexual content *above* the safety-setting layer, so the **explicit-19+ (성인 로판/BL) segment is OUT OF SCOPE**; the product covers 전연령/15+ including dark/violent/suggestive-mature content (defensible under the policy's artistic exception, with HARASSMENT/HATE/DANGEROUS filters relaxed).

⚠️ **Account risk:** do NOT route explicit content through Gemini with relaxed safety settings — Google logs paid traffic for policy-violation monitoring and reviews apps using less-restrictive settings; an explicit-content app risks account suspension. Keep the product on the artistic-fiction side of the line. Use the **paid tier** (the free tier trains on your content).

⚠️ **Risks this single-model choice concentrates:** (1) **Korean prose** — the product's #1 capability rides entirely on Gemini. Its Korean is well-regarded but *unverified for 3.6 Flash specifically* (a new, terseness-tuned model), so Phase 0 is a **go/no-go** that also benchmarks **3.5 Flash vs 3.6 Flash** (Google calls 3.5 Flash its "most intelligent" Flash; for lush prose it may win). (2) **Judge independence** — invariant #3 can't hold at the *model* level; review independence drops to *context-level* (a separate blind Gemini call), no cross-model check.

**Store:** Phase 1 = a git-backed repo of JSON/markdown files (`Bible.md`, `arcs/*.json`, `episodes/NNN.md`) — no DB, no vector search. Add SQLite + embeddings only when canon measurably outgrows the ContextPack budget (~Phase 2, past ~ep 50–100).

### Technical stack (verified 2026-07 against current docs)

**Language / API:** Python 3.11+, **FastAPI** + **Pydantic v2**, **pydantic-settings** for config/secrets (`BaseSettings` moved out of core v2). Packaging: `uv`.

**Pydantic model per artifact = single source of truth.** Each shared artifact (§1) is one Pydantic model backing the FastAPI request/`response_model`, persistence, *and* the LLM structured-output schema. Gemini structured output (`responseSchema`, or `.parse()` with a Pydantic model via the OpenAI-compat layer) accepts a JSON-Schema **subset** — audit for `oneOf`/`anyOf` unions, `$ref`, and deep nesting. **Keep the LLM-facing schema shallow** (no self-referential models; nesting ≤2–3 levels) and enforce rich constraints in Pydantic validators *after* parsing (model a would-be-recursive outline as a flat list with parent-id refs, not a tree).

**Orchestration: LangGraph** — nodes = the 12 components in §3, edges = the flow. **LangGraph owns only control-flow, gates, checkpointing, streaming, and durable execution — never domain logic.** Canon diff/merge, ledger transitions, ContextPackBuilder assembly, and gate aggregation stay plain Python the nodes call, so the deterministic core is unit-testable without the graph (§6). Call **Gemini 3.6 Flash directly via its OpenAI-compatible endpoint inside nodes** (LangGraph is model-agnostic; do *not* pull in LangChain LLM wrappers/LCEL — that fights the "thin stateless function" rule and our precise control of caching/safety-settings). Still keep the LLM call behind a thin **provider adapter** — committing to Gemini doesn't mean hardcoding it; the adapter preserves a one-line swap path (base_url + key + model) should Phase 0 validation fail.

**Human gates = LangGraph `interrupt()`** (`from langgraph.types import interrupt`), resumed via `Command(resume=…)`. Current HITL API; `interrupt_before/after` are now debugging breakpoints, not approvals. Every BLOCKING gate in Part ① (profile confirm, premise lock, voice lock, arc-turn, arc-turn beat sheets (conditional), fact gate, human-accept, replan/complete) is an `interrupt()`; FastAPI surfaces the payload and resumes with the user's decision.

**⚠️ Load-bearing node rule (now composition invariant #13):** on resume, LangGraph **re-runs the interrupted node from its top** — code before `interrupt()` executes again. So **never put an expensive/non-idempotent LLM call in the same node as an `interrupt()`.** Drafter/Reviser run in their own nodes that complete + checkpoint *before* a separate human-gate node interrupts.

**Checkpointer (durable, or "resume" is meaningless):** `interrupt()` needs a checkpointer + a `thread_id` (one per novel). Episodes are gated over days/weeks with restarts between, so **MemorySaver/InMemorySaver is tests-only** (RAM, lost on restart). Use `SqliteSaver` (`langgraph-checkpoint-sqlite`; `from_conn_string("checkpoints.sqlite")` — a file, not `:memory:`; `setup()` auto-runs; note `from_conn_string` is a **context manager** — for a long-lived FastAPI server open it in the app lifespan, or construct `SqliteSaver(sqlite3.connect(path, check_same_thread=False))` directly) for Phase 1 single-node; `PostgresSaver` (`langgraph-checkpoint-postgres`; call `.setup()` once, `autocommit=True`, `row_factory=dict_row`) for Phase 2+ multi-worker; `.aio` async variants if the app is async. **Two separate persistence concerns:** the *checkpointer* = orchestration/resume state; the *canon store* = the novel's authoritative content (git files → SQLite/Postgres + pgvector; embeddings via a Gemini embedding model or a Korean-tuned embedder).

**Gemini API specifics (via OpenAI-compat endpoint; verified 2026-07):**
- Client: **native `google-genai` SDK** (`genai.Client(api_key=…)`), `model="gemini-3.6-flash"`. Context window: **1M input / 65k output**. ⚠️ **Not** the OpenAI-compat endpoint: it rejects safety settings (`400 Unknown name "safety_settings"`, verified 2026-07-29), and safety control is load-bearing for dark fiction.
- Structured output: `response_mime_type="application/json"` + `response_schema=<PydanticModel>` (JSON-Schema subset — see above); read `resp.parsed`.
- **Safety settings** (needed for dark/violent fiction): `config.safety_settings` with `BLOCK_NONE` for HARASSMENT / HATE_SPEECH / DANGEROUS_CONTENT. Do **not** attempt to unblock SEXUALLY_EXPLICIT — policy-banned + account risk (§ above). Child-safety filters are always on.
- Tool calling: OpenAI-style `tools` + `tool_choice`. Caching: implicit (on by default) + explicit `cached_content` via `extra_body`.
- Token count: `countTokens` (native) / `client.models.count_tokens`. Streaming: `stream=True`. Reasoning can't be disabled (tune `thinking_config`).

**Durable background work (buffer-ahead episodes):** a plain worker loop / FastAPI background task invokes the graph per pending episode; the durable checkpointer makes each run resumable. (LangGraph's durability modes only control *when* checkpoints persist — they are not a scheduler; no Celery/RQ needed at this scale.) LangGraph has **no built-in approval TTL** — the orchestrator must enforce "gate waiting N days → nudge/expire."

**Component → node mapping:**

| Component | Node kind | Stack notes |
|---|---|---|
| IdeaIntake/GenreInference | LLM + `interrupt()` | Gemini structured output → `GenreProfile`; confirm gate |
| NorthStarArchitect | LLM + `interrupt()` | best-of-N; premise lock |
| Canon+VoiceBible init | LLM + `interrupt()` | voice lock; inits empty ledgers |
| ArcPlanner | LLM + `interrupt()` | arc-turn gate |
| EpisodePlanner | LLM (+ conditional gate node) | queries canon store directly; arc-turn BeatSheet pauses for approval |
| ContextPackBuilder | **code** | pure Python, no LLM |
| Drafter | LLM (own node) | Gemini streaming draft; emits `FactRequest` |
| Review & Gate | LLM×2 + code | continuity/craft LLM, repetition n-grams |
| Reviser | LLM (own node) | bounded loop owned by code |
| Human-accept | `interrupt()` | produces `AcceptedEpisode` |
| Canonicalizer | LLM + code | Gemini structured output → `CanonDelta`; single writer |
| ArcAuditor | LLM + `interrupt()` | replan/complete/escalate |

**`LLM + interrupt()` = a two-node pair** — the LLM node completes and checkpoints, then a *separate* gate node interrupts (invariant #13); never one node.

**Observability:** `structlog` + a token/cost accounting wrapper around the Gemini (OpenAI-compatible) client (feeds the budget-cap rail); optionally LangSmith or OpenTelemetry GenAI conventions (`gen_ai.usage.*`), both framework-agnostic.

## 6. Testing

Deterministic core is the tested core: ContextPackBuilder, ledger state transitions, canon diff/merge, gate aggregation logic, retrieval key-lookups, rollback/invalidation — pure unit tests, real objects, no LLM mocks. Assert on structured outputs + canon state; never snapshot LLM prose or judge text. For generative/judge stages use a mutation/injected-defect suite as behavior tests (does the ContinuityChecker catch the planted contradiction?). One integration test per critical path (episode → canonicalize → next episode sees the update). Mock only the boundary: LLM HTTP calls and the clock.

## 7. Phase 0 — De-risk (no pipeline code; ~1–2 weeks)

*Genre/platform selection here is for **validation**, not the product — the product infers genre per user idea.*

1. **Platform-policy memo (✅ done — `phase0-market-memo.md`):** Novelpia = home base (open self-pub, AI-permissive, per-view payout). No legal/platform AI-disclosure mandate in ordinary serialization; positioning = honesty + quality, stay out of contests (AI hard-banned there). **⚠️ decisive open probe:** whether Novelpia's PLUS monetization gate de-facto rejects AI work + the current per-view rate (~4원/8원) — probe with a tiny live test before committing.
2. **Taste-owner calibration (load-bearing):** the builder is the taste-owner but not a heavy genre reader → calibrate first (blind-rank {hits, mid-list, flops}; must recover market order above chance). If it fails: recruit a genre-native reader, or downgrade the gate to a coarse obvious-failure filter + lean on multi-rater ranking + retention proxy + real readers.
3. **Genre-inference test (the product is genre-agnostic):** feed several freeform ideas; check the model reliably infers genre + loads the right L0 conventions. A wrong inference silently poisons every downstream rubric.
4. **Prose spike = Gemini go/no-go + tier bake-off.** *(⏳ generation DONE, blind rank PENDING — see `phase0-results.md`. 3.6 Flash beat 3.5 Flash on canon fidelity; 3.5 broke canon twice in one episode. Staying on `gemini-3.6-flash`.)* Generate the first 5–10 eps in Korean, best-of-N, with **both `gemini-3.6-flash` and `gemini-3.5-flash`** (Google calls 3.5 Flash its "most intelligent" Flash; 3.6 is tuned terser — for lush prose 3.5 may win). Blind-rank vs (i) random no-name free-board 화1s (must-beat, top quartile, recognizability-screened) and (ii) 2–3 known hits (ceiling, *no stop authority*). ≥3 raters; pre-register the win threshold. **Go/no-go on the single-model bet**: if neither Flash beats the no-name bar, stop and reassess. Gemini's Korean is well-regarded but unverified for these exact models — the riskiest assumption in the plan.
5. **Content-fit test (in Korean):** *(✅ PASS — `phase0-results.md`. Gemini accepts relaxed safety settings and writes unsanitized dark Korean content. ⚠️ verified: safety settings are NOT supported on the OpenAI-compat endpoint (`400 Unknown name "safety_settings"`) → the adapter uses the native `google-genai` SDK.)* confirm Gemini writes the *in-scope* mature content — 사이다 revenge, villain POV, graphic violence, suggestive (non-explicit) romance — **without softening**, with HARASSMENT/HATE/DANGEROUS safety settings relaxed. (Explicit-19+ is out of scope by policy — do not test or ship it.) If Gemini sanitizes dark/violent content at the model level even with filters off, that's a scope constraint to record.
6. **Retention-signal feasibility check:** per-episode cumulative views are public + scrapeable (existing 연독률 tools); verify that known winners vs flops are actually separable in the scraped 연독률 signal. (The **RetentionPredictor** — a Gemini scorer of per-episode drop-off risk, calibrated on this signal and consulted by EpisodePlanner/ArcAuditor — is a Phase 2+ component; it must not gate anything until validated here.)
7. **Unit economics (✅ modeled — `phase0-market-memo.md`):** cost trivial (full episode loop — draft + parallel reviews + revise + canonicalize/summary — ≈ ₩700–1,800/ep on Gemini 3.6 Flash at $1.50/$7.50, less with prefix caching + batch); **distribution is the constraint** (no-name serial ≈ ₩0; winner-take-most; 70.8% of works earn <₩5M). Treat revenue as a distribution problem.

## 8. Roadmap

- **Phase 1a — true MVP (one validation genre, one arc ~15–30 eps, Co-writer; ~4–6 weeks solo).**
  *Component scope —* **IN (pipeline):** EpisodePlanner · ContextPackBuilder · Drafter · continuity gate + ONE revise iteration · n-gram repetition lint · Human-accept gate · Canonicalizer (incl. Summarizer). **Scripted one-shots with human file-editing (no pipeline UI):** IdeaIntake/GenreProfile · NorthStar · Canon+VoiceBible init · a single hand-approved ArcMap. **OUT (deferred):** CraftJudge (the human is the craft gate) · rhythm controller (manual beat-type tags instead) · embeddings · ArcAuditor · eps-1–3 sub-pipeline.
  *Artifacts:* file-based canon (character+voice cards, world/rules, glossary, foreshadow ledger) + auto `Summary` + stub `RhythmState`. **Manual beat-type tags** = the human tags each accepted episode's beats from a fixed vocabulary {setup, escalation, payoff/사이다, frustration/고구마, reveal, cliffhanger} on the BeatSheet, recorded into `RhythmState`, so cadence is trackable before the automated controller (1b). SqliteSaver-backed `interrupt()`/resume is **1a infrastructure** (required by §5's gates), not deferred machinery. Budget cap on.
  Include a **baseline arm** (~10 eps via the vanilla Gemini app / AI Studio + a doc bible — same model, no apparatus). **Pass condition (set N first): apparatus ≤ N% of baseline editor-hours at ≥ equal blind rank.**
- **Phase 1b — earn the machinery (each gated on an *observed* 1a failure):** CraftJudge + full revise loop · rhythm controller · embedding near-dup · timeline/relationship/knowledge ledgers · eps-1–3 multi-candidate sub-pipeline · ArcAuditor + RePlanDirective machinery.
- **Publishing the pilot:** Novelpia, with a 10–20 ep buffer (daily cadence feeds ranking). Stopping at 15–30 eps reads as 연중 — frame as a completed 단편/중편 or commit to continuing. Scrape 연독률; set a minimum-reader threshold below which the signal is anecdote.
- **Phase 2 — v1 (multi-arc, Showrunner):** JIT arc re-planning + reconciliation, arc audits, Director's Notes steering, judge-validation harness. Pilot retention curve = sanity check on the predictor (statistical validation needs a portfolio).
- **Phase 3 — scale (portfolio, Autopilot-with-rails):** multi-novel infra, Gemini **Batch mode** (−50%) for buffer-ahead generation, platform profiles, draft-queue publishing. Tradeoff: buffer depth ≤ feedback latency.

## 9. Bottom line

Build the **file-based memory layer + continuity gate first** (Phase 1a), but run **Phase 0 before any pipeline code**. The workflow (Part ①) guarantees a *consistent, well-paced, trope-correct* novel; whether it has a *voice worth paying for* rests on a calibrated taste-owner + a validated retention signal — and the whole apparatus must beat the null baseline (a human + the vanilla Gemini app + a doc bible) on hours and blind-judged quality, or it isn't worth its complexity.
