# The MCP Transport Migration

Mnemolis's MCP server didn't always run on Streamable HTTP. This is the story of why it moved, the bug the migration itself surfaced before it ever shipped, and a second, separate round of bugs that only a real client connecting over a real network could catch.

## Where this started: an external community audit

The migration wasn't an internally-initiated cleanup — it started from a real, external MCP audit tool, run by someone outside the project and opened as a GitHub issue against the repo. The audit flagged the old SSE transport's use of `request._send`, a private Starlette attribute.

Initial research confirmed that specific pattern genuinely matched the official MCP Python SDK's own low-level reference examples — it wasn't a Mnemolis-specific shortcut, and the audit's literal complaint wasn't quite right on its own. But digging into *why* the SDK's own examples still used a private attribute led to two more substantive findings that the narrow audit complaint hadn't directly raised: an official, higher-level integration pattern already existed (`FastMCP.streamable_http_app()`) that avoids touching private Starlette internals in application code at all, and SSE transport itself is explicitly being superseded — official FastMCP documentation states it "exists only for backward compatibility and shouldn't be used in new projects." Taking the external report seriously enough to investigate properly, rather than dismissing it on the technicality that the specific complaint didn't fully hold up, is what turned a narrow audit finding into a full transport migration.

## Why the migration happened

Two independent, real reasons drove the full migration from **SSE (Server-Sent Events)** — with the tool's schema hand-written as a raw JSON Schema dict and the low-level `mcp.server.Server` class — to **Streamable HTTP**, mounted at `/mcp` on the main FastAPI app as a sub-application:

1. **SSE is being superseded across the MCP ecosystem**, per FastMCP's own documentation, as above.
2. **The old SSE handler's private-attribute access**, while a real pattern matching the official SDK's own examples, was exactly the kind of thing `FastMCP`'s high-level API exists to avoid needing at all.

This was also a breaking change for any already-connected MCP client — the endpoint itself moved, from `/mcp/sse` to `/mcp`.

## A real, currently-open ecosystem bug found during the migration itself

Before this migration ever shipped, research into the new transport surfaced a genuine, separate bug — not in Mnemolis's design, but in how `FastMCP.streamable_http_app()` itself behaves: it lazily creates **one** `StreamableHTTPSessionManager` and caches it on the `FastMCP` instance. Calling `streamable_http_app()` again still returns the *same* cached session manager wrapped in a new app object — but `StreamableHTTPSessionManager.run()` can only ever be entered **once** per instance. A module-level `mcp_app` built once at import time meant every independent app lifecycle (every container restart; every test file's own `TestClient` instance) tried to re-run the same already-exhausted session manager, raising a hard `RuntimeError` on the second attempt.

This is real and currently affects the broader ecosystem, not just Mnemolis — multiple independent reports describe the identical error, both in test suites and in real production deployments under certain conditions (concurrent startup, serverless cold starts).

### The first fix attempt, and why it was incomplete

The fix, `get_mcp_app()` in `mcp_server.py`, resets the FastMCP instance's cached session manager reference before rebuilding the app — but the first version of this fix was itself genuinely incomplete, and worth understanding why. Resetting the cached reference and building a fresh app object works fine *in isolation*, but Mnemolis's actual `/mcp` route is mounted **once**, at module-import time — the already-mounted route still held a reference to the *original* app object's request handler and lifespan closure, regardless of what the module-level `mcp_app` variable was reassigned to afterward.

### The complete, correct fix

The complete fix rebuilds the app fresh **and** finds the actual `Mount` route object in `main.py`'s router and reassigns its `.app` attribute directly, so the object whose lifespan gets entered during startup is genuinely the same object serving real requests during that same lifecycle — confirmed by directly tracing through three consecutive simulated app lifecycles before and after each version of the fix.

A separate, more serious concern was found during the same research but is **not** something this fix touches: an open upstream issue describes a race condition where the session manager can report "shutting down" immediately after a request starts, before a response is fully streamed, under certain timing conditions. This is a genuine, unresolved transport-level concern worth watching for in real usage, not something Mnemolis's own code can currently work around.

## A second, separate round of bugs — found immediately after, by an actual client

Everything above had only ever been verified through the test suite and direct code tracing — no real MCP client had ever actually connected to the new endpoint yet. The very first real connection attempt, made right after the migration shipped, using MCP Inspector, surfaced two more genuine bugs that neither the test suite nor careful code reading had caught, because both live specifically in the gap between "the code is correct" and "a real network client can actually reach it."

**The only reachable path was `/mcp/mcp`, not the documented `/mcp`.** FastMCP's own internal Streamable HTTP route defaults to mounting at `/mcp` *inside* its own app — and Mnemolis separately mounts that whole app at `/mcp` again in `main.py`. Combined, the two `/mcp`s stacked, and the actual, only-reachable URL was `http://host:8888/mcp/mcp`. `TestClient`-based tests never caught this because they call the app object directly by Python reference and never construct or resolve a real URL path at all — there's no path-doubling for an in-process function call to expose. Fixed by setting `streamable_http_path="/"` on the `FastMCP` instance itself, so `main.py`'s own `/mcp` mount is the only `/mcp` in the final, effective path.

**Real LAN connections were rejected outright with "Invalid Host header."** FastMCP auto-enables DNS-rebinding protection whenever its `host` constructor parameter is left at the default `127.0.0.1` — which only accepts `Host` header values of `127.0.0.1`/`localhost`/`::1`, rejecting every request addressed to Mnemolis's actual LAN IP or hostname. Since Mnemolis is explicitly designed to be reached over a real home network — the entire point of running it as a homelab service — this meant no real-network MCP connection could succeed at all, regardless of the path fix above. `TestClient`'s default `testserver` host never exercises real Host-header validation, so this was just as invisible to the test suite as the path bug. Fixed via `TransportSecuritySettings(enable_dns_rebinding_protection=False)`, matching the trust model already established for the REST API's own optional auth.

A tempting alternative for the path bug — mounting the MCP app at root (`/`) instead of fixing `streamable_http_path` — was considered and tested, then rejected after confirming directly it would shadow every REST route registered after it in `main.py` (`/health`, `/search`, and the rest are all defined after the MCP mount), since a root `Mount` matches any path prefix regardless of registration order. The chosen fix avoids this entirely by keeping the mount at `/mcp` and changing FastMCP's own internal route instead.

**The lesson:** this migration needed two completely different kinds of scrutiny before it could be trusted, and neither would have caught the other's bugs. The session-manager bug was found by research and careful internal tracing, before the migration ever shipped — a problem with the *design*, findable by reading and reasoning about the code. The path and Host-header bugs were found only once a real client crossed a real network boundary — a problem with the *deployment*, findable only by something outside the process actually trying to connect. A migration this size, touching both an internal lifecycle and an external network contract, genuinely needed both kinds of verification, not just the more thorough one.
