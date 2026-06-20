"""
Tests for app/sources/freshrss.py — general query detection and article scoring.
No network calls required.
"""
import pytest


# ---------------------------------------------------------------------------
# General query detection
# ---------------------------------------------------------------------------

class TestIsGeneralQuery:
    """Tests for _is_general_query — determines if filtering should be skipped."""

    def setup_method(self):
        from app.sources.freshrss import _is_general_query
        self.check = _is_general_query

    def test_news_is_general(self):
        assert self.check("news") is True

    def test_headlines_is_general(self):
        assert self.check("headlines") is True

    def test_my_feeds_is_general(self):
        assert self.check("my feeds") is True

    def test_rss_is_general(self):
        assert self.check("rss") is True

    def test_whats_happening_is_general(self):
        assert self.check("what's happening") is True

    def test_whats_happening_no_apostrophe_is_general(self):
        assert self.check("whats happening") is True

    def test_specific_topic_is_not_general(self):
        assert self.check("news about politics") is False

    def test_specific_technology_is_not_general(self):
        assert self.check("articles about Docker") is False

    def test_specific_event_is_not_general(self):
        assert self.check("news about the election") is False

    # Regression tests — things that used to wrongly be general
    def test_latest_iphone_is_not_general(self):
        assert self.check("latest iPhone release") is False

    def test_recent_earthquakes_is_not_general(self):
        assert self.check("recent earthquakes") is False

    def test_recent_python_is_not_general(self):
        assert self.check("recent Python releases") is False


# ---------------------------------------------------------------------------
# Article scoring — now delegated to app.scoring.filter_and_rank, see
# tests/test_scoring.py for the underlying scoring mechanics. These tests
# confirm freshrss.py wires it in correctly, not the scoring math itself.
# ---------------------------------------------------------------------------

class TestRecencyBonus:
    """Tests for _recency_bonus — freshness scoring for news articles."""

    def test_no_bonus_for_missing_timestamp(self):
        from app.sources.freshrss import _recency_bonus
        assert _recency_bonus(None) == 0

    def test_no_bonus_for_zero_timestamp(self):
        from app.sources.freshrss import _recency_bonus
        assert _recency_bonus(0) == 0

    def test_high_bonus_for_very_recent_article(self):
        from app.sources.freshrss import _recency_bonus
        import time
        one_minute_ago = int(time.time()) - 60
        assert _recency_bonus(one_minute_ago) == 15

    def test_medium_bonus_for_few_hours_old(self):
        from app.sources.freshrss import _recency_bonus
        import time
        four_hours_ago = int(time.time()) - (4 * 3600)
        assert _recency_bonus(four_hours_ago) == 10

    def test_low_bonus_for_within_a_day(self):
        from app.sources.freshrss import _recency_bonus
        import time
        twenty_hours_ago = int(time.time()) - (20 * 3600)
        assert _recency_bonus(twenty_hours_ago) == 5

    def test_no_bonus_for_old_article(self):
        from app.sources.freshrss import _recency_bonus
        import time
        three_days_ago = int(time.time()) - (3 * 86400)
        assert _recency_bonus(three_days_ago) == 0

    def test_no_bonus_for_future_timestamp(self):
        from app.sources.freshrss import _recency_bonus
        import time
        future = int(time.time()) + 3600
        assert _recency_bonus(future) == 0


class TestGetToken:
    """Tests for _get_token FreshRSS authentication."""

    def test_returns_token_on_success(self):
        from app.sources import freshrss
        from app.config import settings
        from unittest.mock import patch, MagicMock
        settings.freshrss_url = "http://freshrss"
        settings.freshrss_user = "admin"
        settings.freshrss_api_password = "password"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "SID=abc\nLSID=def\nAuth=mytoken123\n"
        with patch("app.sources.freshrss.requests.post", return_value=mock_resp):
            token = freshrss._get_token()
        assert token == "mytoken123"
        settings.freshrss_url = ""
        settings.freshrss_user = ""
        settings.freshrss_api_password = ""

    def test_returns_none_on_auth_failure(self):
        from app.sources import freshrss
        from app.config import settings
        from unittest.mock import patch, MagicMock
        settings.freshrss_url = "http://freshrss"
        settings.freshrss_user = "admin"
        settings.freshrss_api_password = "wrong"
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("app.sources.freshrss.requests.post", return_value=mock_resp):
            token = freshrss._get_token()
        assert token is None
        settings.freshrss_url = ""
        settings.freshrss_user = ""
        settings.freshrss_api_password = ""

    def test_returns_none_on_connection_error(self):
        from app.sources import freshrss
        from app.config import settings
        import requests as req
        from unittest.mock import patch
        settings.freshrss_url = "http://freshrss"
        settings.freshrss_user = "admin"
        settings.freshrss_api_password = "password"
        with patch("app.sources.freshrss.requests.post", side_effect=req.exceptions.ConnectionError()):
            token = freshrss._get_token()
        assert token is None
        settings.freshrss_url = ""
        settings.freshrss_user = ""
        settings.freshrss_api_password = ""

    def test_returns_none_when_auth_missing_from_response(self):
        from app.sources import freshrss
        from app.config import settings
        from unittest.mock import patch, MagicMock
        settings.freshrss_url = "http://freshrss"
        settings.freshrss_user = "admin"
        settings.freshrss_api_password = "password"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "SID=abc\nLSID=def\n"  # no Auth= line
        with patch("app.sources.freshrss.requests.post", return_value=mock_resp):
            token = freshrss._get_token()
        assert token is None
        settings.freshrss_url = ""
        settings.freshrss_user = ""
        settings.freshrss_api_password = ""
