"""Live model catalogues + role recommendations.

Two connections can be configured (e.g. 1 = OpenRouter, 2 = DeepSeek direct). Each role
(writer / fast / reviewer) picks a model from either one. Catalogues are fetched live from the
provider's /models endpoint (cached 6 h), so new models appear automatically; recommendations pick
the newest version of a preferred family, so they also stay current.
"""
import re

import httpx

from . import db
from .config import get_settings

ROLES = {
    "main": "Writer: paper sections, reports, polishing (quality matters most)",
    "fast": "Fast: query planning, screening abstracts, extraction, JSON (cheap and quick)",
    "review": "Reviewer: simulated peer review (best from a different model family than the writer)",
}

# family patterns per role, best first; the newest version of the first family found wins
PREFS = {
    "main": {
        "balanced": [r"anthropic/claude-sonnet-[\d.]+$", r"^claude-sonnet-[\d-]+$", r"openai/gpt-[\d.]+-sol$",
                     r"google/gemini-[\d.]+-pro$", r"deepseek/deepseek-v[\d.]+-pro$", r"^deepseek-v[\d.]+-pro$"],
        "quality": [r"anthropic/claude-opus-[\d.]+$", r"^claude-opus-[\d-]+$", r"anthropic/claude-sonnet-[\d.]+$"],
        "budget": [r"^deepseek-v[\d.]+-pro$", r"deepseek/deepseek-v[\d.]+-pro$", r"qwen/qwen[\d.]+-max", r"^deepseek-chat$"],
    },
    "fast": {
        "balanced": [r"^deepseek-flash$", r"google/gemini-[\d.]+-flash$", r"deepseek/deepseek-v[\d.]+-flash$",
                     r"^claude-haiku-", r"openai/gpt-[\d.]+-luna$", r"qwen/qwen[\d.]+-flash$", r"^gemini-[\d.]+-flash$"],
        "quality": [r"google/gemini-[\d.]+-flash$", r"^claude-haiku-", r"^deepseek-flash$"],
        "budget": [r"^deepseek-flash$", r"deepseek/deepseek-v[\d.]+-flash$", r"qwen/qwen[\d.]+-flash$", r"z-ai/glm-[\d.]+-flash$"],
    },
    "review": {
        "balanced": [r"openai/gpt-[\d.]+-sol$", r"google/gemini-[\d.]+-pro$", r"^deepseek-v[\d.]+-pro$", r"deepseek/deepseek-v[\d.]+-pro$"],
        "quality": [r"openai/gpt-[\d.]+-sol-pro$", r"openai/gpt-[\d.]+-sol$", r"google/gemini-[\d.]+-pro$"],
        "budget": [r"^deepseek-v[\d.]+-pro$", r"deepseek/deepseek-v[\d.]+-pro$", r"google/gemini-[\d.]+-flash$"],
    },
}
WHY = {
    "main": "Strong academic prose, follows citation rules, prompt caching cuts the cost of repeated manuscript context.",
    "fast": "Very cheap per token with a long context; reliable JSON for planning and screening.",
    "review": "A different model family critiques more independently than the writer reviewing its own text.",
}


def _conn(src: str, s=None):
    s = s or get_settings()
    if str(src) == "2":
        return s.get("llm2_provider", "openai"), (s.get("llm2_base_url") or "").rstrip("/"), s.get("llm2_api_key", "")
    return s.get("llm_provider", "openai"), (s.get("llm_base_url") or "").rstrip("/"), s.get("llm_api_key", "")


async def catalogue(src: str = "1", refresh: bool = False) -> list[dict]:
    provider, base, key = _conn(src)
    if not base and provider != "anthropic":
        return []
    ck = f"models:{provider}:{base}:{bool(key)}"
    if not refresh:
        hit = db.cache_get(ck, max_age=6 * 3600)
        if hit is not None:
            return hit
    async with httpx.AsyncClient(timeout=20) as c:
        if provider == "anthropic":
            b = (base or "https://api.anthropic.com").removesuffix("/v1")
            r = await c.get(b + "/v1/models", params={"limit": 100},
                            headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        else:
            r = await c.get(base + "/models", headers={"authorization": f"Bearer {key}"} if key else {})
        r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        p = m.get("pricing") or {}
        price = lambda k: round(float(p[k]) * 1e6, 3) if p.get(k) not in (None, "") else None  # noqa: E731
        out.append({"id": m["id"], "name": m.get("name") or m.get("display_name") or m["id"],
                    "in": price("prompt"), "out": price("completion"), "cache": price("input_cache_read"),
                    "ctx": m.get("context_length"), "created": m.get("created") or 0})
    out.sort(key=lambda m: m["id"])
    db.cache_set(ck, out)
    return out


def _version(mid: str):
    return tuple(int(x) for x in re.findall(r"\d+", mid.split("/")[-1])[:4])


def pick(models: dict[str, list[dict]], role: str, profile: str):
    """models: {src: [model,...]} -> (src, model) best match, newest version of the first family found."""
    for pat in PREFS[role][profile] + PREFS[role]["balanced"]:
        hits = [(src, m) for src, lst in models.items() for m in lst
                if re.search(pat, m["id"]) and ":" not in m["id"] and not re.search(r"-\d{4}$|exp|preview|vision|image", m["id"])]
        if hits:
            # prefer the direct connection (2) for DeepSeek models when both exist: cheaper cache + off-peak pricing
            return max(hits, key=lambda h: (_version(h[1]["id"]), h[0] == "2" and "deepseek" in h[1]["id"]))
    return None


async def recommend() -> dict:
    s = get_settings()
    models = {}
    for src in ("1", "2"):
        try:
            prov, base, key = _conn(src, s)
            if (base or prov == "anthropic") and (key or src == "1"):
                models[src] = await catalogue(src)
        except Exception:
            models[src] = []
    combos = {}
    for profile in ("balanced", "budget", "quality"):
        combo = {}
        for role in ROLES:
            hit = pick(models, role, profile)
            if hit:
                combo[role] = {"src": hit[0], "model": hit[1]["id"], "in": hit[1]["in"], "out": hit[1]["out"]}
        combos[profile] = combo
    return {"roles": ROLES, "why": WHY, "combos": combos}
