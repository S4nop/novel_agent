"""Drafter node — ContextPack → Draft (DESIGN §3).

Renders exactly one episode of Korean prose to the BeatSheet in the locked
voice. It invents no canon: anything it needs but lacks is emitted as a
[[FACT: …]] marker, which this module extracts into FactRequest[] so the
orchestrator can halt the episode at the fact gate (invariant #9).

Runs in its OWN LangGraph node that completes and checkpoints BEFORE any
human-gate node, because an interrupted node re-runs from its top on resume
(invariant #13) — an episode draft must never be re-generated for free.
"""
from __future__ import annotations

import re

from .artifacts import Draft, FactRequest
from .context_pack import ContextPack
from .llm import LLM

_FACT_RE = re.compile(r"\[\[FACT:\s*(.+?)\]\]", re.DOTALL)


def extract_fact_requests(prose: str) -> tuple[str, list[FactRequest]]:
    """Split [[FACT: …]] markers out of the prose. Pure — unit-testable."""
    requests = [FactRequest(question=m.group(1).strip()) for m in _FACT_RE.finditer(prose)]
    cleaned = _FACT_RE.sub("", prose).strip()
    return cleaned, requests


def draft_episode(llm: LLM, pack: ContextPack, *, max_tokens: int = 16384) -> Draft:
    """Generate one episode. Stateless: everything it knows comes from the pack."""
    prose = llm.text(pack.to_messages(), max_tokens=max_tokens)
    cleaned, requests = extract_fact_requests(prose)
    return Draft(
        episode_number=_episode_number(pack),
        prose=cleaned,
        fact_requests=requests,
    )


def _episode_number(pack: ContextPack) -> int:
    m = re.search(r"# 이번 화: (\d+)화", pack.volatile_suffix)
    return int(m.group(1)) if m else 0
