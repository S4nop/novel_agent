"""Local test UI — drive the whole pipeline from a browser (DESIGN §5).

    uvicorn novel_agent.web.app:app --reload --port 8000
    # or: python -m novel_agent.web

Every step is a separate endpoint so you can stop, inspect, and retry without
re-spending tokens on the earlier stages. Project state persists to disk via
CanonStore, so a reload does not lose the setup.

`POST /api/lint` costs NOTHING — it runs the deterministic style lint, so you can
paste any prose (including hand-written or a competitor's) and score it instantly.
"""
from __future__ import annotations

import json
import pathlib
import traceback
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..artifacts import Draft, Summary
from . import jobs
from ..canon_store import CanonStore
from ..canonicalizer import canonicalize_episode, commit_episode_state
from ..continuity import blocks_acceptance, check_continuity, deterministic_findings
from ..craft import judge_craft, judge_opening_and_ending
from ..config import KNOWN_BASE_URLS, load_settings
from ..context_pack import ContextPackBuilder
from ..drafter import draft_episode
from ..interview import Answer, enrich_idea, generate_interview_questions
from ..llm import LLMRefusal, Usage, build_llm
from ..prompt_store import list_prompts, load as load_prompt, save as save_prompt, validate as validate_prompt
from ..nodes import (
    generate_northstar_candidates,
    infer_genre_profile,
    init_canon_and_voice,
    plan_arcs,
    plan_episode,
    effective_arc_map,
    to_north_star,
)
from ..reviser import revise_draft
from ..reviser import PASS_SCORE
from ..style import forbidden_terms_from, lint_prose, style_score

app = FastAPI(title="novel-agent test console")
STATIC = pathlib.Path(__file__).parent / "static"


# ── project state (small JSON sidecar next to the canon store) ────────────────
def _root() -> pathlib.Path:
    p = pathlib.Path(load_settings().projects_root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dir(pid: str) -> pathlib.Path:
    d = _root() / pid
    if not d.exists():
        raise HTTPException(404, f"project {pid} not found")
    return d


def _load(pid: str) -> dict:
    return json.loads((_dir(pid) / "state.json").read_text(encoding="utf-8"))


def _save(pid: str, state: dict) -> None:
    (_root() / pid / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _llm(state: dict):
    """One LLM per request, with the project's running cost meter restored."""
    s = load_settings()
    if not s.llm_api_key:
        raise HTTPException(400, "NOVEL_LLM_API_KEY is not set — edit .env")
    u = state.get("usage", {})
    usage = Usage(
        input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
        cached_tokens=u.get("cached_tokens", 0),
        cache_write_tokens=u.get("cache_write_tokens", 0),
        thinking_tokens=u.get("thinking_tokens", 0), calls=u.get("calls", 0),
        price_in_per_1m=s.price_in_per_1m, price_out_per_1m=s.price_out_per_1m,
        cache_read_multiplier=s.cache_read_multiplier,
        usd_krw=s.usd_krw,
    )
    return build_llm(s, usage=usage), usage


def _record_usage(state: dict, usage: Usage) -> dict:
    # cache_* must round-trip: they are priced at different rates, so dropping
    # them here would silently re-bill cached tokens at the full input price.
    state["usage"] = {
        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "thinking_tokens": usage.thinking_tokens, "calls": usage.calls,
        "usd": round(usage.usd, 4), "krw": round(usage.krw),
        # 0.0 across many calls = the cache-stable prefix drifted (DESIGN §5)
        "cache_hit_rate": round(usage.cache_hit_rate, 3),
    }
    return state["usage"]


def _guard(fn):
    """Turn provider errors into readable 4xx/5xx instead of a stack trace."""
    try:
        return fn()
    except (HTTPException, jobs.Cancelled):
        raise
    except LLMRefusal as e:
        raise HTTPException(422, f"모델이 응답을 거부했거나 비어 있음: {e}") from e
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            raise HTTPException(429, f"쿼터 초과: {msg[:300]}") from e
        traceback.print_exc()
        raise HTTPException(500, f"{type(e).__name__}: {msg[:400]}") from e


# ── request bodies ───────────────────────────────────────────────────────────
class IdeaIn(BaseModel):
    idea: str


class AnswersIn(BaseModel):
    answers: list[Answer] = []


class LockIn(BaseModel):
    pick: int = 1
    # The arc plan divides this many episodes, so it has to be decided at the
    # premise gate rather than when the serial is launched.
    total_episodes: int = 30


class PromptIn(BaseModel):
    text: str


class CanonIn(BaseModel):
    canon: dict


class ArcMapIn(BaseModel):
    arc_map: dict


class AbandonIn(BaseModel):
    episode: int = 0


class LintIn(BaseModel):
    text: str
    target_chars: int = 5200


class DraftIn(BaseModel):
    episode: int = 1
    revise: bool = True
    iterations: int = 2
    # The direction the author chose from /directions, or wrote themselves.
    direction: str = ""


class DirectionsIn(BaseModel):
    episode: int = 1


class SerialIn(BaseModel):
    # None = the length the arc plan was built for
    target_episodes: int | None = None
    budget_krw: float = 50_000.0
    iterations: int = 3


# ── config / health ──────────────────────────────────────────────────────────
@app.get("/api/config")
def get_config():
    return {**load_settings().public(), "presets": sorted(KNOWN_BASE_URLS)}


@app.post("/api/health")
def health():
    """Cheap live call — verifies provider, model id, and key in one shot."""
    def run():
        s = load_settings()
        llm = build_llm(s)
        # Reasoning models spend max_output_tokens on thinking first, so a tiny
        # budget returns truncated garbage — give it room even for "ok".
        out = llm.text(
            [{"role": "user", "content": "연결 확인용입니다. '연결 정상'이라고만 답하세요."}],
            max_tokens=1024,
        )
        return {"ok": True, "provider": s.llm_provider, "model": s.llm_model,
                "reply": out.strip()[:80]}
    return _guard(run)


def _violation_json(v) -> dict:
    """Include WHY and HOW-TO-FIX — a bare number reads as an arbitrary deduction
    and teaches the author nothing (tester feedback 3)."""
    m = v.meta
    return {
        "rule": v.rule, "severity": v.severity, "count": v.count,
        "limit": v.limit, "evidence": v.evidence,
        "why": m.why if m else "", "fix": m.fix if m else "",
        "bad": m.bad if m else "", "good": m.good if m else "",
    }


# ── the free tool: lint any prose, no tokens spent ───────────────────────────
@app.post("/api/lint")
def lint(body: LintIn):
    vs = lint_prose(body.text, target_chars=body.target_chars)
    return {
        "chars": len(body.text),
        "score": style_score(body.text, target_chars=body.target_chars),
        "pass_score": PASS_SCORE,
        "violations": [_violation_json(v) for v in vs],
    }


# ── prompt editing (no LLM cost; this is the main tuning surface) ────────────
@app.get("/api/prompts")
def prompts_index():
    return [{"name": n, "problems": validate_prompt(n)} for n in list_prompts()]


@app.get("/api/prompts/{name}")
def prompt_get(name: str):
    try:
        return {"name": name, "text": load_prompt(name), "problems": validate_prompt(name)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.put("/api/prompts/{name}")
def prompt_put(name: str, body: PromptIn):
    """Rejects an edit that drops a required ${placeholder} — a silently broken
    prompt is worse than a failed save."""
    try:
        save_prompt(name, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return {"name": name, "text": load_prompt(name), "problems": validate_prompt(name)}


# ── canon editing (author has the final say; storage already supported it) ───
@app.get("/api/projects/{pid}/canon")
def canon_get(pid: str):
    store = CanonStore(_dir(pid) / "_novel")
    if not (store.root / "canon.json").exists():
        raise HTTPException(400, "아직 캐논이 없습니다 — 전제를 먼저 확정하세요")
    return store.load_canon().model_dump(mode="json")


@app.put("/api/projects/{pid}/canon")
def canon_put(pid: str, body: CanonIn):
    """Hand-edit the canon instead of regenerating the whole thing.

    Regeneration is a gamble: it re-rolls the parts the author already liked.
    Edits are marked `author` so a future Canonicalizer can avoid overwriting
    them (provenance, per the reviewer's suggestion).
    """
    from ..artifacts import Canon

    store = CanonStore(_dir(pid) / "_novel")
    try:
        canon = Canon.model_validate(body.canon)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"캐논 형식이 올바르지 않습니다: {str(e)[:300]}") from e
    previous = store.load_canon()
    from ..canon_edit import appeared_in

    # A name lives in eight other files; changing it here would change one.
    for key, card in canon.characters.items():
        if card.name != key:
            raise HTTPException(400, f"'{key}'의 이름 변경은 이름 변경 기능을 쓰세요 — "
                                     "전제·아크 계획·보이스 바이블까지 함께 바뀌어야 합니다")
    # Deleting someone a committed episode names makes canon contradict
    # published prose. Retiring them keeps both true.
    for name in set(previous.characters) - set(canon.characters):
        gone = previous.characters[name]
        eps = appeared_in(store, [name, *gone.aliases])
        if eps:
            raise HTTPException(409, f"'{name}'은(는) {', '.join(map(str, eps))}화에 등장해 "
                                     "삭제할 수 없습니다. 상태를 '퇴장'으로 바꾸세요")
    canon.version = previous.version + 1
    canon.last_modified_by = "author"
    store.save_canon(canon)
    state = _load(pid)
    state["canon"] = canon.model_dump(mode="json")
    _save(pid, state)
    return state["canon"]


# ── pipeline ─────────────────────────────────────────────────────────────────
@app.get("/api/projects/{pid}/arcs")
def arcs_get(pid: str):
    plan = CanonStore(_dir(pid) / "_novel").load_arc_map()
    if plan is None:
        raise HTTPException(400, "아직 아크 계획이 없습니다 — 전제를 먼저 확정하세요")
    return plan.model_dump(mode="json")


@app.put("/api/projects/{pid}/arcs")
def arcs_put(pid: str, body: ArcMapIn):
    """Hand-edit the arc plan. This decides more about the story than the canon
    does, so the author has the last word on it before episode 1."""
    from ..artifacts import ArcMap
    from ..nodes import arc_span_problems

    store = CanonStore(_dir(pid) / "_novel")
    try:
        plan = ArcMap.model_validate(body.arc_map)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"아크 계획 형식이 올바르지 않습니다: {str(e)[:300]}") from e
    problems = arc_span_problems(plan)
    if problems:
        raise HTTPException(400, "화 구간이 이어지지 않습니다 — " + " / ".join(problems))
    previous = store.load_arc_map()
    plan.version = (previous.version if previous else 0) + 1
    plan.last_modified_by = "author"
    store.save_arc_map(plan)
    state = _load(pid)
    state["arc_map"] = plan.model_dump(mode="json")
    _save(pid, state)
    return state["arc_map"]


@app.get("/api/projects/{pid}/pending")
def pending_get(pid: str):
    """The episode waiting for the author, if any.

    Passing the automated gate used to mean committing to canon immediately, so
    an episode the author disliked was already locked into every later
    episode's context before they could read it.
    """
    held = CanonStore(_dir(pid) / "_novel").pending_episode()
    if held is None:
        return {"episode": None}
    return {
        "episode": held.draft.episode_number,
        "prose": held.draft.prose,
        "chars": held.draft.char_count,
        "score": held.score,
        "findings": held.findings,
        "hook": held.beats.opening_hook,
        "progression": held.beats.the_one_progression,
        "cliffhanger": held.beats.closing_cliffhanger,
    }


@app.post("/api/projects/{pid}/pending/accept")
def pending_accept(pid: str):
    """Advance every ledger and extract the canon delta — the point at which
    the episode becomes something later episodes are written from."""
    from ..driver import accept_pending

    store = CanonStore(_dir(pid) / "_novel")
    if store.pending_episode() is None:
        raise HTTPException(400, "승인 대기 중인 화가 없습니다")

    def run():
        state = _load(pid)
        llm, usage = _llm(state)
        n = store.pending_episode().draft.episode_number
        accept_pending(llm, store)
        _record_usage(state, usage)
        _save(pid, state)
        return {"accepted": n}
    return _guard(run)


@app.post("/api/projects/{pid}/pending/reject")
def pending_reject(pid: str):
    """Drop the draft, touch no ledger. The next run rewrites that episode."""
    from ..driver import reject_pending

    store = CanonStore(_dir(pid) / "_novel")
    held = store.pending_episode()
    if held is None:
        raise HTTPException(400, "승인 대기 중인 화가 없습니다")
    n = held.draft.episode_number
    reject_pending(store)
    return {"rejected": n}


@app.get("/api/projects/{pid}/seeds")
def seeds_get(pid: str):
    """The 떡밥 ledger, open threads first — what the story still owes."""
    led = CanonStore(_dir(pid) / "_novel").load_foreshadow()
    order = {"planted": 0, "reinforced": 1, "paid": 2, "abandoned": 3}
    seeds = sorted((s.model_dump(mode="json") for s in led.seeds.values()),
                   key=lambda s: (order.get(s["status"], 9), s["seed_id"]))
    return {"seeds": seeds, "unpaid_major": [s.seed_id for s in led.unpaid_major()]}


@app.post("/api/projects/{pid}/seeds/{seed_id}/abandon")
def seed_abandon(pid: str, seed_id: str, body: AbandonIn):
    """Retire a thread the story is not going to pay off.

    Never automatic: a passed deadline is not evidence the author gave up on a
    thread. Without this a dropped major kept completion_ready() False forever
    and due() re-listed it in every remaining episode's prompt.
    """
    store = CanonStore(_dir(pid) / "_novel")
    led = store.load_foreshadow()
    if seed_id not in led.seeds:
        raise HTTPException(400, f"그런 떡밥이 없습니다: {seed_id}")
    led.abandon(seed_id, episode=body.episode)
    store.save_foreshadow(led)
    return led.seeds[seed_id].model_dump(mode="json")


class RenameIn(BaseModel):
    new_name: str
    dry_run: bool = False


@app.post("/api/projects/{pid}/characters/{name}/rename")
def character_rename(pid: str, name: str, body: RenameIn):
    """Rename a character across every setup file (dry_run previews it).

    Before episode 1 the name changes everywhere. Once episodes exist their
    prose keeps the old name and canon keeps it as an alias. Refused while an
    episode awaits approval, since that draft uses the old name.
    """
    from ..canon_edit import RenameError, rename_character

    store = CanonStore(_dir(pid) / "_novel")
    try:
        report = rename_character(store, name, body.new_name, dry_run=body.dry_run)
    except RenameError as e:
        status = 409 if "승인" in str(e) else 400
        raise HTTPException(status, str(e)) from e
    if not body.dry_run:
        # the console renders from these copies
        state = _load(pid)
        state["canon"] = store.load_canon().model_dump(mode="json")
        state["north_star"] = store.load_north_star().model_dump(mode="json")
        state["voice"] = store.load_voice_bible().model_dump(mode="json")
        if (plan := store.load_arc_map()) is not None:
            state["arc_map"] = plan.model_dump(mode="json")
        _save(pid, state)
    return {"old": report.old, "new": report.new,
            "committed_episodes": report.committed_episodes,
            "changes": [{"file": f, "count": n} for f, n in report.changes],
            "alias_kept": report.alias_kept, "warning": report.warning,
            "applied": not body.dry_run}


@app.get("/api/projects")
def list_projects():
    out = []
    for d in sorted(_root().glob("*/state.json")):
        st = json.loads(d.read_text(encoding="utf-8"))
        out.append({"id": st["id"], "idea": st["idea"], "step": st.get("step", "created")})
    return out


@app.post("/api/projects")
def create_project(body: IdeaIn):
    pid = uuid.uuid4().hex[:8]
    (_root() / pid).mkdir(parents=True, exist_ok=True)
    _save(pid, {"id": pid, "idea": body.idea, "step": "created", "usage": {}})
    return _load(pid)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _load(pid)


@app.post("/api/projects/{pid}/interview")
def interview(pid: str):
    state = _load(pid)

    def run():
        llm, usage = _llm(state)
        qs = generate_interview_questions(llm, state["idea"], max_questions=10)
        state["questions"] = [q.model_dump() for q in qs]
        state["step"] = "interviewed"
        _record_usage(state, usage)
        _save(pid, state)
        return state
    return _guard(run)


@app.post("/api/projects/{pid}/answers")
def answers(pid: str, body: AnswersIn):
    state = _load(pid)
    state["answers"] = [a.model_dump() for a in body.answers]
    state["enriched_idea"] = enrich_idea(state["idea"], body.answers)
    state["step"] = "answered"
    _save(pid, state)
    return state


@app.post("/api/projects/{pid}/setup")
def setup(pid: str):
    """L0 genre profile + best-of-N NorthStar candidates (the premise gate)."""
    state = _load(pid)

    def run():
        llm, usage = _llm(state)
        idea = state.get("enriched_idea") or state["idea"]
        profile, notes = infer_genre_profile(llm, idea)
        cands = generate_northstar_candidates(llm, idea, profile, n=3)
        state["profile"] = profile.model_dump(mode="json")
        state["inference_notes"] = notes
        state["candidates"] = [c.model_dump() for c in cands]
        state["step"] = "setup"
        _record_usage(state, usage)
        _save(pid, state)
        return state
    return _guard(run)


@app.post("/api/projects/{pid}/lock")
def lock(pid: str, body: LockIn):
    """Premise lock → build Canon + VoiceBible and initialise the store."""
    state = _load(pid)
    if not state.get("candidates"):
        raise HTTPException(400, "run /setup first")

    def run():
        from ..artifacts import GenreProfile
        from ..schemas import NorthStarDraft

        llm, usage = _llm(state)
        idea = state.get("enriched_idea") or state["idea"]
        profile = GenreProfile.model_validate(state["profile"])
        chosen = NorthStarDraft.model_validate(state["candidates"][body.pick - 1])
        ns = to_north_star(chosen, profile)
        canon, voice = init_canon_and_voice(llm, idea, profile, ns)
        # L2 — arcs after canon, so they can name the cast. This is what makes
        # 떡밥 derivable from a picture instead of improvised per episode.
        arc_map = plan_arcs(llm, north_star=ns, profile=profile, canon=canon,
                            total_episodes=body.total_episodes)

        CanonStore(_dir(pid) / "_novel").initialize(
            genre_profile=profile, north_star=ns, canon=canon, voice_bible=voice,
            arc_map=arc_map)
        state["picked"] = body.pick
        state["north_star"] = ns.model_dump(mode="json")
        state["canon"] = canon.model_dump(mode="json")
        state["voice"] = voice.model_dump(mode="json")
        state["arc_map"] = arc_map.model_dump(mode="json")
        state["step"] = "locked"
        _record_usage(state, usage)
        _save(pid, state)
        return state
    return _guard(run)


@app.post("/api/projects/{pid}/episode", status_code=202)
def episode(pid: str, body: DraftIn):
    """Start writing one episode as a background job; poll /jobs/{id}.

    A single episode takes 12-20 minutes. As one synchronous request that was
    past the wait limit of most browsers and tunnels, and a dropped connection
    left the console blank while the server finished the episode anyway
    (coworker feedback 1).
    """
    store = CanonStore(_dir(pid) / "_novel")
    if not (store.root / "canon.json").exists():
        raise HTTPException(400, "run /lock first")
    _llm(_load(pid))          # fail fast on a missing key, before any job exists
    return _start_job(pid, "episode",
                      lambda ctx: _guard(lambda: _write_episode(pid, body, ctx)),
                      meta={"episode": body.episode})


def _write_episode(pid: str, body: DraftIn, ctx: jobs.JobContext) -> dict:
    """Plan → draft → revise → judge → commit, reporting each stage.

    Nothing touches canon before the commit stage, so a cancel honoured at any
    earlier boundary leaves the project exactly as it was.
    """
    state = _load(pid)
    store = CanonStore(_dir(pid) / "_novel")
    n = body.episode

    def run():
        llm, usage = _llm(state)
        ctx.stage("plan", f"{n}화 설계")
        profile = store.load_genre_profile()
        ns = store.load_north_star()
        canon = store.load_canon()
        prev = store.load_episode(body.episode - 1)

        beats = plan_episode(
            llm, episode_number=body.episode, profile=profile, north_star=ns,
            canon=canon, arc_map=effective_arc_map(store.load_arc_map(), llm, ns),
            rhythm=store.load_rhythm(),
            foreshadow=store.load_foreshadow(), summary=store.load_summary(),
            chosen_direction=body.direction,
        )
        pack = ContextPackBuilder().build(
            genre_profile=profile, north_star=ns, voice_bible=store.load_voice_bible(),
            canon=canon, beat_sheet=beats, foreshadow=store.load_foreshadow(),
            rhythm=store.load_rhythm(), summary=store.load_summary(),
            current_episode=body.episode, previous_episode=prev,
        )
        # The author's absolute prohibitions become a mechanical blocker rule
        # (테스트 피드백 1-④): a hard rule the model quietly ignored used to be
        # invisible to the gate. Sourced from the interview's hard_rule answers
        # plus the profile's anti-patterns — never a hardcoded word list.
        forbidden = _forbidden(state, profile)
        ctx.stage("draft", f"{n}화 집필")
        draft = draft_episode(llm, pack, max_tokens=32768)
        ctx.stage("judge", "도입·절단 판정")
        structural = judge_opening_and_ending(llm, draft)
        first = style_score(draft.prose, target_chars=beats.length_target,
                            forbidden_terms=forbidden)

        # The gate runs whether or not the loop does: with revise off the
        # episode used to be committed unchecked while the console said 미통과.
        # max_iterations=0 is a pure re-evaluation with no LLM rewrite.
        result = revise_draft(llm, draft, pack, target_chars=beats.length_target,
                                  max_iterations=body.iterations if body.revise else 0,
                                  on_iteration=lambda i, cap: ctx.stage(
                                      "revise", f"{n}화 수정", f"{i}/{cap}회"),
                                  forbidden_terms=forbidden,
                                  # free rules run every iteration so canon breaks
                                  # get FIXED, not merely reported at the end
                                  extra_findings=lambda d: deterministic_findings(
                                      d, beats, canon),
                                  structural_findings=structural,
                                  structural_recheck=lambda d:
                                      judge_opening_and_ending(llm, d))
        draft = result.draft

        # Track A, full pass — one judge call on the final draft. The drafter
        # cannot be trusted to self-report invented facts (phase0-results.md),
        # so this is the only thing standing between an unattended run and
        # accumulating canon damage.
        ctx.stage("judge", "연속성·크래프트 판정")
        continuity = check_continuity(llm, draft, beats, canon)
        # Track B — advisory, never gates (DESIGN §3)
        # The structural half must be the verdict on the prose the author is
        # about to read — result.structural when a rewrite happened, otherwise
        # the original judgement still describes it.
        verdict = judge_craft(llm, draft, profile, canon, store.load_voice_bible())
        craft = list(verdict.findings) + list(result.structural)

        # The prose file is always written — the author must be able to read a
        # rejected draft. Canon is a different matter.
        (_dir(pid) / f"ep{body.episode:02d}.txt").write_text(draft.prose, encoding="utf-8")

        # Advance the cross-episode ledgers ONLY on a passing gate. Committing a
        # continuity-blocked episode would write the contradiction into canon and
        # plant its 떡밥, i.e. the gate would label the failure while letting the
        # damage through — and a retry would re-plant the same seeds under new
        # ids, pushing 완결 further away each time.
        blocked = blocks_acceptance(continuity)
        committed = not blocked and result.passed
        canon_delta = None
        if committed:
            # last boundary: past this point the episode is in canon
            ctx.stage("commit", "캐논 반영")
            commit_episode_state(store, draft, beats,
                                 payoff_landed=verdict.payoff_landed)
            # The LLM half of the Canonicalizer. Runs ONLY on an accepted
            # episode: extracting from a draft that failed the gate would write
            # the contradiction into the source of truth. A failure here costs
            # one episode's canon accumulation, not the run.
            canon_delta = canonicalize_episode(llm, store, draft)
        out = {
            "episode": body.episode,
            "beat_sheet": beats.model_dump(mode="json"),
            "prose": draft.prose,
            "chars": draft.char_count,
            "target": beats.length_target,
            "first_score": first,
            "score": style_score(draft.prose, target_chars=beats.length_target,
                                 forbidden_terms=forbidden),
            "iterations": result.iterations,
            # a continuity blocker overrides a passing style score (hard gate)
            "passed": bool(result.passed) and not blocked,
            "continuity": [_violation_json(v) for v in continuity],
            "continuity_blocked": blocked,
            "craft": [_violation_json(v) for v in craft],
            # was this episode written into canon, or held back for review?
            "committed": committed,
            "canon_delta": ({
                "new_characters": sorted(canon_delta.new_characters),
                "updated": sorted(canon_delta.character_updates),
                "new_facts": sum(len(v) for v in canon_delta.new_known_facts.values()),
                "new_rules": len(canon_delta.new_world_rules),
                "new_terms": [g.canonical_form for g in canon_delta.new_glossary],
            } if canon_delta else None),
            "fact_requests": [f.question for f in draft.fact_requests],
            "violations": [_violation_json(v) for v in
                           lint_prose(draft.prose, target_chars=beats.length_target,
                                      forbidden_terms=forbidden)],
            "pass_score": PASS_SCORE,
            "direction": body.direction,
        }
        state.setdefault("episodes", {})[str(body.episode)] = {
            k: out[k] for k in ("chars", "score", "first_score", "iterations", "passed")
        }
        state["step"] = "drafted"
        out["usage"] = _record_usage(state, usage)
        _save(pid, state)
        return out
    return run()

# ── per-episode directions (coworker feedback 3-2) ───────────────────────────
def _direction_basis(store: CanonStore) -> dict:
    """What a proposal was drawn from. Once the serial or the canon moves on it
    no longer describes the situation the next episode starts from."""
    return {"latest_episode": store.latest_episode_number(),
            "canon_version": store.load_canon().version}


@app.post("/api/projects/{pid}/directions", status_code=202)
def directions(pid: str, body: DirectionsIn):
    """Propose three directions for the next episode, as a job (~1 minute)."""
    from ..nodes import propose_directions

    store = CanonStore(_dir(pid) / "_novel")
    if not (store.root / "canon.json").exists():
        raise HTTPException(400, "run /lock first")
    _llm(_load(pid))

    def work(ctx: jobs.JobContext) -> dict:
        state = _load(pid)
        llm, usage = _llm(state)
        ctx.stage("directions", f"{body.episode}화 방향 제안")
        ns = store.load_north_star()
        opts = propose_directions(
            llm, episode_number=body.episode, profile=store.load_genre_profile(),
            north_star=ns, canon=store.load_canon(),
            arc_map=effective_arc_map(store.load_arc_map(), llm, ns),
            rhythm=store.load_rhythm(), foreshadow=store.load_foreshadow(),
            summary=store.load_summary())
        state["usage"] = _record_usage(state, usage)
        _save(pid, state)
        return {"episode": body.episode, "basis": _direction_basis(store),
                "options": [o.model_dump() | {"directive": o.as_directive()} for o in opts]}

    return _start_job(pid, "directions", lambda ctx: _guard(lambda: work(ctx)),
                      meta={"episode": body.episode})


@app.get("/api/projects/{pid}/directions/latest")
def directions_latest(pid: str):
    job = jobs.latest(_dir(pid), "directions")
    if not job or job.get("status") != "done":
        return {"episode": None, "options": [], "stale": False}
    r = job["result"]
    store = CanonStore(_dir(pid) / "_novel")
    return {**r, "stale": r["basis"] != _direction_basis(store)}


# ── 한꺼번에 집필 (coworker feedback 3-1) ───────────────────────────────────
def _forbidden(state: dict, profile) -> list[str]:
    """The author's absolute prohibitions as blocker terms (테스트 피드백 1-④),
    from the interview's hard-rule answers plus the profile's anti-patterns."""
    return forbidden_terms_from(
        hard_rules=[a["answer"] for a in state.get("answers", []) if a.get("hard_rule")],
        anti_patterns=profile.forbidden_anti_patterns)


def _usage_sum(base: dict, run: Usage) -> Usage:
    s = load_settings()
    return Usage(
        input_tokens=base.get("input_tokens", 0) + run.input_tokens,
        output_tokens=base.get("output_tokens", 0) + run.output_tokens,
        cached_tokens=base.get("cached_tokens", 0) + run.cached_tokens,
        cache_write_tokens=base.get("cache_write_tokens", 0) + run.cache_write_tokens,
        thinking_tokens=base.get("thinking_tokens", 0) + run.thinking_tokens,
        calls=base.get("calls", 0) + run.calls,
        price_in_per_1m=s.price_in_per_1m, price_out_per_1m=s.price_out_per_1m,
        cache_read_multiplier=s.cache_read_multiplier, usd_krw=s.usd_krw)


# Measured on the live arc-v8 run: ₩1,500-1,800 per episode at $10/M output
# (Sonnet 5), and 12-20 minutes per episode on Opus 5.5. Rough by design — the
# point is that the author sees "hours, tens of thousands of won" before
# pressing a button that commits every episode unattended.
_EP_KRW_AT_10 = (1500.0, 1800.0)
_EP_MINUTES = (12, 20)


@app.get("/api/projects/{pid}/serial/estimate")
def serial_estimate(pid: str, target_episodes: int | None = None):
    from ..driver import resolve_target_episodes

    store = CanonStore(_dir(pid) / "_novel")
    target, warning = resolve_target_episodes(target_episodes, store.load_arc_map())
    latest = store.latest_episode_number()
    remaining = max(0, target - latest)
    scale = load_settings().price_out_per_1m / 10.0
    return {"target": target, "latest": latest, "remaining": remaining, "warning": warning,
            "krw_min": round(remaining * _EP_KRW_AT_10[0] * scale),
            "krw_max": round(remaining * _EP_KRW_AT_10[1] * scale),
            "minutes_min": remaining * _EP_MINUTES[0],
            "minutes_max": remaining * _EP_MINUTES[1]}


@app.post("/api/projects/{pid}/serial", status_code=202)
def serial(pid: str, body: SerialIn):
    """Write from the next episode to the target as one job — the unattended
    driver, with progress, a budget cap and a stop that finishes the episode in
    hand. Every passing episode is committed without asking; the 매화 생성
    tab is the way to read each one first."""
    from ..driver import RunConfig, resolve_target_episodes, run_serial

    store = CanonStore(_dir(pid) / "_novel")
    if not (store.root / "canon.json").exists():
        raise HTTPException(400, "run /lock first")
    _llm(_load(pid))
    target, warning = resolve_target_episodes(body.target_episodes, store.load_arc_map())
    if store.latest_episode_number() >= target:
        raise HTTPException(400, f"이미 {target}화까지 썼습니다")

    def work(ctx: jobs.JobContext) -> dict:
        state = _load(pid)
        base_usage = dict(state.get("usage") or {})
        s = load_settings()
        # The cap is what THIS run spends. The console meter is the project's
        # lifetime total, and capping by it would stop at once on any project
        # that had already spent.
        run_usage = _usage_sum({}, Usage())
        llm = build_llm(s, usage=run_usage)
        outcomes: list[dict] = []
        ctx.progress(stage="serial", stage_label=f"{store.latest_episode_number() + 1}/{target}화 집필",
                     progress={"target": target, "outcomes": outcomes, "krw": 0})

        def started(ep: int) -> None:
            ctx.progress(stage="serial", stage_label=f"{ep}/{target}화 집필",
                         stage_detail=f"커밋 {sum(o['committed'] for o in outcomes)} · "
                                      f"반려 {sum(not o['committed'] for o in outcomes)} · "
                                      f"₩{run_usage.krw:,.0f}")

        def ended(o) -> None:
            if not o.committed and o.prose:
                (_dir(pid) / f"ep{o.episode:02d}.rejected.txt").write_text(o.prose, encoding="utf-8")
            outcomes.append({"episode": o.episode, "committed": o.committed, "chars": o.chars,
                             "score": o.score, "reason": o.reason})
            st = _load(pid)
            st["usage"] = _record_usage(st, _usage_sum(base_usage, run_usage))
            st["step"] = "drafted"
            _save(pid, st)
            ctx.progress(progress={"target": target, "outcomes": outcomes,
                                   "krw": round(run_usage.krw)})

        report = run_serial(
            llm, store, usage=run_usage,
            config=RunConfig(target_episodes=target, max_krw=body.budget_krw,
                             revise_iterations=body.iterations,
                             forbidden_terms=_forbidden(state, store.load_genre_profile())),
            on_episode_start=started, on_outcome=ended, should_stop=lambda: ctx.cancelled)
        return {"stopped_because": report.stopped_because, "completed": report.completed,
                "committed": report.committed_episodes, "krw": round(run_usage.krw),
                "outcomes": outcomes, "warning": warning}

    return _start_job(pid, "serial", lambda ctx: _guard(lambda: work(ctx)),
                      meta={"target": target})


@app.get("/api/projects/{pid}/episodes/{n}")
def episode_read(pid: str, n: int):
    """A committed episode, or the last rejected attempt at it."""
    store = CanonStore(_dir(pid) / "_novel")
    rec = store.load_episode(n)
    if rec is not None:
        return {"episode": n, "prose": rec.prose, "committed": True}
    rejected = _dir(pid) / f"ep{n:02d}.rejected.txt"
    if rejected.exists():
        return {"episode": n, "prose": rejected.read_text(encoding="utf-8"), "committed": False}
    raise HTTPException(404, f"{n}화 원고가 없습니다")


# ── background jobs ──────────────────────────────────────────────────────────
def _start_job(pid: str, kind: str, fn, *, meta: dict | None = None) -> dict:
    try:
        job = jobs.start(_dir(pid), kind, fn, meta=meta)
    except jobs.Busy as b:
        j = b.job
        where = j.get("stage_label", "") + (f" {j['stage_detail']}" if j.get("stage_detail") else "")
        raise HTTPException(409, f"이미 실행 중인 작업이 있습니다 ({where}). "
                                 "끝나거나 취소한 뒤 다시 시도하세요.") from b
    return {"job_id": job["id"], "status": job["status"]}


@app.get("/api/projects/{pid}/jobs/latest")
def job_latest(pid: str, kind: str | None = None):
    """The most recent job — how the console gets its last result back after a
    reload or a dropped connection."""
    return jobs.latest(_dir(pid), kind) or {"id": None}


@app.get("/api/projects/{pid}/jobs/{job_id}")
def job_get(pid: str, job_id: str):
    job = jobs.get(_dir(pid), job_id)
    if job is None:
        raise HTTPException(404, f"그런 작업이 없습니다: {job_id}")
    return job


@app.post("/api/projects/{pid}/jobs/{job_id}/cancel")
def job_cancel(pid: str, job_id: str):
    """Stop at the next stage boundary. Nothing is committed before the last
    stage, so a cancelled episode leaves canon untouched."""
    if not jobs.cancel(_dir(pid), job_id):
        raise HTTPException(400, "실행 중인 작업이 아닙니다")
    return {"cancelling": job_id}


# ── static UI ────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
