"""Settings storage (data/settings.json) with environment-variable fallbacks."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RT_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULTS = {
    # LLM
    "llm_provider": "openai",          # "openai" (any OpenAI-compatible API) | "anthropic"
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "",
    "llm_model": "gpt-4o-mini",
    "llm_model_src": "1",              # which connection each role uses: "1" or "2"
    "llm_fast_model": "",             # optional cheaper model for planning/extraction steps
    "llm_fast_src": "1",
    "llm_review_model": "",           # optional reviewer model (different family = more independent critique)
    "llm_review_src": "1",
    # optional second connection, e.g. DeepSeek direct next to OpenRouter
    "llm2_provider": "openai",
    "llm2_base_url": "",
    "llm2_api_key": "",
    "llm_temperature": 0.4,
    "llm_max_tokens": 8000,
    # Search
    "tavily_api_key": "",
    "semantic_scholar_api_key": "",
    "contact_email": "",
    "zotero_user_id": "",             # zotero.org/settings/keys (Web API); desktop sync needs no key
    "zotero_api_key": "",               # polite pool for OpenAlex / Crossref
    "default_sources": ["openalex", "europepmc", "crossref"],
    "sources_version": 2,
    # Writing
    "default_language": "English",
    "default_citation_style": "apa",
    "default_template": "apa7",
}

ENV_MAP = {
    "llm_provider": "LLM_PROVIDER",
    "llm_base_url": "LLM_BASE_URL",
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "tavily_api_key": "TAVILY_API_KEY",
    "semantic_scholar_api_key": "S2_API_KEY",
}


def get_settings() -> dict:
    s = dict(DEFAULTS)
    saved = {}
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            s.update(saved)
        except Exception:
            pass
    if saved and saved.get("sources_version", 1) < 2:  # saved before Europe PMC existed: switch it on once
        s["default_sources"] = list(dict.fromkeys(list(s.get("default_sources") or []) + ["europepmc"]))
        s["sources_version"] = 2
    for key, env in ENV_MAP.items():
        if not s.get(key) and os.environ.get(env):
            s[key] = os.environ[env]
    return s


def save_settings(new: dict) -> dict:
    s = get_settings()
    for k, v in new.items():
        if k in DEFAULTS:
            s[k] = v
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s
