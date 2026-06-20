"""
Mnemolis MCP Server
Exposes Mnemolis as an MCP tool server via SSE transport.
Mounted at /mcp by the FastAPI app in main.py.
"""

import logging
import asyncio
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request

from app.router import route, SOURCE_MAP

_LOGGER = logging.getLogger(__name__)

server = Server("mnemolis")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Mnemolis tools."""
    return [
        Tool(
            name="search",
            description=(
                "Search across local and remote knowledge sources via Mnemolis. "
                "Automatically selects the best source based on the query. "
                "Sources: 'auto' (default), 'kiwix' (offline knowledge), 'forecast' (weather), "
                "'news' (RSS articles), 'web' (live search), 'uptime' (service status), "
                "'fusion' (query multiple sources concurrently — LLM picks best 2-3 sources, "
                "or specify with fusion_sources)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query or question"
                    },
                    "source": {
                        "type": "string",
                        "enum": ["auto", "kiwix", "forecast", "news", "web", "uptime", "ha", "fusion"],
                        "default": "auto",
                        "description": "The source to query. Use 'auto' to let Mnemolis decide."
                    },
                    "fusion_sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of sources to fuse. Only used when source='fusion'. If omitted, LLM picks the best 2-3 sources."
                    }
                },
                "required": ["query"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls from MCP clients."""
    if name != "search":
        raise ValueError(f"Unknown tool: {name}")

    query = arguments.get("query", "")
    source = arguments.get("source", "auto")
    fusion_sources = arguments.get("fusion_sources", None)

    if not query:
        return [TextContent(type="text", text="Error: query is required.")]

    try:
        result = await asyncio.to_thread(route, query, source, fusion_sources)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        _LOGGER.error("Mnemolis MCP error: %s", e)
        return [TextContent(type="text", text=f"Error: {e}")]


def create_sse_app() -> Starlette:
    """Create a Starlette app with SSE transport for MCP."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send  # _send is private but required by SseServerTransport
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=sse.handle_post_message, methods=["POST"]),
        ]
    )


mcp_app = create_sse_app()
