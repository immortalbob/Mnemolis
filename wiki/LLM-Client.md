# LLM Client

`llm.py` is the one place in Mnemolis that talks to a real language model — used by [Routing](Routing) for LLM-assisted source selection and discourse-framing escalation, and by [Kiwix Disambiguation](Kiwix-Disambiguation) for candidate book selection. Every other source in this project is purely deterministic; this is the single module where Mnemolis's behavior depends on a model's actual output rather than fixed logic, and it's built to fail safely the moment that dependency isn't available.

## Two backends, one interface

`LLM_API_TYPE` picks between two real wire protocols:

- **`ollama`** (default) — Ollama's native `/api/generate` endpoint
- **`openai`** — the OpenAI-compatible `/v1/chat/completions` shape, the convention `llama-server`, LM Studio, and most other local-inference servers speak

Everything above `llm.py` calls one function, `complete(prompt, max_tokens, temperature)`, and never needs to know which backend is actually configured. `is_configured()` is the other half of that contract — every caller checks it first, and `complete()` itself returns `None` immediately if it's `False`, so nothing above this module needs its own "is the LLM even set up" branch.

```text
                    complete(prompt, max_tokens, temperature)
                                       │
                                       ▼
                          is_configured()? (LLM_URL + LLM_MODEL set)
                                       │
                         ┌─────────────┴─────────────┐
                         ▼ no                          ▼ yes
                    Return None                  LLM_API_TYPE == "openai"?
                    immediately                          │
                                            ┌─────────────┴─────────────┐
                                            ▼ yes                       ▼ no (default)
                                  _complete_openai()             _complete_ollama()
                                            │                           │
                                            └─────────────┬─────────────┘
                                                           ▼
                                              Any exception anywhere
                                              in this call? → caught,
                                              logged, return None
```

`complete()`'s own `try/except` wraps the entire backend call — a connection refused, a timeout, a malformed response, an HTTP error status, all collapse to the same `None` return rather than an exception propagating up into routing logic that was never written to handle one. This is why a dark LLM backend (Ollama down, network partition, container not yet started) degrades Mnemolis's *intelligence*, not its *availability* — every real caller has a deterministic fallback for exactly this case, covered in [Routing](Routing#two-ways-a-source-gets-chosen) and [Kiwix Disambiguation](Kiwix-Disambiguation).

## How thinking models are handled on both backends

Both backends need to handle one real, common case in modern local models: a "thinking" model (Qwen3 and similar reasoning-tuned families) often puts its actual answer in a separate field from the one a naive client would read, especially when prompted not to think out loud but does anyway.

**Ollama's native API** — if the `response` field comes back empty, fall through to the `thinking` field, take its last non-empty line, and use that as the real answer.

**The OpenAI-compatible path** — if `message.content` is empty, fall through to `message.reasoning_content` (or the `reasoning` field variant some servers use instead), take its last non-empty line, and use that. `llama.cpp`'s own server documentation confirms its default `reasoning_format` ("deepseek" style) puts a thinking model's real output in `message.reasoning_content`, leaving `message.content` empty — and this is also the convention most other OpenAI-compatible servers follow.

Both fallbacks exist for the identical underlying reason — a thinking model's real answer needs to be found somewhere even when the field a naive client expects it in is empty — implemented twice because the two backends genuinely shape that fallback data differently, not because the logic was duplicated carelessly.

## Why "last non-empty line," specifically

Both fallbacks take the *last* non-empty line of the thinking/reasoning text, not the whole thing, and not the first line. A thinking model's reasoning trace is, by definition, working *toward* an answer — the line that actually states the conclusion is reliably the last one, not somewhere in the middle of working through the problem out loud. Every real routing prompt this module sends also asks for a short, constrained answer (a bare source name, a one-line classification) specifically so this convention holds reliably in practice.

## What happens when nothing is configured at all

`is_configured()` returns `False` whenever `LLM_URL` or `LLM_MODEL` is blank — the default, out-of-the-box state. This isn't a degraded or error mode; it's a fully supported way to run Mnemolis, just with less of its routing intelligence available. [Routing](Routing) falls back to keyword-only matching, [Kiwix Disambiguation](Kiwix-Disambiguation) and [Query Expansion](Query-Expansion) never trigger, and book selection falls back to a fixed "search Wikipedia first" rule — see [Configuration Reference](Configuration-Reference) for the complete list of what depends on this setting being present.
