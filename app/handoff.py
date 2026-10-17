"""Claude mode: the AI step of a button runs in the Claude app (the user's Claude plan) instead of an API.

A.F.R.A builds exactly the same instructions it would send to an API (same prompt, library excerpts and answer
format). Instead of calling a model, it parks them as a task and waits. The page opens Claude with a one-line
message; the user presses Enter; Claude fetches the task through the A.F.R.A connector (get_task) and returns the
answer (finish_task). The waiting request then continues exactly as if an API had answered, so the result lands
in the same box, section or note, and nothing is computed twice.

Only requests that carry the page's hand-off id are parked. Any other AI step in Claude mode fails fast, so
features with a no-AI fallback (keyword search, metadata from Crossref...) use that instead of spending tokens.
"""
import asyncio
import contextvars
import time

from . import db

CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("afra_handoff", default=None)
TASKS: dict[str, dict] = {}
WAIT = 45 * 60  # how long a button waits for Claude
JSON_HINT = "Respond with valid JSON only."


def _new_id(base: str) -> str:
    if base not in TASKS:
        return base
    n = 2
    while f"{base}-{n}" in TASKS:
        n += 1
    return f"{base}-{n}"  # a second AI step in the same button: Claude is told to fetch it next


async def ask(system: str, messages: list[dict], task: str) -> str:
    from .llm import LLMError  # (llm imports this module)
    base = CURRENT.get()
    if not base:
        raise LLMError("This step uses an API model, and A.F.R.A is set to use the Claude app.")
    tid = _new_id(base)
    prompt = "\n\n".join(m["content"] if isinstance(m["content"], str) else "" for m in messages)
    t = {"id": tid, "task": task, "system": system, "prompt": prompt, "json": JSON_HINT in (system or ""),
         "status": "waiting", "created": time.time(), "result": None, "done": asyncio.Event()}
    TASKS[tid] = t
    try:
        await asyncio.wait_for(t["done"].wait(), WAIT)
    except asyncio.TimeoutError:
        t["status"] = "expired"
        raise LLMError("Claude didn't answer in time. Click the button again when Claude is ready.")
    if t["status"] == "cancelled":
        raise LLMError("Cancelled.")
    try:  # the Token Usage page shows what ran on the Claude plan (estimated: ~4 characters per token, no cost)
        db.log_usage(task, "Claude app (your plan)", (len(system or "") + len(prompt)) // 4, len(t["result"]) // 4, 0, 0, True)
    except Exception:
        pass
    return t["result"]


def _visible(t: dict) -> dict:
    return {k: v for k, v in t.items() if k != "done"}


async def get(tid: str) -> dict:
    for _ in range(30):  # Claude can be faster than the page: give the request a moment to arrive
        if tid in TASKS:
            break
        await asyncio.sleep(0.5)
    t = TASKS.get(tid)
    if not t:
        raise ValueError(f"No A.F.R.A task {tid}. Ask the user to click the button in A.F.R.A again.")
    if t["status"] in ("done", "cancelled", "expired"):
        raise ValueError(f"Task {tid} is already {t['status']}.")
    t["status"] = "claimed"
    return _visible(t)


def finish(tid: str, result: str) -> dict:
    t = TASKS.get(tid)
    if not t or t["status"] in ("done", "cancelled", "expired"):
        raise ValueError(f"Task {tid} is not waiting any more (it may have been cancelled in A.F.R.A).")
    result = (result or "").strip()
    if not result:
        raise ValueError("The answer is empty.")
    if t["json"]:
        from .llm import extract_json
        try:
            extract_json(result)
        except ValueError:
            raise ValueError("A.F.R.A needs this answer as valid JSON in the format the instructions describe. Send it again.")
    t.update(result=result, status="done", finished=time.time())
    t["done"].set()
    nxt = next((x["id"] for x in TASKS.values() if x["id"].startswith(tid.split("-")[0]) and x["status"] == "waiting"), None)
    return {"ok": True, "next": nxt}


def cancel(tid: str):
    t = TASKS.get(tid)
    if t and t["status"] in ("waiting", "claimed"):
        t["status"] = "cancelled"
        t["done"].set()


def pending() -> list[dict]:
    now = time.time()
    for k in [k for k, t in TASKS.items() if now - t["created"] > 2 * WAIT]:
        TASKS.pop(k, None)  # tidy up
    return [{"id": t["id"], "task": t["task"], "created": t["created"]} for t in TASKS.values() if t["status"] in ("waiting", "claimed")]
