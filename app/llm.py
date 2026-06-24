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
    """Call Ollama native /api/generate endpoint."""
    resp = requests.post(
        f"{settings.llm_url}/api/generate",
        json={
            "model": settings.llm_model,
            "prompt": prompt,
            "stream": False,
            "think": False,
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
    """Call OpenAI-compatible /v1/chat/completions endpoint."""
    resp = requests.post(
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
