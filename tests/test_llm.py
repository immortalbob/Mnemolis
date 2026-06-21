"""
Tests for app/llm.py — LLM client supporting Ollama native and OpenAI-compatible APIs.

This module backs every routing decision in the system (source selection,
Kiwix book selection, fusion source selection), so correctness here matters
disproportionately relative to its small size.
"""
from unittest.mock import patch, MagicMock
import requests as req


class TestIsConfigured:
    """Tests for is_configured()."""

    def setup_method(self):
        from app.config import settings
        self._original_url = settings.llm_url
        self._original_model = settings.llm_model

    def teardown_method(self):
        from app.config import settings
        settings.llm_url = self._original_url
        settings.llm_model = self._original_model

    def test_false_when_url_blank(self):
        from app.llm import is_configured
        from app.config import settings
        settings.llm_url = ""
        settings.llm_model = "qwen3:8b"
        assert is_configured() is False

    def test_false_when_model_blank(self):
        from app.llm import is_configured
        from app.config import settings
        settings.llm_url = "http://ollama:11434"
        settings.llm_model = ""
        assert is_configured() is False

    def test_false_when_both_blank(self):
        from app.llm import is_configured
        from app.config import settings
        settings.llm_url = ""
        settings.llm_model = ""
        assert is_configured() is False

    def test_true_when_both_set(self):
        from app.llm import is_configured
        from app.config import settings
        settings.llm_url = "http://ollama:11434"
        settings.llm_model = "qwen3:8b"
        assert is_configured() is True


class TestCompleteNotConfigured:
    """Tests for complete() when no LLM backend is configured."""

    def test_returns_none_when_not_configured(self):
        from app.llm import complete
        from app.config import settings
        original_url = settings.llm_url
        settings.llm_url = ""
        result = complete("test prompt")
        assert result is None
        settings.llm_url = original_url


class TestCompleteOllama:
    """Tests for complete() using the Ollama native API path."""

    def setup_method(self):
        from app.config import settings
        self._orig_url = settings.llm_url
        self._orig_model = settings.llm_model
        self._orig_type = settings.llm_api_type
        settings.llm_url = "http://ollama:11434"
        settings.llm_model = "qwen3:8b"
        settings.llm_api_type = "ollama"

    def teardown_method(self):
        from app.config import settings
        settings.llm_url = self._orig_url
        settings.llm_model = self._orig_model
        settings.llm_api_type = self._orig_type

    def _mock_response(self, json_data, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def test_returns_response_text(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "kiwix"})
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("which source for: what is nitrogen")
        assert result == "kiwix"

    def test_strips_trailing_period(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "forecast."})
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result == "forecast"

    def test_strips_whitespace(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "  uptime  "})
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result == "uptime"

    def test_falls_back_to_thinking_field_when_response_empty(self):
        """Thinking models (qwen3 etc) sometimes return empty 'response' with
        the actual answer buried in 'thinking'. Should fall back to last line."""
        from app.llm import complete
        mock_resp = self._mock_response({
            "response": "",
            "thinking": "Let me think about this.\nThe query is about weather.\nforecast"
        })
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result == "forecast"

    def test_returns_none_when_response_and_thinking_both_empty(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "", "thinking": ""})
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_returns_none_on_connection_error(self):
        from app.llm import complete
        with patch("app.llm.requests.post", side_effect=req.exceptions.ConnectionError("refused")):
            result = complete("test")
        assert result is None

    def test_returns_none_on_timeout(self):
        from app.llm import complete
        with patch("app.llm.requests.post", side_effect=req.exceptions.Timeout("timed out")):
            result = complete("test")
        assert result is None

    def test_returns_none_on_http_error(self):
        from app.llm import complete
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError("500 error")
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_sends_correct_payload_structure(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "kiwix"})
        with patch("app.llm.requests.post", return_value=mock_resp) as mock_post:
            complete("test prompt", max_tokens=50, temperature=0.2)
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["model"] == "qwen3:8b"
        assert call_kwargs["json"]["prompt"] == "test prompt"
        assert call_kwargs["json"]["options"]["temperature"] == 0.2
        assert call_kwargs["json"]["options"]["num_predict"] == 50

    def test_hits_ollama_native_endpoint(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "kiwix"})
        with patch("app.llm.requests.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/api/generate" in call_args.args[0]


class TestCompleteOpenAI:
    """Tests for complete() using the OpenAI-compatible API path."""

    def setup_method(self):
        from app.config import settings
        self._orig_url = settings.llm_url
        self._orig_model = settings.llm_model
        self._orig_type = settings.llm_api_type
        settings.llm_url = "http://llama-server:8080"
        settings.llm_model = "qwen3-coder-30b"
        settings.llm_api_type = "openai"

    def teardown_method(self):
        from app.config import settings
        settings.llm_url = self._orig_url
        settings.llm_model = self._orig_model
        settings.llm_api_type = self._orig_type

    def _mock_response(self, json_data, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def test_returns_message_content(self):
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {"content": "kiwix"}}]
        })
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result == "kiwix"

    def test_strips_trailing_period(self):
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {"content": "forecast."}}]
        })
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result == "forecast"

    def test_returns_none_when_no_choices(self):
        from app.llm import complete
        mock_resp = self._mock_response({"choices": []})
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_returns_none_when_content_empty(self):
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {"content": ""}}]
        })
        with patch("app.llm.requests.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_returns_none_on_connection_error(self):
        from app.llm import complete
        with patch("app.llm.requests.post", side_effect=req.exceptions.ConnectionError()):
            result = complete("test")
        assert result is None

    def test_hits_openai_compatible_endpoint(self):
        from app.llm import complete
        mock_resp = self._mock_response({"choices": [{"message": {"content": "kiwix"}}]})
        with patch("app.llm.requests.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/v1/chat/completions" in call_args.args[0]

    def test_sends_correct_payload_structure(self):
        from app.llm import complete
        mock_resp = self._mock_response({"choices": [{"message": {"content": "kiwix"}}]})
        with patch("app.llm.requests.post", return_value=mock_resp) as mock_post:
            complete("test prompt", max_tokens=75, temperature=0.5)
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["model"] == "qwen3-coder-30b"
        assert call_kwargs["json"]["messages"] == [{"role": "user", "content": "test prompt"}]
        assert call_kwargs["json"]["max_tokens"] == 75
        assert call_kwargs["json"]["temperature"] == 0.5

    def test_api_type_case_insensitive(self):
        from app.llm import complete
        from app.config import settings
        settings.llm_api_type = "OpenAI"
        mock_resp = self._mock_response({"choices": [{"message": {"content": "kiwix"}}]})
        with patch("app.llm.requests.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/v1/chat/completions" in call_args.args[0]


class TestCompleteDefaultsToOllama:
    """Tests confirming unknown/unset api_type falls back to Ollama path."""

    def setup_method(self):
        from app.config import settings
        self._orig_url = settings.llm_url
        self._orig_model = settings.llm_model
        self._orig_type = settings.llm_api_type
        settings.llm_url = "http://ollama:11434"
        settings.llm_model = "qwen3:8b"

    def teardown_method(self):
        from app.config import settings
        settings.llm_url = self._orig_url
        settings.llm_model = self._orig_model
        settings.llm_api_type = self._orig_type

    def test_unknown_api_type_defaults_to_ollama(self):
        from app.llm import complete
        from app.config import settings
        settings.llm_api_type = "something_unrecognized"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "kiwix"}
        mock_resp.raise_for_status.return_value = None
        with patch("app.llm.requests.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/api/generate" in call_args.args[0]
