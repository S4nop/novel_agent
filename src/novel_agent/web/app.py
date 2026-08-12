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
from ..canon_store import CanonStore
from ..canonicalizer import commit_episode_state
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
    plan_episode,
    seed_arc_map,
    to_north_star,
)
from ..reviser import revise_draft
from ..reviser import PASS_SCORE
from ..style import lint_prose, style_score

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
    except HTTPException:
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


class PromptIn(BaseModel):
    text: str


class CanonIn(BaseModel):
    canon: dict


class LintIn(BaseModel):
    text: str
    target_chars: int = 5200


class DraftIn(BaseModel):
    episode: int = 1
    revise: bool = True
    iterations: int = 2


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
    canon.version = previous.version + 1
    canon.last_modified_by = "author"
    store.save_canon(canon)
    state = _load(pid)
    state["canon"] = canon.model_dump(mode="json")
    _save(pid, state)
    return state["canon"]


# ── pipeline ─────────────────────────────────────────────────────────────────
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

        CanonStore(_dir(pid) / "_novel").initialize(
            genre_profile=profile, north_star=ns, canon=canon, voice_bible=voice)
        state["picked"] = body.pick
        state["north_star"] = ns.model_dump(mode="json")
        state["canon"] = canon.model_dump(mode="json")
        state["voice"] = voice.model_dump(mode="json")
        state["step"] = "locked"
        _record_usage(state, usage)
        _save(pid, state)
        return state
    return _guard(run)


@app.post("/api/projects/{pid}/episode")
def episode(pid: str, body: DraftIn):
    """Plan → draft → (optionally) revise one episode, with lint results."""
    state = _load(pid)
    store = CanonStore(_dir(pid) / "_novel")
    if not (store.root / "canon.json").exists():
        raise HTTPException(400, "run /lock first")

    def run():
        llm, usage = _llm(state)
        profile = store.load_genre_profile()
        ns = store.load_north_star()
        canon = store.load_canon()
        prev = store.load_episode(body.episode - 1)

        beats = plan_episode(
            llm, episode_number=body.episode, profile=profile, north_star=ns,
            canon=canon, arc_map=seed_arc_map(llm, ns), rhythm=store.load_rhythm(),
            foreshadow=store.load_foreshadow(), summary=store.load_summary(),
        )
        pack = ContextPackBuilder().build(
            genre_profile=profile, north_star=ns, voice_bible=store.load_voice_bible(),
            canon=canon, beat_sheet=beats, foreshadow=store.load_foreshadow(),
            rhythm=store.load_rhythm(), summary=store.load_summary(),
            current_episode=body.episode, previous_episode=prev,
        )
        draft = draft_episode(llm, pack, max_tokens=32768)
        first = style_score(draft.prose, target_chars=beats.length_target)

        result = None
        if body.revise:
            result = revise_draft(llm, draft, pack, target_chars=beats.length_target,
                                  max_iterations=body.iterations)
            draft = result.draft

        (_dir(pid) / f"ep{body.episode:02d}.txt").write_text(draft.prose, encoding="utf-8")

        # Advance every cross-episode ledger and persist the episode.
        # (Previously rhythm/foreshadow were loaded but never saved, so pacing
        #  debt and foreshadow deadlines reset on every episode.)
        commit_episode_state(store, draft, beats)
        out = {
            "episode": body.episode,
            "beat_sheet": beats.model_dump(mode="json"),
            "prose": draft.prose,
            "chars": draft.char_count,
            "target": beats.length_target,
            "first_score": first,
            "score": style_score(draft.prose, target_chars=beats.length_target),
            "iterations": result.iterations if result else 0,
            "passed": result.passed if result else None,
            "fact_requests": [f.question for f in draft.fact_requests],
            "violations": [_violation_json(v) for v in
                           lint_prose(draft.prose, target_chars=beats.length_target)],
            "pass_score": PASS_SCORE,
        }
        state.setdefault("episodes", {})[str(body.episode)] = {
            k: out[k] for k in ("chars", "score", "first_score", "iterations", "passed")
        }
        state["step"] = "drafted"
        out["usage"] = _record_usage(state, usage)
        _save(pid, state)
        return out
    return _guard(run)


# ── static UI ────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
