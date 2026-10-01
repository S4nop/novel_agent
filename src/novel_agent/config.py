"""Runtime configuration (DESIGN §5).

Provider-agnostic: the model is chosen entirely from the environment, so
swapping providers is a .env edit, not a code change. See `.env.example`.

    NOVEL_LLM_PROVIDER = anthropic | gemini | openai
    NOVEL_LLM_MODEL    = claude-opus-5-5 | gemini-3.6-flash | kimi-k3 | ...
    NOVEL_LLM_API_KEY  = ...
    NOVEL_LLM_EFFORT   = low | medium | high | xhigh | max  (anthropic only)
    NOVEL_LLM_BASE_URL = (openai-compatible providers only)

The deterministic core (artifacts, canon store, ledgers, context pack, lint)
does not import this module, so its tests need no env at all.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# Convenience presets for OpenAI-compatible endpoints, so users only need to
# set a key. Any other compatible provider works via NOVEL_LLM_BASE_URL.
KNOWN_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "moonshot": "https://api.moonshot.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "upstage": "https://api.upstage.ai/v1/solar",
    "openrouter": "https://openrouter.ai/api/v1",
    # Google also exposes an OpenAI-compat endpoint, but it rejects
    # safety_settings — prefer provider=gemini (native) for this project.
    "gemini-compat": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOVEL_", env_file=".env", extra="ignore"
    )

    # All human-facing agent output (gate prompts, questions, prose) is Korean.
    ui_language: str = "ko"

    # ── LLM provider ────────────────────────────────────────────────────────
    llm_provider: str = "anthropic"       # "anthropic" | "gemini" | "openai"
    llm_model: str = "claude-opus-5-5"
    llm_api_key: str = ""
    llm_base_url: str = ""                # required for provider=openai unless preset
    llm_preset: str = ""                  # optional key into KNOWN_BASE_URLS
    # Reasoning depth (anthropic only). "high" is the API default;
    # "medium" is the cost/latency step-down for a long unattended serial.
    llm_effort: str = "high"

    # Pricing for the cost meter (USD per 1M tokens) — override per model.
    # Defaults are claude-opus-5-5. Sonnet 5 is $2/$10, Opus 5 $5/$25,
    # Fable 5.1 $10/$50. Verified 2026-10-02.
    price_in_per_1m: float = 4.00
    price_out_per_1m: float = 20.00
    # A cache hit costs this much of the input price. 0.05 on Opus 5.5 and
    # 0.025 on Fable 5.1 / Mythos 5.1; every other model uses 0.1.
    cache_read_multiplier: float = 0.05
    usd_krw: float = 1400.0

    # Root directory for projects created through the web UI.
    projects_root: str = "./data/projects"

    def resolved_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url
        if self.llm_preset:
            return KNOWN_BASE_URLS.get(self.llm_preset, "")
        return ""

    def public(self) -> dict:
        """Safe to show in the UI — never includes the key itself."""
        return {
            "provider": self.llm_provider,
            "model": self.llm_model,
            "effort": self.llm_effort,
            "base_url": self.resolved_base_url() or "(provider default)",
            "key_present": bool(self.llm_api_key),
            "key_hint": (self.llm_api_key[:6] + "…") if self.llm_api_key else "",
            "ui_language": self.ui_language,
            "price_in_per_1m": self.price_in_per_1m,
            "cache_read_multiplier": self.cache_read_multiplier,
            "price_out_per_1m": self.price_out_per_1m,
        }


def load_settings() -> Settings:
    return Settings()
