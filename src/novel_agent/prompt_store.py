"""Editable prompt store — every prompt lives in `prompts/*.md`, not in code.

Why: prompts are the product's main tuning surface, and the people best placed
to tune them (a PM, an editor, the author) should not have to edit Python
f-strings to do it. Each prompt is one plain-text file with `${placeholder}`
markers, so editing is safe and obvious.

Guardrails, because a silently broken prompt is worse than a crash:
  • rendering validates that every required placeholder is still present, so
    deleting `${idea}` fails loudly instead of quietly starving the model
  • unknown placeholders raise rather than render as literal text
  • `${...}` uses string.Template, which ignores braces/JSON in the prose

Override the directory with NOVEL_PROMPTS_DIR to A/B a whole prompt set.
"""
from __future__ import annotations

import os
import pathlib
from string import Template

# Placeholders each prompt MUST keep. If an edit drops one, the model would
# stop receiving that data — we fail loudly instead.
REQUIRED: dict[str, tuple[str, ...]] = {
    "genre_inference": ("idea",),
    "northstar": ("idea", "audience", "sub_genre", "tropes", "angle", "prior", "restraint"),
    "canon_init": ("idea", "premise", "core_conflict", "protagonist_edge",
                   "episode_engine", "hard_rules", "audience", "sub_genre", "pov",
                   "restraint", "voice_spec_guidance"),
    "episode_plan": ("premise", "episode_engine", "hard_rules", "sub_genre",
                     "catharsis_cadence", "max_frustration", "forbidden", "arc_goal",
                     "arc_payoff", "cast", "story_so_far", "pacing_directive",
                     "due_seeds", "episode_number"),
    "interview_request": ("idea", "max_questions", "required_topics"),
    "drafter_system": ("audience", "content_rating", "sub_genre", "pov", "tense",
                       "length_target", "style_rules"),
    "revise_instruction": ("prefix", "suffix", "findings", "prose"),
    "continuity_system": (),
    "continuity_check": ("canon", "episode_number", "prose"),
    "canon_extract_system": (),
    "canon_extract": ("canon", "episode_number", "prose"),
    "craft_system": (),
    "craft_check": ("genre", "voices", "episode_number", "prose"),
    "opening_ending_system": (),
    "opening_ending_check": ("episode_number", "opening", "ending"),
}

_CACHE: dict[str, str] = {}


def prompts_dir() -> pathlib.Path:
    env = os.environ.get("NOVEL_PROMPTS_DIR")
    if env:
        return pathlib.Path(env)
    return pathlib.Path(__file__).resolve().parents[2] / "prompts"


def clear_cache() -> None:
    _CACHE.clear()


def list_prompts() -> list[str]:
    return sorted(p.stem for p in prompts_dir().glob("*.md") if not p.stem.startswith("_"))


def load(name: str) -> str:
    """Raw prompt text, exactly as the editor wrote it."""
    if name in _CACHE:
        return _CACHE[name]
    path = prompts_dir() / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"프롬프트 파일이 없습니다: {path}\n"
            f"사용 가능: {', '.join(list_prompts()) or '(없음)'}"
        )
    _CACHE[name] = path.read_text(encoding="utf-8").strip()
    return _CACHE[name]


def save(name: str, text: str) -> None:
    """Write an edited prompt back — but only if it is still valid.

    Refusing here is the whole safety net: a saved prompt missing `${idea}`
    would degrade every downstream artifact without raising anything.
    """
    problems = validate(name, text)
    if problems:
        raise ValueError(f"[{name}.md] 저장할 수 없습니다:\n- " + "\n- ".join(problems))
    (prompts_dir() / f"{name}.md").write_text(text.strip() + "\n", encoding="utf-8")
    _CACHE.pop(name, None)


def placeholders(text: str) -> set[str]:
    return {m.group("named") or m.group("braced")
            for m in Template.pattern.finditer(text)
            if m.group("named") or m.group("braced")}


def validate(name: str, text: str | None = None) -> list[str]:
    """Return [] if OK, else a list of human-readable problems (Korean)."""
    text = load(name) if text is None else text
    found = placeholders(text)
    problems = []
    for req in REQUIRED.get(name, ()):
        if req not in found:
            problems.append(f"필수 자리표시자 ${{{req}}} 가 빠졌습니다 — 이 값이 모델에 전달되지 않습니다")
    for extra in sorted(found - set(REQUIRED.get(name, ()))):
        problems.append(f"알 수 없는 자리표시자 ${{{extra}}} — 오타이거나 지원되지 않는 값입니다")
    if not text.strip():
        problems.append("프롬프트가 비어 있습니다")
    return problems


def render(name: str, **values) -> str:
    """Load and fill a prompt. Raises with a readable message on a bad edit."""
    text = load(name)
    problems = validate(name, text)
    if problems:
        raise ValueError(f"[{name}.md] 프롬프트 오류:\n- " + "\n- ".join(problems))
    try:
        return Template(text).substitute(**values)
    except KeyError as e:  # placeholder present but caller gave no value
        raise ValueError(f"[{name}.md] 값이 제공되지 않은 자리표시자: {e}") from e
