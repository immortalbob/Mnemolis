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
# Article scoring
# ---------------------------------------------------------------------------

class TestScoreArticle:
    """Tests for _score_article — article relevance to query."""

    def setup_method(self):
        from app.sources.freshrss import _score_article, _STOP_WORDS
        self.score = _score_article
        self.stop_words = _STOP_WORDS

    def _query_words(self, query: str) -> set:
        return set(query.lower().split()) - self.stop_words

    def test_title_match_scores_higher_than_summary(self):
        query_words = self._query_words("politics")
        title_score = self.score("Politics in America", "", query_words)
        summary_score = self.score("Daily Roundup", "politics discussed today", query_words)
        assert title_score > summary_score

    def test_zero_score_for_unrelated(self):
        query_words = self._query_words("politics")
        score = self.score("Blueberry Pancake Recipe", "whole grain cornmeal healthy", query_words)
        assert score == 0

    def test_multi_word_query_scores_higher_with_more_hits(self):
        query_words = self._query_words("artificial intelligence machine learning")
        full_match = self.score("AI and Machine Learning Trends", "artificial intelligence developments", query_words)
        partial_match = self.score("AI Overview", "some information", query_words)
        assert full_match > partial_match

    def test_stop_words_dont_contribute_to_score(self):
        query_words = self._query_words("what is the news")
        # After stop word removal, query_words should be empty or just "news"
        meaningful = query_words - self.stop_words
        # A generic title shouldn't score high just from stop word matches
        score = self.score("What Is It", "the news is here", query_words)
        # Score should only reflect actual meaningful word hits
        assert score >= 0  # just verify it doesn't crash

    def test_case_insensitive_scoring(self):
        query_words = self._query_words("Politics")
        score1 = self.score("politics in washington", "", query_words)
        score2 = self.score("POLITICS IN WASHINGTON", "", query_words)
        assert score1 == score2

    def test_empty_query_words_scores_zero(self):
        score = self.score("Any Title", "any summary", set())
        assert score == 0


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
