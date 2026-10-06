"""Minimal LLM client: OpenAI-compatible APIs (OpenAI, OpenRouter, DeepSeek, Gemini, Ollama, LM Studio,
Groq, ...) and Anthropic's native Messages API. Streaming first.

Token economy:
- `fast=True` routes a call to the optional cheaper "fast model" (query planning, extraction, JSON).
- `cache_system=True` marks the system prompt cacheable on Claude models (Anthropic API or OpenRouter),
  so repeated calls that share a long prefix (e.g. drafting section after section) pay ~10% for it.
- Every call is logged to the usage table (exact counts when the provider reports them, else estimated).
"""
import json
import re
from typing import AsyncIterator

import httpx

from . import db
from .config import get_settings


class LLMError(Exception):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def _uses_new_openai_params(model: str) -> bool:
    m = model.lower().split("/")[-1]
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


ROLE_KEYS = {"main": ("llm_model", "llm_model_src"), "fast": ("llm_fast_model", "llm_fast_src"),
             "review": ("llm_review_model", "llm_review_src")}


def resolve(role: str, s=None):
    """role -> (provider, base_url, api_key, model). Fast/review fall back to the writer model when unset."""
    from .models import _conn
    s = s or get_settings()
    mk, sk = ROLE_KEYS.get(role, ROLE_KEYS["main"])
    if not (s.get(mk) or "").strip():
        mk, sk = ROLE_KEYS["main"]
    provider, base, key = _conn(s.get(sk) or "1", s)
    return provider, base, key, (s.get(mk) or "").strip()


async def stream_chat(messages: list[dict], system: str = "", temperature: float | None = None,
                      max_tokens: int | None = None, fast: bool = False, cache_system: bool = False,
                      task: str = "chat", role: str = "main") -> AsyncIterator[str]:
    s = get_settings()
    provider, base, key, model = resolve("fast" if fast else role, s)
    max_tokens = max_tokens or int(s.get("llm_max_tokens") or 8000)
    temperature = s.get("llm_temperature", 0.4) if temperature is None else temperature
    if not model:
        raise LLMError("No model configured. Open Settings and choose an AI model.")
    timeout = httpx.Timeout(600, connect=20)
    is_openrouter = "openrouter.ai" in base
    claude = provider == "anthropic" or "claude" in model.lower()
    usage = {"input": 0, "output": 0, "cached": 0, "cost": None}

    if provider == "anthropic":
        base = base or "https://api.anthropic.com"
        if base.endswith("/v1"):
            base = base[:-3]
        url = base + "/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": model, "max_tokens": max_tokens, "messages": messages, "stream": True}
        if system:
            body["system"] = ([{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
                              if cache_system else system)
    else:
        url = (base or "https://api.openai.com/v1") + "/chat/completions"
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        if system and cache_system and claude and is_openrouter:
            sys_msg = {"role": "system", "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}
        else:
            sys_msg = {"role": "system", "content": system}
        body = {"model": model, "messages": ([sys_msg] if system else []) + messages, "stream": True}
        if _uses_new_openai_params(model):
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
            body["temperature"] = float(temperature)
        if is_openrouter or "api.openai.com" in (base or "api.openai.com"):
            body["stream_options"] = {"include_usage": True}
        if is_openrouter:
            body["usage"] = {"include": True}

    out_chars = 0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode("utf-8", "ignore")[:800]
                    raise LLMError(f"LLM API error {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if provider == "anthropic":
                        t = obj.get("type")
                        if t == "content_block_delta" and obj["delta"].get("type") == "text_delta":
                            out_chars += len(obj["delta"]["text"])
                            yield obj["delta"]["text"]
                        elif t == "message_start":
                            u = obj.get("message", {}).get("usage", {})
                            usage["cached"] = u.get("cache_read_input_tokens", 0) or 0
                            usage["input"] = (u.get("input_tokens", 0) or 0) + usage["cached"] + (u.get("cache_creation_input_tokens", 0) or 0)
                        elif t == "message_delta":
                            usage["output"] = obj.get("usage", {}).get("output_tokens", usage["output"])
                        elif t == "error":
                            raise LLMError(str(obj.get("error")))
                    else:
                        if obj.get("error"):
                            raise LLMError(str(obj["error"]))
                        if obj.get("usage"):
                            u = obj["usage"]
                            usage["input"] = u.get("prompt_tokens", 0) or 0
                            usage["output"] = u.get("completion_tokens", 0) or 0
                            usage["cached"] = ((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0
                            if u.get("cost") is not None:
                                usage["cost"] = float(u["cost"])
                        choices = obj.get("choices") or []
                        if choices:
                            piece = (choices[0].get("delta") or {}).get("content")
                            if piece:
                                out_chars += len(piece)
                                yield piece
    finally:
        estimated = not (usage["input"] or usage["output"])
        if estimated:  # provider didn't report usage: ~4 chars per token
            in_chars = len(system) + sum(len(m["content"]) if isinstance(m["content"], str) else 0 for m in messages)
            usage["input"], usage["output"] = in_chars // 4, out_chars // 4
        if usage["input"] or usage["output"]:
            try:
                db.log_usage(task, model, usage["input"], usage["output"], usage["cached"], usage["cost"], estimated)
            except Exception:
                pass


async def complete(prompt: str, system: str = "", **kw) -> str:
    out = []
    async for piece in stream_chat([{"role": "user", "content": prompt}], system=system, **kw):
        out.append(piece)
    return _THINK_RE.sub("", "".join(out)).strip()


def extract_json(text: str):
    text = _THINK_RE.sub("", text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("Model did not return valid JSON")


async def complete_json(prompt: str, system: str = "", **kw):
    sys = (system + "\n\n" if system else "") + "Respond with valid JSON only. No commentary."
    text = await complete(prompt, system=sys, **kw)
    try:
        return extract_json(text)
    except ValueError:
        text = await complete(prompt + "\n\nIMPORTANT: Your previous answer was not valid JSON. "
                              "Return ONLY valid JSON.", system=sys, **kw)
        return extract_json(text)
