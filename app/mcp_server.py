"""
MiniSearch MCP Server
Exposes MiniSearch as an MCP tool server over stdio or SSE transport.
Run alongside the FastAPI server to make MiniSearch available to any MCP client.
"""

import asyncio
import logging
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request

from app.router import route, SOURCE_MAP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MCP server instance
server = Server("minisearch")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MiniSearch tools."""
    return [
        Tool(
            name="search",
            description=(
                "Search across local and remote knowledge sources via MiniSearch. "
                "Automatically selects the best source based on the query. "
                "Sources: 'auto' (default), 'kiwix' (offline knowledge — Wikipedia, "
                "Stack Exchange, iFixit, FreeCodeCamp, DevDocs), 'forecast' (3-day "
                "weather forecast), 'news' (recent RSS articles), 'web' (live web search)."
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
                        "enum": ["auto", "kiwix", "forecast", "news", "web"],
                        "default": "auto",
                        "description": "The source to query. Use 'auto' to let MiniSearch decide."
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

    if not query:
        return [TextContent(type="text", text="Error: query is required.")]

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, route, query, source
        )
        return [TextContent(type="text", text=result)]
    except Exception as e:
        logger.error(f"MiniSearch MCP error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


def create_sse_app() -> Starlette:
    """Create a Starlette app with SSE transport for MCP."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
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


# Export for use in main.py
mcp_app = create_sse_app()
