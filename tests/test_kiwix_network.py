"""
Tests for app/sources/kiwix.py network-calling functions —
_fetch_catalog_page, get_books, refresh_catalog, _pick_books_with_llm,
_search_book, _fetch_article.

All HTTP calls are mocked. These complement test_kiwix.py, which covers
the pure scoring/stemming logic; this file covers the OPDS catalog parsing,
HTML scraping, and LLM book-selection dispatch.
"""
import pytest
from unittest.mock import patch, MagicMock
import requests as req


SAMPLE_OPDS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">
  <entry>
    <title>Wikipedia</title>
    <summary>Free encyclopedia</summary>
    <link type="text/html" href="/content/wikipedia_en_all_maxi_2026-02"/>
  </entry>
  <entry>
    <title>Unix Stack Exchange</title>
    <summary>Q&amp;A for Unix users</summary>
    <link type="text/html" href="/content/unix.stackexchange.com_en_all_2026-02"/>
  </entry>
</feed>"""

EMPTY_OPDS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">
</feed>"""


class TestFetchCatalogPage:
    """Tests for _fetch_catalog_page OPDS XML parsing."""

    def _mock_xml_response(self, xml_content):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = xml_content.encode("utf-8")
        resp.raise_for_status.return_value = None
        return resp

    def test_parses_valid_opds_entries(self):
        from app.sources.kiwix import _fetch_catalog_page
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_xml_response(SAMPLE_OPDS_XML)):
            books = _fetch_catalog_page("http://kiwix:8080/catalog/v2/entries")
        assert len(books) == 2

    def test_extracts_book_name_from_href(self):
        from app.sources.kiwix import _fetch_catalog_page
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_xml_response(SAMPLE_OPDS_XML)):
            books = _fetch_catalog_page("http://kiwix:8080/catalog/v2/entries")
        names = [b["name"] for b in books]
        assert "wikipedia_en_all_maxi_2026-02" in names
        assert "unix.stackexchange.com_en_all_2026-02" in names

    def test_extracts_title_and_summary(self):
        from app.sources.kiwix import _fetch_catalog_page
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_xml_response(SAMPLE_OPDS_XML)):
            books = _fetch_catalog_page("http://kiwix:8080/catalog/v2/entries")
        wiki = next(b for b in books if "wikipedia" in b["name"])
        assert wiki["title"] == "Wikipedia"
        assert wiki["summary"] == "Free encyclopedia"

    def test_empty_feed_returns_empty_list(self):
        from app.sources.kiwix import _fetch_catalog_page
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_xml_response(EMPTY_OPDS_XML)):
            books = _fetch_catalog_page("http://kiwix:8080/catalog/v2/entries")
        assert books == []

    def test_returns_empty_on_connection_error(self):
        from app.sources.kiwix import _fetch_catalog_page
        with patch("app.sources.kiwix.requests.get", side_effect=req.exceptions.ConnectionError()):
            books = _fetch_catalog_page("http://kiwix:8080/catalog/v2/entries")
        assert books == []

    def test_returns_empty_on_malformed_xml(self):
        from app.sources.kiwix import _fetch_catalog_page
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_xml_response("not xml at all <<<")):
            books = _fetch_catalog_page("http://kiwix:8080/catalog/v2/entries")
        assert books == []

    def test_entry_without_html_link_skipped(self):
        from app.sources.kiwix import _fetch_catalog_page
        xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>No Link Book</title></entry>
</feed>"""
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_xml_response(xml)):
            books = _fetch_catalog_page("http://kiwix:8080/catalog/v2/entries")
        assert books == []


class TestGetBooks:
    """Tests for get_books() pagination and caching."""

    def setup_method(self):
        import app.sources.kiwix as kiwix_module
        self._original_cache = list(kiwix_module._book_cache)
        kiwix_module._book_cache = []

    def teardown_method(self):
        import app.sources.kiwix as kiwix_module
        kiwix_module._book_cache = self._original_cache

    def test_returns_cached_books_without_fetching(self):
        import app.sources.kiwix as kiwix_module
        kiwix_module._book_cache = [{"name": "cached_book", "title": "T", "summary": ""}]
        with patch("app.sources.kiwix._fetch_catalog_page") as mock_fetch:
            books = kiwix_module.get_books()
        assert not mock_fetch.called
        assert books == [{"name": "cached_book", "title": "T", "summary": ""}]

    def test_fetches_and_caches_when_empty(self):
        from app.sources.kiwix import get_books
        page1 = [{"name": "b1", "title": "T1", "summary": ""}] * 10  # full page
        page2 = [{"name": "b2", "title": "T2", "summary": ""}]  # partial page, ends pagination
        with patch("app.sources.kiwix._fetch_catalog_page", side_effect=[page1, page2]):
            books = get_books()
        assert len(books) == 11

    def test_stops_pagination_on_partial_page(self):
        from app.sources.kiwix import get_books
        partial_page = [{"name": "only_book", "title": "T", "summary": ""}]
        with patch("app.sources.kiwix._fetch_catalog_page", return_value=partial_page) as mock_fetch:
            get_books()
        # Should only call once since first page was already partial (< page_size)
        assert mock_fetch.call_count == 1

    def test_no_books_found_returns_empty(self):
        from app.sources.kiwix import get_books
        with patch("app.sources.kiwix._fetch_catalog_page", return_value=[]):
            books = get_books()
        assert books == []


class TestRefreshCatalog:
    """Tests for refresh_catalog() forcing a fresh fetch."""

    def setup_method(self):
        import app.sources.kiwix as kiwix_module
        self._original_cache = list(kiwix_module._book_cache)

    def teardown_method(self):
        import app.sources.kiwix as kiwix_module
        kiwix_module._book_cache = self._original_cache

    def test_clears_cache_before_fetching(self):
        import app.sources.kiwix as kiwix_module
        kiwix_module._book_cache = [{"name": "old_book", "title": "Old", "summary": ""}]
        new_books = [{"name": "new_book", "title": "New", "summary": ""}]
        with patch("app.sources.kiwix._fetch_catalog_page", side_effect=[new_books, []]):
            result = kiwix_module.refresh_catalog()
        assert result == new_books
        assert "old_book" not in [b["name"] for b in result]


class TestPickBooksWithLLM:
    """Tests for _pick_books_with_llm() book selection dispatch."""

    def _books(self):
        return [
            {"name": "wikipedia_en_all_maxi_2026-02", "title": "Wikipedia", "summary": "Encyclopedia"},
            {"name": "unix.stackexchange.com_en_all_2026-02", "title": "Unix SE", "summary": "Q&A"},
        ]

    def test_empty_books_returns_empty(self):
        from app.sources.kiwix import _pick_books_with_llm
        result = _pick_books_with_llm("test query", [])
        assert result == []

    def test_uses_routing_cache_when_valid(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns:
            mock_get_routing = MagicMock(return_value="wikipedia_en_all_maxi_2026-02")
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("what is nitrogen", self._books())
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_ignores_cached_book_not_in_current_list(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=False):
            mock_get_routing = MagicMock(return_value="some_deleted_book")
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", self._books())
        # Cached book no longer exists, falls through to non-LLM fallback (wikipedia)
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_falls_back_to_wikipedia_when_llm_not_configured(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=False):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", self._books())
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_falls_back_to_first_book_when_no_wikipedia_and_not_configured(self):
        from app.sources.kiwix import _pick_books_with_llm
        books = [{"name": "some_other_book", "title": "X", "summary": ""}]
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=False):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", books)
        assert result == ["some_other_book"]

    def test_llm_picks_exact_book_name(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="wikipedia_en_all_maxi_2026-02"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("what is nitrogen", self._books())
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_llm_fuzzy_substring_match(self):
        """LLM sometimes returns a partial/truncated book name — should fuzzy match."""
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="wikipedia"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("what is nitrogen", self._books())
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_llm_returns_multiple_books_respects_max(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="wikipedia_en_all_maxi_2026-02, unix.stackexchange.com_en_all_2026-02"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", self._books(), max_books=1)
        assert len(result) == 1

    def test_llm_invalid_response_falls_back(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="completely_unrelated_garbage_xyz"):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", self._books())
        # Falls back to Wikipedia-first
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_llm_none_response_falls_back(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value=None):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", self._books())
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_llm_empty_string_response_falls_back(self):
        """Regression test — empty string used to incorrectly substring-match
        whatever book name happened to come first in unordered set iteration,
        rather than falling through to the Wikipedia-first fallback."""
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value=""):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", self._books())
        assert result == ["wikipedia_en_all_maxi_2026-02"]

    def test_llm_whitespace_only_response_falls_back(self):
        from app.sources.kiwix import _pick_books_with_llm
        with patch("app.sources.kiwix._get_routing_fns") as mock_fns, \
             patch("app.llm.is_configured", return_value=True), \
             patch("app.llm.complete", return_value="   "):
            mock_get_routing = MagicMock(return_value=None)
            mock_set_routing = MagicMock()
            mock_fns.return_value = (mock_get_routing, mock_set_routing)
            result = _pick_books_with_llm("test", self._books())
        assert result == ["wikipedia_en_all_maxi_2026-02"]


class TestSearchBook:
    """Tests for _search_book() HTML scraping of Kiwix search results."""

    def setup_method(self):
        from app.config import settings
        self._original_url = settings.kiwix_url
        settings.kiwix_url = "http://kiwix:8080"

    def teardown_method(self):
        from app.config import settings
        settings.kiwix_url = self._original_url

    def _mock_html_response(self, html):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = html.encode("utf-8")
        resp.raise_for_status.return_value = None
        return resp

    def test_parses_results_with_title_and_excerpt(self):
        from app.sources.kiwix import _search_book
        html = """
        <div class="results">
          <li><a href="/viewer#wikipedia/A/Nitrogen">Nitrogen</a><cite>chemical element</cite></li>
        </div>
        """
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            results = _search_book("nitrogen", "wikipedia_en_all_maxi_2026-02")
        assert len(results) == 1
        assert results[0]["title"] == "Nitrogen"
        assert results[0]["excerpt"] == "chemical element"

    def test_no_results_div_returns_empty(self):
        from app.sources.kiwix import _search_book
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response("<html><body>nothing</body></html>")):
            results = _search_book("test", "wikipedia_en_all_maxi_2026-02")
        assert results == []

    def test_excludes_stack_exchange_tag_pages(self):
        from app.sources.kiwix import _search_book
        html = """
        <div class="results">
          <li><a href="/viewer#se/questions/tagged/python">Python tag</a></li>
          <li><a href="/viewer#se/A/Real-Question">Real Question</a><cite>excerpt</cite></li>
        </div>
        """
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            results = _search_book("python", "stackoverflow")
        assert len(results) == 1
        assert results[0]["title"] == "Real Question"

    def test_handles_missing_cite_tag(self):
        from app.sources.kiwix import _search_book
        html = """
        <div class="results">
          <li><a href="/viewer#wikipedia/A/Test">Test</a></li>
        </div>
        """
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            results = _search_book("test", "wikipedia_en_all_maxi_2026-02")
        assert results[0]["excerpt"] == ""

    def test_returns_empty_on_connection_error(self):
        from app.sources.kiwix import _search_book
        with patch("app.sources.kiwix.requests.get", side_effect=req.exceptions.ConnectionError()):
            results = _search_book("test", "wikipedia_en_all_maxi_2026-02")
        assert results == []

    def test_book_field_set_correctly(self):
        from app.sources.kiwix import _search_book
        html = """
        <div class="results">
          <li><a href="/viewer#wikipedia/A/Test">Test</a><cite>excerpt</cite></li>
        </div>
        """
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            results = _search_book("test", "wikipedia_en_all_maxi_2026-02")
        assert results[0]["book"] == "wikipedia_en_all_maxi_2026-02"


class TestFetchArticle:
    """Tests for _fetch_article() HTML content extraction."""

    def _mock_html_response(self, html):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = html.encode("utf-8")
        resp.raise_for_status.return_value = None
        return resp

    def test_extracts_wikipedia_style_content(self):
        from app.sources.kiwix import _fetch_article
        html = '<html><body><div class="mw-parser-output">Nitrogen is a chemical element.</div></body></html>'
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            result = _fetch_article("http://kiwix:8080/viewer#wikipedia/A/Nitrogen")
        assert "Nitrogen is a chemical element" in result

    def test_falls_back_to_body_when_no_known_selector(self):
        from app.sources.kiwix import _fetch_article
        html = "<html><body>Just plain body content here.</body></html>"
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            result = _fetch_article("http://kiwix:8080/viewer#test")
        assert "Just plain body content" in result

    def test_strips_script_and_style_tags(self):
        from app.sources.kiwix import _fetch_article
        html = '''<html><body><div class="mw-parser-output">
            <script>alert('bad')</script>
            <style>.x { color: red; }</style>
            Real content here.
        </div></body></html>'''
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            result = _fetch_article("http://kiwix:8080/viewer#test")
        assert "alert" not in result
        assert "color: red" not in result
        assert "Real content here" in result

    def test_truncates_to_max_chars(self):
        from app.sources.kiwix import _fetch_article
        long_text = "x" * 5000
        html = f'<html><body><div class="mw-parser-output">{long_text}</div></body></html>'
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            result = _fetch_article("http://kiwix:8080/viewer#test", max_chars=100)
        assert len(result) <= 100

    def test_collapses_excess_newlines(self):
        from app.sources.kiwix import _fetch_article
        html = '<html><body><div class="mw-parser-output">Line1\n\n\n\n\nLine2</div></body></html>'
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            result = _fetch_article("http://kiwix:8080/viewer#test")
        assert "\n\n\n" not in result

    def test_no_content_found_returns_empty(self):
        from app.sources.kiwix import _fetch_article
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response("")):
            result = _fetch_article("http://kiwix:8080/viewer#test")
        assert result == ""

    def test_returns_empty_on_connection_error(self):
        from app.sources.kiwix import _fetch_article
        with patch("app.sources.kiwix.requests.get", side_effect=req.exceptions.ConnectionError()):
            result = _fetch_article("http://kiwix:8080/viewer#test")
        assert result == ""

    def test_stack_exchange_style_question_div(self):
        from app.sources.kiwix import _fetch_article
        html = '<html><body><div id="question">How do I do X?</div></body></html>'
        with patch("app.sources.kiwix.requests.get", return_value=self._mock_html_response(html)):
            result = _fetch_article("http://kiwix:8080/viewer#se/test")
        assert "How do I do X?" in result
