"""Prompt accessors — the text itself lives in `prompts/*.md`, not here.

Everything in this module is a thin lookup so that a non-engineer can tune the
agent by editing plain text files (or the web console's 프롬프트 panel) without
touching Python. See `prompts/_README.md`.
"""
from __future__ import annotations

from .artifacts import GenreProfile
from .prompt_store import load, render


def style_rules() -> str:
    """The craft rules block. The highest-leverage prompt in the project."""
    return load("style_rules")


def voice_spec_guidance() -> str:
    return load("voice_spec_guidance")


def analyst_system() -> str:
    return load("analyst_system")


def restraint() -> str:
    return load("restraint")


def drafting_system_prompt(gp: GenreProfile) -> str:
    """System prompt for the Drafter node. Byte-stable per GenreProfile, so it
    stays in the provider's cached prefix."""
    return render(
        "drafter_system",
        audience=gp.audience,
        content_rating=gp.content_rating.value,
        sub_genre=gp.sub_genre,
        pov=gp.pov,
        tense=gp.tense,
        length_target=gp.episode_length_target,
        style_rules=style_rules(),
    )
