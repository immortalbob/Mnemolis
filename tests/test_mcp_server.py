"""
Tests for app/mcp_server.py — MCP tool server exposing Mnemolis via
Streamable HTTP transport (migrated from SSE — see CHANGELOG and the
module's own docstring for the full reasoning).

Tests the tool schema definition and call dispatch logic directly using
FastMCP's own list_tools()/call_tool() methods, since the transport layer
itself requires a real ASGI connection to test meaningfully and is a thin
wrapper around the MCP SDK at that point.
"""
import pytest
import asyncio
from unittest.mock import patch


class TestListTools:
    """Tests for the registered 'search' tool's schema definition."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_one_tool(self):
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        assert len(tools) == 1

    def test_tool_is_named_search(self):
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        assert tools[0].name == "search"

    def test_tool_has_description(self):
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        assert len(tools[0].description) > 0

    def test_input_schema_requires_query(self):
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        schema = tools[0].inputSchema
        assert "query" in schema["required"]

    def test_input_schema_has_query_property(self):
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        schema = tools[0].inputSchema
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"

    def test_input_schema_source_is_plain_string_not_enum(self):
        """Regression test for a deliberate design decision made during
        the SSE -> Streamable HTTP migration, not an oversight: 'source'
        is a plain string, NOT an Enum/Literal-backed schema with an
        `enum` constraint. FastMCP's @mcp.tool() decorator currently has
        no supported way to register a fully custom inputSchema (open
        upstream SDK issue), so the schema is inferred from type hints —
        and an Enum/Literal here would generate a $ref/$defs-based
        schema with a real, separate open compatibility bug affecting at
        least one real MCP client. A plain string with the valid values
        documented in the docstring sidesteps that specific bug class.
        The real, honest cost: invalid source values are no longer
        rejected at the schema level, only by Mnemolis's own routing
        logic — this test exists to make sure that tradeoff is never
        silently reversed without it being a deliberate decision again."""
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        schema = tools[0].inputSchema
        assert schema["properties"]["source"]["type"] == "string"
        assert "enum" not in schema["properties"]["source"]
        assert "$ref" not in str(schema)
        assert "$defs" not in schema

    def test_input_schema_source_defaults_to_auto(self):
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        schema = tools[0].inputSchema
        assert schema["properties"]["source"]["default"] == "auto"

    def test_docstring_documents_all_valid_source_values(self):
        """Since the schema no longer enforces valid source values via
        an enum constraint, the docstring is the only place this
        contract is documented at all — confirm every real source name
        Mnemolis actually supports is mentioned."""
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        description = tools[0].description.lower()
        for expected in ["auto", "kiwix", "forecast", "news", "web", "uptime", "ha", "fusion"]:
            assert expected in description

    def test_input_schema_has_fusion_sources_array(self):
        from app.mcp_server import mcp
        tools = self._run(mcp.list_tools())
        schema = tools[0].inputSchema
        fusion_schema = schema["properties"]["fusion_sources"]
        # anyOf[array, null] since it's Optional — not a bare "array" type
        type_options = [opt.get("type") for opt in fusion_schema.get("anyOf", [])]
        assert "array" in type_options


class TestCallTool:
    """Tests for the 'search' tool's actual call dispatch behavior."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_unknown_tool_raises(self):
        from app.mcp_server import mcp
        with pytest.raises(Exception):
            self._run(mcp.call_tool("not_search", {"query": "test"}))

    def test_missing_query_raises_validation_error(self):
        """Unlike the old manual implementation, a missing required
        query is now rejected automatically by Pydantic validation
        before the tool function body ever runs — no manual
        `if not query` check needed."""
        from app.mcp_server import mcp
        with pytest.raises(Exception):
            self._run(mcp.call_tool("search", {}))

    def test_successful_call_returns_route_result(self):
        from app.mcp_server import mcp
        with patch("app.mcp_server.route", return_value="Nitrogen is a chemical element."):
            content, _structured = self._run(mcp.call_tool("search", {"query": "what is nitrogen"}))
        assert content[0].text == "Nitrogen is a chemical element."

    def test_default_source_is_auto(self):
        from app.mcp_server import mcp
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(mcp.call_tool("search", {"query": "test"}))
        call_args = mock_route.call_args.args
        assert call_args[1] == "auto"

    def test_explicit_source_passed_through(self):
        from app.mcp_server import mcp
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(mcp.call_tool("search", {"query": "test", "source": "kiwix"}))
        call_args = mock_route.call_args.args
        assert call_args[1] == "kiwix"

    def test_fusion_sources_passed_through(self):
        from app.mcp_server import mcp
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(mcp.call_tool("search", {
                "query": "test",
                "source": "fusion",
                "fusion_sources": ["forecast", "uptime"]
            }))
        call_args = mock_route.call_args.args
        assert call_args[2] == ["forecast", "uptime"]

    def test_fusion_sources_defaults_to_none(self):
        from app.mcp_server import mcp
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(mcp.call_tool("search", {"query": "test"}))
        call_args = mock_route.call_args.args
        assert call_args[2] is None

    def test_exception_in_route_returns_error_text(self):
        """The tool function itself catches exceptions from route() and
        returns an error string rather than letting the exception
        propagate — preserved behavior from the original implementation."""
        from app.mcp_server import mcp
        with patch("app.mcp_server.route", side_effect=Exception("boom")):
            content, _structured = self._run(mcp.call_tool("search", {"query": "test"}))
        assert "Error" in content[0].text
        assert "boom" in content[0].text

    def test_result_is_text_content_type(self):
        from app.mcp_server import mcp
        with patch("app.mcp_server.route", return_value="result"):
            content, _structured = self._run(mcp.call_tool("search", {"query": "test"}))
        assert content[0].type == "text"


class TestGetMcpApp:
    """Tests for get_mcp_app() — the function that rebuilds the
    Streamable HTTP ASGI app with a fresh session manager each call.

    This exists because of a real, currently-open issue across the
    broader MCP/FastMCP ecosystem (not specific to Mnemolis):
    FastMCP.streamable_http_app() lazily creates and caches ONE session
    manager on the FastMCP instance, but StreamableHTTPSessionManager can
    only be .run() once per instance, ever. A module-level mcp_app built
    once at import time meant every independent app lifecycle (every
    `with TestClient(app) as client:` block, every container restart)
    tried to reuse the same already-exhausted session manager, raising a
    hard RuntimeError on the second attempt. get_mcp_app() resets the
    cached session manager before rebuilding, so each call gets a
    genuinely independent one."""

    def test_returns_a_starlette_app(self):
        from app.mcp_server import get_mcp_app
        from starlette.applications import Starlette
        app = get_mcp_app()
        assert isinstance(app, Starlette)

    def test_repeated_calls_produce_genuinely_independent_session_managers(self):
        """The actual regression this function exists to fix — confirms
        the underlying session manager object is different (not the
        same cached instance) across repeated calls."""
        from app.mcp_server import get_mcp_app, mcp
        get_mcp_app()
        first_manager = mcp._session_manager
        get_mcp_app()
        second_manager = mcp._session_manager
        assert first_manager is not second_manager

    def test_full_lifespan_can_be_entered_and_exited_multiple_times(self):
        """The real, end-to-end regression test — this exact scenario
        (entering and exiting the app's lifespan multiple times) is what
        raised RuntimeError before this fix existed, and is exactly what
        happens once per `with TestClient(app) as client:` block across
        this test suite's many separate test files."""
        from app.mcp_server import get_mcp_app

        async def run_three_lifecycles():
            for _ in range(3):
                app = get_mcp_app()
                async with app.router.lifespan_context(app):
                    pass

        asyncio.run(run_three_lifecycles())  # must not raise


class TestMcpAppModuleLevel:
    """Tests confirming the module-level mcp and mcp_app objects are valid."""

    def test_mcp_app_exists(self):
        from app.mcp_server import mcp_app
        assert mcp_app is not None

    def test_mcp_app_is_starlette_instance(self):
        from app.mcp_server import mcp_app
        from starlette.applications import Starlette
        assert isinstance(mcp_app, Starlette)

    def test_server_name_is_mnemolis(self):
        from app.mcp_server import mcp
        assert mcp.name == "mnemolis"
