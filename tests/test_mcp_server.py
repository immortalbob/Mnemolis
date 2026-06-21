"""
Tests for app/mcp_server.py — MCP tool server exposing Mnemolis via SSE.

Tests the tool schema definition and call dispatch logic directly,
since the SSE transport itself requires a real ASGI connection to test
meaningfully and is effectively a thin wrapper around the MCP SDK.
"""
import pytest
import asyncio
from unittest.mock import patch


class TestListTools:
    """Tests for list_tools() — the MCP tool schema definition."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_one_tool(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        assert len(tools) == 1

    def test_tool_is_named_search(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        assert tools[0].name == "search"

    def test_tool_has_description(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        assert len(tools[0].description) > 0

    def test_input_schema_requires_query(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        schema = tools[0].inputSchema
        assert "query" in schema["required"]

    def test_input_schema_has_query_property(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        schema = tools[0].inputSchema
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"

    def test_input_schema_source_has_enum_with_all_sources(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        schema = tools[0].inputSchema
        source_enum = schema["properties"]["source"]["enum"]
        for expected in ["auto", "kiwix", "forecast", "news", "web", "uptime", "ha", "fusion"]:
            assert expected in source_enum

    def test_input_schema_source_defaults_to_auto(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        schema = tools[0].inputSchema
        assert schema["properties"]["source"]["default"] == "auto"

    def test_input_schema_has_fusion_sources_array(self):
        from app.mcp_server import list_tools
        tools = self._run(list_tools())
        schema = tools[0].inputSchema
        assert schema["properties"]["fusion_sources"]["type"] == "array"


class TestCallTool:
    """Tests for call_tool() — MCP tool call dispatch."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_unknown_tool_raises_value_error(self):
        from app.mcp_server import call_tool
        with pytest.raises(ValueError):
            self._run(call_tool("not_search", {"query": "test"}))

    def test_missing_query_returns_error_text(self):
        from app.mcp_server import call_tool
        result = self._run(call_tool("search", {}))
        assert len(result) == 1
        assert "Error" in result[0].text
        assert "required" in result[0].text.lower()

    def test_empty_query_string_returns_error(self):
        from app.mcp_server import call_tool
        result = self._run(call_tool("search", {"query": ""}))
        assert "Error" in result[0].text

    def test_successful_call_returns_route_result(self):
        from app.mcp_server import call_tool
        with patch("app.mcp_server.route", return_value="Nitrogen is a chemical element."):
            result = self._run(call_tool("search", {"query": "what is nitrogen"}))
        assert result[0].text == "Nitrogen is a chemical element."

    def test_default_source_is_auto(self):
        from app.mcp_server import call_tool
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(call_tool("search", {"query": "test"}))
        # route(query, source, fusion_sources) — source defaults to "auto"
        call_args = mock_route.call_args.args
        assert call_args[1] == "auto"

    def test_explicit_source_passed_through(self):
        from app.mcp_server import call_tool
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(call_tool("search", {"query": "test", "source": "kiwix"}))
        call_args = mock_route.call_args.args
        assert call_args[1] == "kiwix"

    def test_fusion_sources_passed_through(self):
        from app.mcp_server import call_tool
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(call_tool("search", {
                "query": "test",
                "source": "fusion",
                "fusion_sources": ["forecast", "uptime"]
            }))
        call_args = mock_route.call_args.args
        assert call_args[2] == ["forecast", "uptime"]

    def test_fusion_sources_defaults_to_none(self):
        from app.mcp_server import call_tool
        with patch("app.mcp_server.route", return_value="result") as mock_route:
            self._run(call_tool("search", {"query": "test"}))
        call_args = mock_route.call_args.args
        assert call_args[2] is None

    def test_exception_in_route_returns_error_text(self):
        from app.mcp_server import call_tool
        with patch("app.mcp_server.route", side_effect=Exception("boom")):
            result = self._run(call_tool("search", {"query": "test"}))
        assert "Error" in result[0].text
        assert "boom" in result[0].text

    def test_result_is_text_content_type(self):
        from app.mcp_server import call_tool
        with patch("app.mcp_server.route", return_value="result"):
            result = self._run(call_tool("search", {"query": "test"}))
        assert result[0].type == "text"


class TestCreateSseApp:
    """Tests for create_sse_app() — Starlette app construction."""

    def test_creates_starlette_app(self):
        from app.mcp_server import create_sse_app
        from starlette.applications import Starlette
        app = create_sse_app()
        assert isinstance(app, Starlette)

    def test_has_sse_route(self):
        from app.mcp_server import create_sse_app
        app = create_sse_app()
        paths = [r.path for r in app.routes]
        assert "/sse" in paths

    def test_has_messages_route(self):
        from app.mcp_server import create_sse_app
        app = create_sse_app()
        paths = [r.path for r in app.routes]
        assert "/messages/" in paths


class TestMcpAppModuleLevel:
    """Tests confirming the module-level mcp_app instance is valid."""

    def test_mcp_app_exists(self):
        from app.mcp_server import mcp_app
        assert mcp_app is not None

    def test_mcp_app_is_starlette_instance(self):
        from app.mcp_server import mcp_app
        from starlette.applications import Starlette
        assert isinstance(mcp_app, Starlette)

    def test_server_name_is_mnemolis(self):
        from app.mcp_server import server
        assert server.name == "mnemolis"
