# MCP Server

Mnemolis exposes everything through two interfaces at once: a REST API and an [MCP](https://modelcontextprotocol.io/) server, both backed by the exact same routing/decomposition/fusion logic underneath. The REST API is what Home Assistant and most automation scripts use; MCP is what lets Claude Desktop, Cursor, or any other MCP-aware client query Mnemolis directly as a tool.

## What's actually exposed

The MCP server exposes exactly **one tool**, called `search`, built with FastMCP's decorator-based registration:

```python
@mcp.tool()
async def search(
    query: str,
    source: str = "auto",
    fusion_sources: list[str] | None = None,
) -> str:
    """Search across local and remote knowledge sources via Mnemolis. ..."""
```

This mirrors `POST /search`'s own request shape almost exactly — `query`, `source`, `fusion_sources` mean the same thing in both places, and the same [Routing](Routing), [Query Decomposition](Query-Decomposition), and [Conditional Query Detection](Conditional-Query-Detection) logic runs underneath regardless of which interface the call came through.

**A real, deliberate tradeoff worth knowing about:** `source` is typed as a plain `str`, not an `Enum`/`Literal`, even though the values are genuinely constrained to a fixed set (`auto`, `kiwix`, `forecast`, `news`, `web`, `uptime`, `ha`, `fusion`). This isn't an oversight — FastMCP's `@mcp.tool()` decorator currently has no supported way to register a fully custom JSON Schema (an open, unresolved upstream SDK issue), so the schema is inferred entirely from type hints. Using `Enum`/`Literal` here would generate a `$ref`/`$defs`-based schema with a separate, real, open compatibility bug that gets at least one real MCP client to reject the tool outright. A plain string with the valid values documented in the docstring sidesteps that specific bug, at the honest cost of losing schema-level enforcement — an invalid `source` value is now only caught by Mnemolis's own routing logic, not at the protocol layer.

## A real difference worth knowing about

MCP's `search` tool calls `route()`, the simpler, backward-compatible wrapper that returns just the result string — not `route_with_source()`, which is what the REST API's `/search` endpoint uses to also report `source_used` (including whether a [fallback](Routing#fallback-when-a-source-comes-back-empty) occurred). This means an MCP client gets the plain answer text, the same content a REST caller would get, but **never** learns which source actually answered, or whether a fallback happened along the way. If you need that metadata, you need the REST API — MCP's contract is intentionally just "ask a question, get an answer," with no provenance attached.

## How it's actually transported

MCP runs over **Streamable HTTP**, mounted at `/mcp` on the main FastAPI app as a sub-application — shares the same container, port, and network exposure as the REST API, no separate process to manage.

This wasn't always the case. Mnemolis originally used **SSE (Server-Sent Events)** transport, with the tool's schema hand-written as a raw JSON Schema dict and the low-level `mcp.server.Server` class. Two independent, real reasons drove a full migration:

1. **SSE is being superseded across the MCP ecosystem.** Official FastMCP documentation states directly that SSE "exists only for backward compatibility and shouldn't be used in new projects" — patching the old transport further would have meant investing in something already on its way out.
2. **The old SSE handler manually accessed `request._send`**, a private Starlette attribute. This genuinely matched the official SDK's own low-level reference examples (it wasn't a Mnemolis-specific shortcut), but FastMCP's high-level `streamable_http_app()` avoids the need for any application code to touch private Starlette internals at all.

### A real, currently-open ecosystem bug found during the migration

The migration surfaced a genuine, separate bug — not in Mnemolis's design, but in how `FastMCP.streamable_http_app()` itself behaves: it lazily creates **one** `StreamableHTTPSessionManager` and caches it on the `FastMCP` instance. Calling `streamable_http_app()` again still returns the *same* cached session manager wrapped in a new app object — but `StreamableHTTPSessionManager.run()` can only ever be entered **once** per instance. A module-level `mcp_app` built once at import time meant every independent app lifecycle (every container restart; every test file's own `TestClient` instance) tried to re-run the same already-exhausted session manager, raising a hard `RuntimeError` on the second attempt.

This is real and currently affects the broader ecosystem, not just Mnemolis — multiple independent reports describe the identical error, both in test suites and in real production deployments under certain conditions (concurrent startup, serverless cold starts).

The fix, `get_mcp_app()` in `mcp_server.py`, resets the FastMCP instance's cached session manager reference before rebuilding the app — but the first version of this fix was itself genuinely incomplete, and worth understanding why. Resetting the cached reference and building a fresh app object works fine *in isolation*, but Mnemolis's actual `/mcp` route is mounted **once**, at module-import time — the already-mounted route still held a reference to the *original* app object's request handler and lifespan closure, regardless of what the module-level `mcp_app` variable was reassigned to afterward. The complete, correct fix rebuilds the app fresh **and** finds the actual `Mount` route object in `main.py`'s router and reassigns its `.app` attribute directly, so the object whose lifespan gets entered during startup is genuinely the same object serving real requests during that same lifecycle — confirmed by directly tracing through three consecutive simulated app lifecycles before and after each version of the fix.

A separate, more serious concern was found during the same research but is **not** something this fix touches: an open upstream issue describes a race condition where the session manager can report "shutting down" immediately after a request starts, before a response is fully streamed, under certain timing conditions. This is a genuine, unresolved transport-level concern worth watching for in real usage, not something Mnemolis's own code can currently work around.

## Connecting a client

**Claude Desktop** — add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mnemolis": {
      "url": "http://your-host-ip:8888/mcp"
    }
  }
}
```

**Any other MCP client** — point it at the same Streamable HTTP endpoint, `http://your-host-ip:8888/mcp`.

## Why one tool instead of several

It would be possible to expose `search_kiwix`, `search_weather`, `search_news`, and so on as separate MCP tools, letting the calling model pick directly. Mnemolis deliberately doesn't do this — the entire point of [Routing](Routing) is that Mnemolis itself is better positioned to decide which source(s) actually apply than a general-purpose model guessing from tool names alone, especially once [Query Decomposition](Query-Decomposition) and [Fusion](Fusion) are involved. A single `search` tool keeps that decision where it belongs.

This is also exactly the reasoning behind the [Open WebUI System Prompt Guide](Open-WebUI-System-Prompt-Guide) — the real, observed failure mode for tool-calling models isn't picking the wrong tool, it's *pre-splitting* a compound question into multiple narrow calls (or just answering part of it) before Mnemolis's own decomposition logic ever gets a chance to run on the full, original question.
