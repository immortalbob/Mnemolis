"""
LLM client helper — supports both Ollama native API and OpenAI-compatible API.
Used by router.py and kiwix.py for routing and book selection calls.

Supported backends via LLM_API_TYPE:
  "ollama"  — Ollama native /api/generate (default)
  "openai"  — OpenAI-compatible /v1/chat/completions (llama-server, LM Studio, etc.)
"""

import logging
import requests
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persistent HTTP session — connection reuse across every LLM call
# ---------------------------------------------------------------------------
#
# Found while investigating why singleflight (v3.50.13) didn't move `auto`'s
# cold-path p99 plateau, despite directly confirming the deduplication
# mechanism itself works in isolation: every single call into this module
# used the bare `requests.post()` module function, never a `requests.Session`
# — meaning every LLM call (book selection, source routing, fusion-source
# selection, disambiguation candidates) opened a brand-new TCP connection to
# the LLM backend and tore it down again immediately after, on every single
# call, with zero reuse. This is the identical class of bug
# `uptime_kuma.py`'s own connection used to have (a fresh Socket.IO
# connect+login cycle on every call) before that was found and fixed — see
# wiki/Caching.md's "Why uptime's connection is persistent..." section and
# wiki/The-Benchmark-Investigation-Log.md's Thread 1 for the full history of
# that fix and how long it took to actually find, despite looking like a
# caching problem at first.
#
# This module's case is structurally simpler than Uptime Kuma's — there's no
# login/session state to track and no liveness check needed (a plain HTTP
# connection pool has no equivalent of "logged in vs not"; `requests`' own
# adapter already transparently opens a fresh connection if a pooled one has
# gone stale or dead), so a single, eagerly-created module-level `Session`
# is sufficient. `requests.Session` is documented as safe for concurrent use
# across threads for making requests (the unsafe case is concurrent
# *mutation* of shared session state like `session.headers`, which nothing
# in this module ever does after construction) — Mnemolis's own concurrent
# request model (FastAPI's /search route is synchronous, so Starlette
# already runs real concurrent requests on its own thread pool) makes this
# the actual, live concurrency shape this needs to be safe under, not a
# theoretical one.
#
# Plain module-level singleton, not a lazy-init-with-lock accessor like
# `uptime_kuma.get_connection()` — Session() construction does no I/O at
# all (it just builds an empty connection-pool adapter), so there's no
# "first caller pays a real connection cost" race to guard against the way
# Uptime Kuma's actual login call has.
#
# Pool size explicitly set via settings.llm_connection_pool_size rather
# than left at requests' own library default (10) — see that setting's
# own comment in app/config.py for the real concurrency numbers
# (Starlette's 40-thread default limiter; a 20-concurrent-user Locust
# benchmark) behind the chosen size. Mounted on both schemes since
# LLM_URL could plausibly be configured with either, even though every
# real deployment this project documents uses plain http://.
_session = requests.Session()
_pool_adapter = requests.adapters.HTTPAdapter(
    pool_connections=settings.llm_connection_pool_size,
    pool_maxsize=settings.llm_connection_pool_size,
)
_session.mount("http://", _pool_adapter)
_session.mount("https://", _pool_adapter)


def is_configured() -> bool:
    """Return True if an LLM backend is configured."""
    return bool(settings.llm_url and settings.llm_model)


def complete(prompt: str, max_tokens: int = 100, temperature: float = 0.0) -> str | None:
    """
    Send a prompt to the configured LLM backend and return the response text.
    Returns None on failure.

    Supports:
    - Ollama native API (LLM_API_TYPE=ollama)
    - OpenAI-compatible API (LLM_API_TYPE=openai)
    """
    if not is_configured():
        return None

    api_type = settings.llm_api_type.lower().strip()

    try:
        if api_type == "openai":
            return _complete_openai(prompt, max_tokens, temperature)
        else:
            return _complete_ollama(prompt, max_tokens, temperature)
    except Exception as e:
        _LOGGER.warning("LLM completion failed (%s): %s", api_type, e)
        return None


def _complete_ollama(prompt: str, max_tokens: int, temperature: float) -> str | None:
    """Call Ollama native /api/generate endpoint.

    Sends keep_alive — see settings.llm_keep_alive's own comment in
    app/config.py for why this exists and why it's configurable to any
    value Ollama itself accepts (a duration string, plain seconds, "-1"
    for never-unload, "0" for unload-immediately), not a fixed Mnemolis-
    specific shape.
    """
    resp = _session.post(
        f"{settings.llm_url}/api/generate",
        json={
            "model": settings.llm_model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": settings.llm_keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    # Handle thinking models (qwen3 etc) that return empty response with thinking field
    raw = data.get("response", "").strip()
    if not raw:
        thinking = data.get("thinking", "")
        lines = [line.strip() for line in thinking.splitlines() if line.strip()]
        raw = lines[-1] if lines else ""

    return raw.strip(".").strip() or None


def _complete_openai(prompt: str, max_tokens: int, temperature: float) -> str | None:
    """Call OpenAI-compatible /v1/chat/completions endpoint.

    Deliberately does NOT send keep_alive, unlike _complete_ollama()
    above. Confirmed via a real, externally-reported gap (not assumed):
    Ollama's own OpenAI-compatible endpoint silently ignores keep_alive
    when passed through OpenAI-SDK-style requests, falling back to
    whatever the server's own ambient default is regardless of what's
    sent — and a genuinely different OpenAI-compatible backend
    (llama-server, LM Studio) has no standard equivalent concept at all,
    since "keep a model resident in VRAM between calls" isn't a concern
    those typically expose as a per-request parameter the same way.
    Sending a field that's either silently dropped or meaningless to the
    actual backend would be a false promise of control this setting
    can't actually deliver on this path — left out rather than sent and
    hoped for.
    """
    resp = _session.post(
        f"{settings.llm_url}/v1/chat/completions",
        json={
            "model": settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        return None

    message = choices[0].get("message", {})
    raw = message.get("content", "").strip()

    # Found via a deliberate "bulletproofing" pass, confirmed against
    # multiple independent real-world bug reports of this exact failure
    # mode: thinking models served via an OpenAI-compatible endpoint
    # (the actual real backend this project uses — llama-server with
    # Qwen3-Coder-30B) routinely return an EMPTY content field with all
    # real output sitting in a separate reasoning_content field instead
    # — the same underlying problem _complete_ollama already has a
    # real, working fallback for via Ollama's own "thinking" field, just
    # never mirrored here. llama.cpp's server defaults to this exact
    # "deepseek" reasoning_format convention (message.reasoning_content),
    # which is also the convention most other OpenAI-compatible servers
    # use. Without this fallback, a thinking model on this code path
    # would silently return None for every single completion — not a
    # contrived edge case, but the literal default behavior for the
    # specific kind of model this project's own README documents using
    # on this backend.
    if not raw:
        reasoning = message.get("reasoning_content", "") or message.get("reasoning", "")
        lines = [line.strip() for line in reasoning.splitlines() if line.strip()]
        raw = lines[-1] if lines else ""

    return raw.strip(".").strip() or None

