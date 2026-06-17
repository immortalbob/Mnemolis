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
        lines = [l.strip() for l in thinking.splitlines() if l.strip()]
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

    raw = choices[0].get("message", {}).get("content", "").strip()
    return raw.strip(".").strip() or None
