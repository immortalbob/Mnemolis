# MCP Server

Mnemolis exposes everything through two interfaces at once: a REST API and an [MCP](https://modelcontextprotocol.io/) server, both backed by the exact same routing/decomposition/fusion logic underneath. The REST API is what Home Assistant and most automation scripts use; MCP is what lets Claude Desktop, Cursor, or any other MCP-aware client query Mnemolis directly as a tool.

## What's actually exposed

The MCP server exposes exactly **one tool**, called `search`:

```json
{
  "name": "search",
  "properties": {
    "query": "the search query or question (required)",
    "source": "auto | kiwix | forecast | news | web | uptime | ha | fusion (default: auto)",
    "fusion_sources": "optional array of source names, only used when source='fusion'"
  }
}
```

This mirrors `POST /search`'s own request shape almost exactly — `query`, `source`, `fusion_sources` mean the same thing in both places, and the same [Routing](Routing), [Query Decomposition](Query-Decomposition), and [Conditional Query Detection](Conditional-Query-Detection) logic runs underneath regardless of which interface the call came through.

## A real difference worth knowing about

MCP's `call_tool` handler calls `route()`, the simpler, backward-compatible wrapper that returns just the result string — not `route_with_source()`, which is what the REST API's `/search` endpoint uses to also report `source_used` (including whether a [fallback](Routing#fallback-when-a-source-comes-back-empty) occurred). This means an MCP client gets the plain answer text, the same content a REST caller would get, but **never** learns which source actually answered, or whether a fallback happened along the way. If you need that metadata, you need the REST API — MCP's contract is intentionally just "ask a question, get an answer," with no provenance attached.

## How it's actually transported

MCP here runs over Server-Sent Events (SSE), not stdio — this is what makes it reachable over your network rather than only usable as a local subprocess. Two routes exist under `/mcp`:

- `GET /mcp/sse` — the actual SSE stream a client connects to
- `POST /mcp/messages/` — where the client posts messages back

Both are mounted onto the main FastAPI app as a sub-application, so they share the same container, port, and network exposure as the REST API — there's no separate process or port to manage.

## Connecting a client

**Claude Desktop** — add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mnemolis": {
      "url": "http://your-host-ip:8888/mcp/sse"
    }
  }
}
```

**Any other MCP client** — point it at the same SSE endpoint, `http://your-host-ip:8888/mcp/sse`. Any client that speaks MCP over SSE should work without any Mnemolis-specific configuration beyond that URL.

## Why one tool instead of several

It would be possible to expose `search_kiwix`, `search_weather`, `search_news`, and so on as separate MCP tools, letting the calling model pick directly. Mnemolis deliberately doesn't do this — the entire point of [Routing](Routing) is that Mnemolis itself is better positioned to decide which source(s) actually apply than a general-purpose model guessing from tool names alone, especially once [Query Decomposition](Query-Decomposition) and [Fusion](Fusion) are involved. A single `search` tool keeps that decision where it belongs.

This is also exactly the reasoning behind the [Open WebUI System Prompt Guide](Open-WebUI-System-Prompt-Guide) — the real, observed failure mode for tool-calling models isn't picking the wrong tool, it's *pre-splitting* a compound question into multiple narrow calls (or just answering part of it) before Mnemolis's own decomposition logic ever gets a chance to run on the full, original question.
