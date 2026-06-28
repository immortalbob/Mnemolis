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
        self._orig_keep_alive = settings.llm_keep_alive
        settings.llm_url = "http://ollama:11434"
        settings.llm_model = "qwen3:8b"
        settings.llm_api_type = "ollama"

    def teardown_method(self):
        from app.config import settings
        settings.llm_url = self._orig_url
        settings.llm_model = self._orig_model
        settings.llm_api_type = self._orig_type
        settings.llm_keep_alive = self._orig_keep_alive

    def _mock_response(self, json_data, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def test_returns_response_text(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "kiwix"})
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("which source for: what is nitrogen")
        assert result == "kiwix"

    def test_strips_trailing_period(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "forecast."})
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result == "forecast"

    def test_strips_whitespace(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "  uptime  "})
        with patch("app.llm._session.post", return_value=mock_resp):
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
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result == "forecast"

    def test_returns_none_when_response_and_thinking_both_empty(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "", "thinking": ""})
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_returns_none_on_connection_error(self):
        from app.llm import complete
        with patch("app.llm._session.post", side_effect=req.exceptions.ConnectionError("refused")):
            result = complete("test")
        assert result is None

    def test_returns_none_on_timeout(self):
        from app.llm import complete
        with patch("app.llm._session.post", side_effect=req.exceptions.Timeout("timed out")):
            result = complete("test")
        assert result is None

    def test_returns_none_on_http_error(self):
        from app.llm import complete
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError("500 error")
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_sends_correct_payload_structure(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "kiwix"})
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
            complete("test prompt", max_tokens=50, temperature=0.2)
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["model"] == "qwen3:8b"
        assert call_kwargs["json"]["prompt"] == "test prompt"
        assert call_kwargs["json"]["options"]["temperature"] == 0.2
        assert call_kwargs["json"]["options"]["num_predict"] == 50

    def test_hits_ollama_native_endpoint(self):
        from app.llm import complete
        mock_resp = self._mock_response({"response": "kiwix"})
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/api/generate" in call_args.args[0]

    def test_sends_keep_alive_from_settings(self):
        """The actual mechanism this fix changes: keep_alive must be
        read fresh from settings.llm_keep_alive on every call, not a
        hardcoded value baked in once — confirmed by changing the
        setting between two calls and checking both payloads reflect
        their own call-time value."""
        from app.config import settings
        from app.llm import complete

        mock_resp = self._mock_response({"response": "kiwix"})
        settings.llm_keep_alive = "30m"
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
            complete("test")
        assert mock_post.call_args.kwargs["json"]["keep_alive"] == "30m"

        settings.llm_keep_alive = "-1"
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
            complete("test")
        assert mock_post.call_args.kwargs["json"]["keep_alive"] == "-1"

    def test_keep_alive_default_matches_ollamas_own_default(self):
        """Deliberately left at Ollama's own server-side default ("5m"),
        not pinned to infinite — see settings.llm_keep_alive's own
        comment in app/config.py for why an indefinite default was
        rejected. Confirms that choice rather than assuming it."""
        from app.config import settings
        assert settings.llm_keep_alive == "5m"

    def test_accepts_every_documented_ollama_keep_alive_format(self):
        """Ollama's own FAQ documents four valid shapes for this field:
        a duration string, a plain number-of-seconds string, "-1" for
        never-unload, and "0" for unload-immediately. Confirms each
        passes through to the payload unmodified — this project doesn't
        reinterpret or validate the value beyond what pydantic-settings'
        plain str type already provides, by design, so any future
        Ollama-documented format keeps working without a code change
        here."""
        from app.config import settings
        from app.llm import complete

        mock_resp = self._mock_response({"response": "kiwix"})
        for value in ["30m", "3h", "3600", "-1", "0"]:
            settings.llm_keep_alive = value
            with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
                complete("test")
            assert mock_post.call_args.kwargs["json"]["keep_alive"] == value


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
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result == "kiwix"

    def test_strips_trailing_period(self):
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {"content": "forecast."}}]
        })
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result == "forecast"

    def test_returns_none_when_no_choices(self):
        from app.llm import complete
        mock_resp = self._mock_response({"choices": []})
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_returns_none_when_content_empty(self):
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {"content": ""}}]
        })
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_falls_back_to_reasoning_content_when_content_empty(self):
        """Regression test for a real, significant bug found via a
        deliberate "bulletproofing" pass, confirmed against multiple
        independent real-world bug reports of this exact failure mode:
        thinking models served via an OpenAI-compatible endpoint (the
        actual real backend this project uses — llama-server with
        Qwen3-Coder-30B) routinely return an EMPTY content field with
        all real output sitting in a separate reasoning_content field
        instead. llama.cpp's server defaults to exactly this "deepseek"
        reasoning_format convention. Without this fallback, a thinking
        model on this code path would silently return None for every
        single completion — mirrors the exact fallback _complete_ollama
        already has for Ollama's own "thinking" field."""
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {
                "content": "",
                "reasoning_content": "Let me think about this.\nThe query is about weather.\nforecast"
            }}]
        })
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result == "forecast"

    def test_falls_back_to_reasoning_field_variant(self):
        """Some OpenAI-compatible servers use 'reasoning' instead of
        'reasoning_content' for the same concept — confirms both
        variants are checked."""
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {
                "content": "",
                "reasoning": "Thinking it through.\nuptime"
            }}]
        })
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result == "uptime"

    def test_dict_shaped_reasoning_field_does_not_crash(self):
        """Regression test for a real, defensive gap found via a
        deliberate function-by-function read: a different, OpenAI-
        proper convention exists where 'reasoning' is itself a dict
        (e.g. {"effort": "none"}), distinct from the plain-string
        'reasoning_content'/'reasoning' shape llama.cpp's own real
        response format actually uses (confirmed against llama.cpp's
        server README). Not reachable through this project's actual
        documented backend, but .splitlines() against a dict would
        raise an uncaught AttributeError if it ever were.

        Calls _complete_openai() DIRECTLY rather than the public
        complete() wrapper — complete()'s own outer except Exception
        would catch this AttributeError regardless of whether the
        inner defensive guard exists, which would make a test only
        checking the final return value pass either way and prove
        nothing about which layer actually handled it. Checking the
        inner function's own behavior is the only way to confirm this
        specific guard, not just the existing outer safety net,
        actually does the work."""
        from app.llm import _complete_openai
        mock_resp = self._mock_response({
            "choices": [{"message": {
                "content": "",
                "reasoning": {"effort": "none"}
            }}]
        })
        with patch("app.llm._session.post", return_value=mock_resp):
            result = _complete_openai("test", 100, 0.0)
        assert result is None

    def test_returns_none_when_content_and_reasoning_both_empty(self):
        """Confirms the fix doesn't change behavior when there's
        genuinely no usable content anywhere — the existing
        test_returns_none_when_content_empty case, re-verified alongside
        its new sibling to confirm both genuinely coexist correctly."""
        from app.llm import complete
        mock_resp = self._mock_response({
            "choices": [{"message": {"content": "", "reasoning_content": ""}}]
        })
        with patch("app.llm._session.post", return_value=mock_resp):
            result = complete("test")
        assert result is None

    def test_returns_none_on_connection_error(self):
        from app.llm import complete
        with patch("app.llm._session.post", side_effect=req.exceptions.ConnectionError()):
            result = complete("test")
        assert result is None

    def test_hits_openai_compatible_endpoint(self):
        from app.llm import complete
        mock_resp = self._mock_response({"choices": [{"message": {"content": "kiwix"}}]})
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/v1/chat/completions" in call_args.args[0]

    def test_sends_correct_payload_structure(self):
        from app.llm import complete
        mock_resp = self._mock_response({"choices": [{"message": {"content": "kiwix"}}]})
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
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
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/v1/chat/completions" in call_args.args[0]

    def test_does_not_send_keep_alive(self):
        """Deliberate, not an oversight — see _complete_openai()'s own
        docstring: Ollama's OpenAI-compatible endpoint is confirmed to
        silently ignore keep_alive when sent this way, and a genuinely
        different OpenAI-compatible backend (llama-server, LM Studio)
        has no standard equivalent. Sending it anyway would be a false
        promise of control. Confirms the field is genuinely absent from
        the payload, not just unused — changing settings.llm_keep_alive
        here must have zero effect on what's sent on this path."""
        from app.config import settings
        from app.llm import complete
        orig_keep_alive = settings.llm_keep_alive
        settings.llm_keep_alive = "-1"
        try:
            mock_resp = self._mock_response({"choices": [{"message": {"content": "kiwix"}}]})
            with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
                complete("test")
            assert "keep_alive" not in mock_post.call_args.kwargs["json"]
        finally:
            settings.llm_keep_alive = orig_keep_alive


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
        with patch("app.llm._session.post", return_value=mock_resp) as mock_post:
            complete("test")
        call_args = mock_post.call_args
        assert "/api/generate" in call_args.args[0]


class TestPersistentConnection:
    """Tests for the persistent-session fix: connection pooling across
    calls, not just that complete()'s output is unchanged. The same
    "prove the property, not just the symptom" discipline
    uptime_kuma.py's own TestPersistentConnection class already
    established for the structurally identical class of fix (a fresh
    connection on every call, replaced with a reused one) — found while
    investigating why singleflight (v3.50.13) didn't move `auto`'s
    cold-path benchmark plateau despite the deduplication mechanism
    itself working correctly in isolation.
    """

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

    def test_module_has_a_persistent_session_not_bare_requests_calls(self):
        """The actual mechanism this fix changes: complete() must call
        through a module-level requests.Session, not the bare
        requests.post()/requests.get() module functions — confirmed
        directly by checking the real object identity, not just that
        the right URL eventually gets hit."""
        import requests
        from app import llm
        assert isinstance(llm._session, requests.Session)

    def test_same_session_object_used_across_multiple_calls(self):
        """Proves reuse, not just presence: two sequential complete()
        calls must dispatch through the IDENTICAL Session object,
        confirming the module doesn't construct a fresh Session (or
        fall back to a bare requests.post()) per call — the actual
        property that gives this fix its real benefit (one pooled TCP
        connection, not N fresh ones)."""
        from app.llm import complete
        from app import llm

        session_before = llm._session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "kiwix"}
        mock_resp.raise_for_status.return_value = None

        with patch.object(llm._session, "post", return_value=mock_resp) as mock_post:
            complete("first call")
            complete("second call")

        # The session object itself was never replaced mid-test.
        assert llm._session is session_before
        # And both real calls went through that one session's .post,
        # not two independent bare requests.post() calls.
        assert mock_post.call_count == 2

    def test_session_has_a_real_connection_pool_adapter(self):
        """Confirms the Session genuinely has pooling machinery
        attached (an HTTPAdapter, mounted for both http:// and https://
        — requests' own default behavior on Session construction), not
        just that it happens to expose a .post() method with the same
        call signature as the bare module function. A bare
        requests.post() call internally constructs and immediately
        discards its own throwaway Session with no pooling at all; this
        test distinguishes "looks like a session" from "is actually a
        session with a real, reusable adapter mounted."""
        from app import llm
        assert "http://" in llm._session.adapters
        assert "https://" in llm._session.adapters
        adapter = llm._session.adapters["http://"]
        assert adapter._pool_maxsize >= 1

    def test_ollama_path_uses_the_persistent_session(self):
        """Direct confirmation for the Ollama code path specifically —
        patching app.llm._session.post (not app.llm.requests.post) is
        what now correctly intercepts the real call, proving
        _complete_ollama() was actually migrated, not left calling the
        bare module function under a misleading new test target."""
        from app.llm import complete
        from app import llm
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "forecast"}
        mock_resp.raise_for_status.return_value = None
        with patch.object(llm._session, "post", return_value=mock_resp) as mock_post:
            result = complete("test")
        assert result == "forecast"
        assert mock_post.called

    def test_openai_path_uses_the_persistent_session(self):
        """The OpenAI-compatible code path's own equivalent
        confirmation — both _complete_ollama() and _complete_openai()
        needed the identical migration, checked independently since
        they're two separate functions with their own separate
        requests.post() call sites before this fix."""
        from app.config import settings
        from app.llm import complete
        from app import llm
        settings.llm_api_type = "openai"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "uptime"}}]}
        mock_resp.raise_for_status.return_value = None
        with patch.object(llm._session, "post", return_value=mock_resp) as mock_post:
            result = complete("test")
        assert result == "uptime"
        assert mock_post.called

    def test_session_pool_size_is_configurable_not_library_default(self):
        """Confirms the pool is explicitly sized from
        settings.llm_connection_pool_size rather than left at requests'
        own library default (10) — found necessary because Starlette's
        own default thread-pool limit for sync routes is 40 (confirmed
        directly via anyio.to_thread.current_default_thread_limiter()),
        comfortably exceeding requests' default pool size under real
        concurrent load."""
        from app import llm
        from app.config import settings
        adapter = llm._session.adapters["http://"]
        assert adapter._pool_maxsize == settings.llm_connection_pool_size
        https_adapter = llm._session.adapters["https://"]
        assert https_adapter._pool_maxsize == settings.llm_connection_pool_size
