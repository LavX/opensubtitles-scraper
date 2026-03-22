"""Comprehensive tests for scraper.py, base_provider.py, and opensubtitles_scraper_provider.py"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

from src.parsers.search_parser import SearchResult
from src.parsers.subtitle_parser import SubtitleInfo
from src.utils.exceptions import SearchError, ScrapingError, DownloadError


# ---------------------------------------------------------------------------
# Helpers to build mock objects
# ---------------------------------------------------------------------------

def make_search_result(title="Test Movie", year=2020, imdb_id="tt1234567",
                       url="/en/subtitles/123", subtitle_count=10, kind="movie"):
    return SearchResult(
        title=title, year=year, imdb_id=imdb_id,
        url=url, subtitle_count=subtitle_count, kind=kind,
    )


def make_subtitle_info(subtitle_id="999", language="en", filename="test.srt",
                       release_name="Test.Release", uploader="user1",
                       download_count=100, rating=8.0, hearing_impaired=False,
                       forced=False, fps=23.976, download_url="/en/download/sub/999"):
    return SubtitleInfo(
        subtitle_id=subtitle_id, language=language, filename=filename,
        release_name=release_name, uploader=uploader,
        download_count=download_count, rating=rating,
        hearing_impaired=hearing_impaired, forced=forced, fps=fps,
        download_url=download_url,
    )


def _mock_response(text="<html></html>", content=b"", headers=None, status_code=200):
    """Create a mock HTTP response."""
    resp = MagicMock()
    resp.text = text
    resp.content = content
    resp.headers = headers or {}
    resp.status_code = status_code
    resp.close = MagicMock()
    return resp


# ===================================================================
# SCRAPER TESTS (src/core/scraper.py)
# ===================================================================

class TestOpenSubtitlesScraperInit:
    """Test __init__ wiring."""

    @patch("src.core.scraper.IMDBLookupService")
    @patch("src.core.scraper.DownloadParser")
    @patch("src.core.scraper.SubtitleParser")
    @patch("src.core.scraper.SearchParser")
    @patch("src.core.scraper.SessionManager")
    def test_init_default_timeout(self, MockSM, MockSP, MockSubP, MockDP, MockIMDB):
        from src.core.scraper import OpenSubtitlesScraper
        scraper = OpenSubtitlesScraper()
        MockSM.assert_called_once_with(timeout=30)
        assert scraper.base_url == "https://www.opensubtitles.org"
        MockSP.assert_called_once()
        MockSubP.assert_called_once_with(session_manager=scraper.session_manager)
        MockDP.assert_called_once()
        MockIMDB.assert_called_once_with(scraper.session_manager)

    @patch("src.core.scraper.IMDBLookupService")
    @patch("src.core.scraper.DownloadParser")
    @patch("src.core.scraper.SubtitleParser")
    @patch("src.core.scraper.SearchParser")
    @patch("src.core.scraper.SessionManager")
    def test_init_custom_timeout(self, MockSM, MockSP, MockSubP, MockDP, MockIMDB):
        from src.core.scraper import OpenSubtitlesScraper
        OpenSubtitlesScraper(timeout=60)
        MockSM.assert_called_once_with(timeout=60)


# ---------------------------------------------------------------------------
# Fixture that provides a fully-mocked scraper instance
# ---------------------------------------------------------------------------

@pytest.fixture
def scraper():
    with patch("src.core.scraper.SessionManager") as MockSM, \
         patch("src.core.scraper.SearchParser") as MockSP, \
         patch("src.core.scraper.SubtitleParser") as MockSubP, \
         patch("src.core.scraper.DownloadParser") as MockDP, \
         patch("src.core.scraper.IMDBLookupService") as MockIMDB:
        from src.core.scraper import OpenSubtitlesScraper
        s = OpenSubtitlesScraper()
        # Expose mocks for easy access
        s._mock_session = s.session_manager
        s._mock_search_parser = s.search_parser
        s._mock_subtitle_parser = s.subtitle_parser
        s._mock_download_parser = s.download_parser
        s._mock_imdb_lookup = s.imdb_lookup
        yield s


# ---------------------------------------------------------------------------
# search_movies
# ---------------------------------------------------------------------------

class TestSearchMovies:

    def test_success_via_imdb_id(self, scraper):
        """When imdb_id is provided and IMDB search returns results."""
        result = make_search_result(imdb_id="tt1234567", kind="movie")
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_page.return_value = [result]
        results = scraper.search_movies("Test Movie", year=2020, imdb_id="tt1234567")
        assert len(results) >= 1

    def test_success_via_autocomplete(self, scraper):
        """Autocomplete returns results, no IMDB ID."""
        result = make_search_result(title="Test Movie")
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_autocomplete.return_value = [result]
        results = scraper.search_movies("Test Movie", year=2020)
        assert len(results) >= 1

    def test_success_via_full_page(self, scraper):
        """Autocomplete empty, full page search returns results."""
        result = make_search_result(title="Test Movie")
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_autocomplete.return_value = []
        scraper._mock_search_parser.parse_search_page.return_value = [result]
        results = scraper.search_movies("Test Movie", year=2020)
        assert len(results) >= 1

    def test_empty_query_with_imdb_id_resolves_title(self, scraper):
        """Empty query + IMDB ID triggers title lookup."""
        # IMDB search returns nothing, so it falls through to title lookup
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_page.return_value = []
        scraper._mock_imdb_lookup.lookup_title.return_value = "Resolved Title"
        result = make_search_result(title="Resolved Title")
        scraper._mock_search_parser.parse_search_autocomplete.return_value = [result]
        results = scraper.search_movies("", imdb_id="tt1234567")
        scraper._mock_imdb_lookup.lookup_title.assert_called_once_with("tt1234567")
        assert len(results) >= 1

    def test_empty_query_imdb_id_lookup_fails(self, scraper):
        """Empty query + IMDB ID, title lookup fails returns empty."""
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_page.return_value = []
        scraper._mock_imdb_lookup.lookup_title.return_value = None
        results = scraper.search_movies("", imdb_id="tt1234567")
        assert results == []

    def test_exception_wraps_into_search_error(self, scraper):
        """Any exception is wrapped in SearchError."""
        # Must use imdb_id to trigger _search_by_imdb_id which propagates errors
        # Autocomplete/full-page search swallow exceptions and return []
        scraper._mock_session.get.side_effect = RuntimeError("boom")
        scraper._mock_imdb_lookup.lookup_title.side_effect = RuntimeError("boom")
        with pytest.raises(SearchError, match="Movie search failed"):
            scraper.search_movies("", imdb_id="tt9999999")


# ---------------------------------------------------------------------------
# search_tv_shows
# ---------------------------------------------------------------------------

class TestSearchTvShows:

    def test_success_via_imdb_id(self, scraper):
        result = make_search_result(kind="episode", imdb_id="tt9999999", title="Breaking Bad")
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_page.return_value = [result]
        results = scraper.search_tv_shows("Breaking Bad", imdb_id="tt9999999")
        assert len(results) >= 1

    def test_success_via_autocomplete(self, scraper):
        result = make_search_result(kind="episode", title="Breaking Bad")
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_autocomplete.return_value = [result]
        results = scraper.search_tv_shows("Breaking Bad")
        assert len(results) >= 1

    def test_success_via_full_page(self, scraper):
        result = make_search_result(kind="episode", title="Breaking Bad")
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_autocomplete.return_value = []
        scraper._mock_search_parser.parse_search_page.return_value = [result]
        results = scraper.search_tv_shows("Breaking Bad")
        assert len(results) >= 1

    def test_empty_query_with_imdb_id(self, scraper):
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_page.return_value = []
        scraper._mock_imdb_lookup.lookup_title.return_value = "Breaking Bad"
        result = make_search_result(kind="episode", title="Breaking Bad", imdb_id="tt0903747")
        scraper._mock_search_parser.parse_search_autocomplete.return_value = [result]
        results = scraper.search_tv_shows("", imdb_id="tt0903747")
        assert len(results) >= 1

    def test_empty_query_imdb_lookup_fails(self, scraper):
        scraper._mock_session.get.return_value = _mock_response()
        scraper._mock_search_parser.parse_search_page.return_value = []
        scraper._mock_imdb_lookup.lookup_title.return_value = None
        results = scraper.search_tv_shows("", imdb_id="tt0903747")
        assert results == []

    def test_exception_wraps_into_search_error(self, scraper):
        scraper._mock_session.get.side_effect = RuntimeError("boom")
        scraper._mock_imdb_lookup.lookup_title.side_effect = RuntimeError("boom")
        with pytest.raises(SearchError, match="TV show search failed"):
            scraper.search_tv_shows("", imdb_id="tt9999999")


# ---------------------------------------------------------------------------
# _search_autocomplete
# ---------------------------------------------------------------------------

class TestSearchAutocomplete:

    def test_returns_results(self, scraper):
        resp = _mock_response(text="<html>results</html>")
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_autocomplete.return_value = [
            make_search_result()
        ]
        results = scraper._search_autocomplete("Matrix")
        assert len(results) == 1
        # Verify URL construction contains MovieName
        call_args = scraper._mock_session.get.call_args[0][0]
        assert "MovieName=Matrix" in call_args

    def test_exception_returns_empty(self, scraper):
        scraper._mock_session.get.side_effect = RuntimeError("network error")
        results = scraper._search_autocomplete("test")
        assert results == []

    def test_response_closed_on_success(self, scraper):
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_autocomplete.return_value = []
        scraper._search_autocomplete("test")
        resp.close.assert_called()


# ---------------------------------------------------------------------------
# _search_full_page
# ---------------------------------------------------------------------------

class TestSearchFullPage:

    def test_movie_params(self, scraper):
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_page.return_value = []
        scraper._search_full_page("Matrix", kind="movie")
        url = scraper._mock_session.get.call_args[0][0]
        assert "SearchOnlyMovies=on" in url
        assert "SearchOnlyTVSeries" not in url

    def test_episode_params(self, scraper):
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_page.return_value = []
        scraper._search_full_page("Breaking Bad", kind="episode")
        url = scraper._mock_session.get.call_args[0][0]
        assert "SearchOnlyTVSeries=on" in url

    def test_exception_returns_empty(self, scraper):
        scraper._mock_session.get.side_effect = RuntimeError("fail")
        results = scraper._search_full_page("test")
        assert results == []

    def test_response_closed(self, scraper):
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_page.return_value = []
        scraper._search_full_page("test")
        resp.close.assert_called()


# ---------------------------------------------------------------------------
# _search_by_imdb_id
# ---------------------------------------------------------------------------

class TestSearchByImdbId:

    def test_valid_imdb_id(self, scraper):
        result = make_search_result(imdb_id="tt1234567", kind="movie")
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_page.return_value = [result]
        results = scraper._search_by_imdb_id("tt1234567", kind="movie")
        assert len(results) == 1
        url = scraper._mock_session.get.call_args[0][0]
        assert "imdbid-1234567" in url

    def test_empty_imdb_number(self, scraper):
        results = scraper._search_by_imdb_id("nodigits")
        assert results == []

    def test_assigns_imdb_id_when_missing(self, scraper):
        result = make_search_result(imdb_id=None, kind="movie")
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_page.return_value = [result]
        results = scraper._search_by_imdb_id("tt1234567", kind="movie")
        assert results[0].imdb_id == "tt1234567"

    def test_filters_by_kind(self, scraper):
        movie = make_search_result(kind="movie")
        episode = make_search_result(kind="episode")
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_page.return_value = [movie, episode]
        results = scraper._search_by_imdb_id("tt1234567", kind="episode")
        assert all(r.kind == "episode" for r in results)

    def test_redirect_to_subtitle_page(self, scraper):
        """When no results but HTML contains /en/subtitles/, return synthetic result."""
        resp = _mock_response(text='<html>/en/subtitles/12345</html>')
        scraper._mock_session.get.return_value = resp
        scraper._mock_search_parser.parse_search_page.return_value = []
        results = scraper._search_by_imdb_id("tt1234567", kind="movie")
        assert len(results) == 1
        assert results[0].kind == "movie"

    def test_exception_returns_empty(self, scraper):
        scraper._mock_session.get.side_effect = RuntimeError("fail")
        results = scraper._search_by_imdb_id("tt1234567")
        assert results == []


# ---------------------------------------------------------------------------
# _filter_search_results
# ---------------------------------------------------------------------------

class TestFilterSearchResults:

    def test_empty_results(self, scraper):
        assert scraper._filter_search_results([], "query") == []

    def test_exact_title_match_score_100(self, scraper):
        r = make_search_result(title="The Matrix", kind="movie")
        filtered = scraper._filter_search_results([r], "The Matrix", kind="movie")
        assert len(filtered) == 1
        assert filtered[0].relevance_score == 100

    def test_partial_match_score_80(self, scraper):
        r = make_search_result(title="The Matrix Reloaded", kind="movie")
        filtered = scraper._filter_search_results([r], "The Matrix", kind="movie")
        assert len(filtered) == 1
        assert filtered[0].relevance_score == 80

    def test_word_overlap_scoring(self, scraper):
        r = make_search_result(title="Matrix Revolutions Extended", kind="movie")
        # "matrix" overlaps. query={matrix, final}, title={matrix, revolutions, extended}
        # overlap=1, union=4, score = (1/4)*60 = 15 -> below threshold 30
        filtered = scraper._filter_search_results([r], "matrix final", kind="movie")
        assert len(filtered) == 0  # filtered out by relevance threshold

    def test_word_overlap_above_threshold(self, scraper):
        r = make_search_result(title="Matrix Reloaded", kind="movie")
        # query={matrix, reloaded}, title={matrix, reloaded}, overlap=2, union=2, score=60
        filtered = scraper._filter_search_results([r], "matrix reloaded", kind="movie")
        assert len(filtered) == 1
        assert filtered[0].relevance_score == 100  # exact match

    def test_year_boost(self, scraper):
        r = make_search_result(title="The Matrix", year=1999, kind="movie")
        filtered = scraper._filter_search_results([r], "The Matrix", year=1999, kind="movie")
        assert filtered[0].relevance_score == 110  # 100 + 10

    def test_imdb_boost(self, scraper):
        r = make_search_result(title="The Matrix", imdb_id="tt0133093", kind="movie")
        filtered = scraper._filter_search_results(
            [r], "The Matrix", imdb_id="tt0133093", kind="movie"
        )
        assert filtered[0].relevance_score == 120  # 100 + 20

    def test_year_and_imdb_boost(self, scraper):
        r = make_search_result(title="The Matrix", year=1999, imdb_id="tt0133093", kind="movie")
        filtered = scraper._filter_search_results(
            [r], "The Matrix", year=1999, imdb_id="tt0133093", kind="movie"
        )
        assert filtered[0].relevance_score == 130  # 100 + 10 + 20

    def test_threshold_filtering(self, scraper):
        """Results below 30 are excluded."""
        r = make_search_result(title="Completely Different Title", kind="movie")
        filtered = scraper._filter_search_results([r], "Xyz Abc", kind="movie")
        # No word overlap -> score 0 -> below 30
        assert len(filtered) == 0

    def test_year_tolerance(self, scraper):
        """Year difference > 1 is excluded."""
        r = make_search_result(title="The Matrix", year=2000, kind="movie")
        filtered = scraper._filter_search_results([r], "The Matrix", year=1997, kind="movie")
        assert len(filtered) == 0

    def test_year_tolerance_within_one(self, scraper):
        """Year difference of 1 is allowed."""
        r = make_search_result(title="The Matrix", year=2000, kind="movie")
        filtered = scraper._filter_search_results([r], "The Matrix", year=1999, kind="movie")
        assert len(filtered) == 1

    def test_kind_filter(self, scraper):
        r = make_search_result(title="Breaking Bad", kind="episode")
        filtered = scraper._filter_search_results([r], "Breaking Bad", kind="movie")
        assert len(filtered) == 0

    def test_imdb_mismatch_filter(self, scraper):
        r = make_search_result(title="The Matrix", imdb_id="tt0000001", kind="movie")
        filtered = scraper._filter_search_results(
            [r], "The Matrix", imdb_id="tt9999999", kind="movie"
        )
        assert len(filtered) == 0

    def test_sorted_by_relevance(self, scraper):
        r1 = make_search_result(title="The Matrix Reloaded", kind="movie")  # partial 80
        r2 = make_search_result(title="The Matrix", kind="movie")  # exact 100
        filtered = scraper._filter_search_results([r1, r2], "The Matrix", kind="movie")
        assert filtered[0].relevance_score >= filtered[-1].relevance_score


# ---------------------------------------------------------------------------
# get_movie_url
# ---------------------------------------------------------------------------

class TestGetMovieUrl:

    def test_absolute_url(self, scraper):
        r = make_search_result(url="https://www.opensubtitles.org/en/subtitles/123")
        assert scraper.get_movie_url(r) == "https://www.opensubtitles.org/en/subtitles/123"

    def test_relative_url(self, scraper):
        r = make_search_result(url="/en/subtitles/123")
        assert scraper.get_movie_url(r) == "https://www.opensubtitles.org/en/subtitles/123"

    def test_no_url_movie(self, scraper):
        r = make_search_result(url=None, kind="movie", title="Matrix")
        url = scraper.get_movie_url(r)
        assert "/en/movies" in url
        assert "q=Matrix" in url

    def test_no_url_episode(self, scraper):
        r = make_search_result(url=None, kind="episode", title="Breaking Bad")
        url = scraper.get_movie_url(r)
        assert "/en/ssearch" in url


# ---------------------------------------------------------------------------
# get_subtitles
# ---------------------------------------------------------------------------

class TestGetSubtitles:

    def test_basic_no_language_filter(self, scraper):
        sub = make_subtitle_info(language="en")
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = [sub]
        subs = scraper.get_subtitles("https://example.com/en/subtitles/123")
        assert len(subs) == 1

    def test_language_filter_eng(self, scraper):
        sub_en = make_subtitle_info(language="en")
        sub_fr = make_subtitle_info(language="fr")
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = [sub_en, sub_fr]
        subs = scraper.get_subtitles("http://example.com/sublanguageid-all", languages=["eng"])
        assert all(s.language == "en" for s in subs)

    def test_language_filter_hun(self, scraper):
        sub = make_subtitle_info(language="hu")
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = [sub]
        subs = scraper.get_subtitles("http://example.com/sublanguageid-all", languages=["hun"])
        assert len(subs) == 1

    def test_language_filter_various_codes(self, scraper):
        """Test that 3-letter codes map to 2-letter."""
        for three, two in [("spa", "es"), ("fre", "fr"), ("ger", "de"), ("ita", "it"),
                           ("por", "pt"), ("rus", "ru"), ("chi", "zh"), ("jpn", "ja"),
                           ("kor", "ko"), ("ara", "ar"), ("dut", "nl"), ("pol", "pl")]:
            sub = make_subtitle_info(language=two)
            resp = _mock_response()
            scraper._mock_session.get.return_value = resp
            scraper._mock_subtitle_parser.parse_subtitle_page.return_value = [sub]
            subs = scraper.get_subtitles("http://example.com/sublanguageid-all", languages=[three])
            assert len(subs) == 1, f"Failed for {three}->{two}"

    def test_season_episode_delegates(self, scraper):
        """When season+episode given, delegates to _get_episode_subtitles."""
        sub = make_subtitle_info()
        resp = _mock_response(text="<html></html>")
        scraper._mock_session.get.return_value = resp
        with patch.object(scraper, "_get_episode_subtitles", return_value=[sub]) as mock_ep:
            subs = scraper.get_subtitles("http://example.com", season=1, episode=2)
            mock_ep.assert_called_once()
            assert len(subs) == 1

    def test_exception_wraps_into_scraping_error(self, scraper):
        scraper._mock_session.get.side_effect = RuntimeError("fail")
        with pytest.raises(ScrapingError, match="Subtitle listing failed"):
            scraper.get_subtitles("http://example.com")

    def test_url_modification_for_language(self, scraper):
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = []
        scraper.get_subtitles("http://example.com/sublanguageid-all/test", languages=["eng"])
        url_called = scraper._mock_session.get.call_args[0][0]
        assert "sublanguageid-eng" in url_called

    def test_response_closed(self, scraper):
        resp = _mock_response()
        scraper._mock_session.get.return_value = resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = []
        scraper.get_subtitles("http://example.com")
        resp.close.assert_called()


# ---------------------------------------------------------------------------
# download_subtitle
# ---------------------------------------------------------------------------

class TestDownloadSubtitle:

    def test_direct_download_zip(self, scraper):
        sub_info = make_subtitle_info()
        zip_bytes = b"PK\x03\x04fakecontent"
        resp = _mock_response(content=zip_bytes, headers={"content-type": "application/zip"})
        scraper._mock_session.get.return_value = resp
        scraper._mock_download_parser.extract_subtitle_from_zip.return_value = {
            "filename": "test.srt",
            "content": "1\n00:00:00,000 --> 00:00:01,000\nHello",
            "size": 100,
            "encoding": "utf-8",
        }
        scraper._mock_download_parser.validate_subtitle_content.return_value = True

        result = scraper.download_subtitle(sub_info)
        assert result["filename"] == "test.srt"
        scraper._mock_download_parser.extract_subtitle_from_zip.assert_called_once()

    def test_direct_download_not_zip_fallback(self, scraper):
        """Direct download returns HTML -> fallback to download page."""
        sub_info = make_subtitle_info(download_url="/en/download/sub/999")

        # First call: direct download returns HTML (not zip)
        html_resp = _mock_response(
            content=b"<html></html>",
            headers={"content-type": "text/html"},
        )
        # Second call: download page
        page_resp = _mock_response(text="<html>download page</html>")
        # Third call: actual download
        zip_resp = _mock_response(
            content=b"PK\x03\x04data",
            headers={"content-type": "application/zip"},
        )
        scraper._mock_session.get.side_effect = [html_resp, page_resp, zip_resp]
        scraper._mock_download_parser.parse_download_page.return_value = {
            "download_url": "http://example.com/actual.zip",
            "requires_captcha": False,
            "wait_time": 0,
        }
        scraper._mock_download_parser.extract_subtitle_from_zip.return_value = {
            "filename": "test.srt",
            "content": "subtitle content",
            "size": 50,
            "encoding": "utf-8",
        }
        scraper._mock_download_parser.validate_subtitle_content.return_value = True

        result = scraper.download_subtitle(sub_info)
        assert result["filename"] == "test.srt"

    def test_captcha_raises_download_error(self, scraper):
        sub_info = make_subtitle_info(download_url="/en/download/sub/999")
        html_resp = _mock_response(headers={"content-type": "text/html"})
        page_resp = _mock_response(text="<html>captcha</html>")
        scraper._mock_session.get.side_effect = [html_resp, page_resp]
        scraper._mock_download_parser.parse_download_page.return_value = {
            "download_url": None,
            "requires_captcha": True,
            "wait_time": 0,
        }
        with pytest.raises(DownloadError, match="Subtitle download failed"):
            scraper.download_subtitle(sub_info)

    def test_wait_time_sleep(self, scraper):
        sub_info = make_subtitle_info(download_url="/en/download/sub/999")
        html_resp = _mock_response(headers={"content-type": "text/html"})
        page_resp = _mock_response()
        zip_resp = _mock_response(
            content=b"raw subtitle data",
            headers={"content-type": "text/plain"},
        )
        scraper._mock_session.get.side_effect = [html_resp, page_resp, zip_resp]
        scraper._mock_download_parser.parse_download_page.return_value = {
            "download_url": "http://example.com/dl",
            "requires_captcha": False,
            "wait_time": 2,
        }
        scraper._mock_download_parser.validate_subtitle_content.return_value = True

        with patch("time.sleep") as mock_sleep:
            result = scraper.download_subtitle(sub_info)
            mock_sleep.assert_called_once_with(2)
        assert result["filename"] == sub_info.filename

    def test_no_download_url_raises(self, scraper):
        sub_info = make_subtitle_info(download_url=None)
        # Direct download fails
        scraper._mock_session.get.side_effect = RuntimeError("fail")
        with pytest.raises(DownloadError):
            scraper.download_subtitle(sub_info)

    def test_fallback_direct_subtitle_file(self, scraper):
        """Fallback path with non-zip response decodes content directly."""
        sub_info = make_subtitle_info(download_url="/en/download/sub/999")
        html_resp = _mock_response(headers={"content-type": "text/html"})
        page_resp = _mock_response()
        # Non-zip, non-zip-url response
        direct_resp = _mock_response(
            content=b"1\n00:00:00,000 --> 00:00:01,000\nHello",
            headers={"content-type": "text/plain"},
        )
        scraper._mock_session.get.side_effect = [html_resp, page_resp, direct_resp]
        scraper._mock_download_parser.parse_download_page.return_value = {
            "download_url": "http://example.com/sub.srt",
            "requires_captcha": False,
            "wait_time": 0,
        }
        scraper._mock_download_parser.validate_subtitle_content.return_value = True

        result = scraper.download_subtitle(sub_info)
        assert result["encoding"] == "utf-8"

    def test_fallback_latin1_decode(self, scraper):
        """Fallback decodes with latin1 when utf-8 fails."""
        sub_info = make_subtitle_info(download_url="/en/download/sub/999")
        html_resp = _mock_response(headers={"content-type": "text/html"})
        page_resp = _mock_response()
        # Bytes that are not valid utf-8
        bad_bytes = bytes([0xff, 0xfe, 0x41, 0x42])
        direct_resp = _mock_response(
            content=bad_bytes,
            headers={"content-type": "text/plain"},
        )
        scraper._mock_session.get.side_effect = [html_resp, page_resp, direct_resp]
        scraper._mock_download_parser.parse_download_page.return_value = {
            "download_url": "http://example.com/sub.srt",
            "requires_captcha": False,
            "wait_time": 0,
        }
        scraper._mock_download_parser.validate_subtitle_content.return_value = True

        result = scraper.download_subtitle(sub_info)
        # Should not raise, content decoded with latin1 fallback
        assert "filename" in result

    def test_fallback_zip_url_ending(self, scraper):
        """Fallback path: URL ending with .zip triggers zip extraction."""
        sub_info = make_subtitle_info(download_url="/en/download/sub/999")
        html_resp = _mock_response(headers={"content-type": "text/html"})
        page_resp = _mock_response()
        zip_resp = _mock_response(
            content=b"PK\x03\x04data",
            headers={"content-type": "application/octet-stream"},
        )
        scraper._mock_session.get.side_effect = [html_resp, page_resp, zip_resp]
        scraper._mock_download_parser.parse_download_page.return_value = {
            "download_url": "http://example.com/sub.zip",
            "requires_captcha": False,
            "wait_time": 0,
        }
        scraper._mock_download_parser.extract_subtitle_from_zip.return_value = {
            "filename": "test.srt",
            "content": "content",
            "size": 10,
            "encoding": "utf-8",
        }
        scraper._mock_download_parser.validate_subtitle_content.return_value = True

        result = scraper.download_subtitle(sub_info)
        scraper._mock_download_parser.extract_subtitle_from_zip.assert_called()

    def test_fallback_no_download_url_in_page(self, scraper):
        """When download_url from page is None, uses subtitle_info.download_url."""
        sub_info = make_subtitle_info(download_url="/en/download/sub/999")
        html_resp = _mock_response(headers={"content-type": "text/html"})
        page_resp = _mock_response()
        direct_resp = _mock_response(
            content=b"subtitle text",
            headers={"content-type": "text/plain"},
        )
        scraper._mock_session.get.side_effect = [html_resp, page_resp, direct_resp]
        scraper._mock_download_parser.parse_download_page.return_value = {
            "download_url": None,
            "requires_captcha": False,
            "wait_time": 0,
        }
        scraper._mock_download_parser.validate_subtitle_content.return_value = True

        result = scraper.download_subtitle(sub_info)
        # Third get call should use the subtitle_info.download_url as fallback
        assert result is not None


# ---------------------------------------------------------------------------
# close, __enter__, __exit__
# ---------------------------------------------------------------------------

class TestLifecycle:

    def test_close(self, scraper):
        scraper.close()
        scraper._mock_session.close.assert_called_once()

    def test_context_manager(self, scraper):
        assert scraper.__enter__() is scraper
        scraper.__exit__(None, None, None)
        scraper._mock_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# _get_episode_subtitles
# ---------------------------------------------------------------------------

class TestGetEpisodeSubtitles:

    SERIES_HTML = """
    <html><body>
    <table id="search_results">
      <tr><td>Movie name</td><td>#Latest</td></tr>
      <tr><td colspan="5">Season 1</td></tr>
      <tr>
        <td>1.<a href="/en/search/sublanguageid-eng/imdbid-1480055">Winter Is Coming</a></td>
        <td>41</td>
      </tr>
      <tr>
        <td>2.<a href="/en/search/sublanguageid-eng/imdbid-1668746">The Kingsroad</a></td>
        <td>28</td>
      </tr>
      <tr><td colspan="5">Season 2</td></tr>
      <tr>
        <td>1.<a href="/en/search/sublanguageid-eng/imdbid-1971833">The North Remembers</a></td>
        <td>38</td>
      </tr>
    </table>
    </body></html>
    """

    def test_finds_correct_episode(self, scraper):
        sub = make_subtitle_info()
        ep_resp = _mock_response(text="<html>episode page</html>")
        scraper._mock_session.get.return_value = ep_resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = [sub]

        subs = scraper._get_episode_subtitles(
            self.SERIES_HTML, "http://example.com", season=1, episode=2, languages=None
        )
        assert len(subs) == 1
        url_called = scraper._mock_session.get.call_args[0][0]
        assert "1668746" in url_called

    def test_episode_not_found(self, scraper):
        subs = scraper._get_episode_subtitles(
            self.SERIES_HTML, "http://example.com", season=1, episode=99, languages=None
        )
        assert subs == []

    def test_no_search_results_table(self, scraper):
        subs = scraper._get_episode_subtitles(
            "<html><body></body></html>", "http://example.com",
            season=1, episode=1, languages=None,
        )
        assert subs == []

    def test_season_2_episode_1(self, scraper):
        sub = make_subtitle_info()
        ep_resp = _mock_response(text="<html>s2e1</html>")
        scraper._mock_session.get.return_value = ep_resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = [sub]

        subs = scraper._get_episode_subtitles(
            self.SERIES_HTML, "http://example.com", season=2, episode=1, languages=None
        )
        assert len(subs) == 1
        url_called = scraper._mock_session.get.call_args[0][0]
        assert "1971833" in url_called

    def test_exception_returns_empty(self, scraper):
        scraper._mock_session.get.side_effect = RuntimeError("boom")
        subs = scraper._get_episode_subtitles(
            self.SERIES_HTML, "http://example.com", season=1, episode=1, languages=None
        )
        assert subs == []

    def test_attaches_episode_metadata(self, scraper):
        sub = make_subtitle_info()
        # Remove movie_name if present
        if hasattr(sub, 'movie_name'):
            del sub.movie_name
        ep_resp = _mock_response()
        scraper._mock_session.get.return_value = ep_resp
        scraper._mock_subtitle_parser.parse_subtitle_page.return_value = [sub]

        subs = scraper._get_episode_subtitles(
            self.SERIES_HTML, "http://example.com", season=1, episode=1, languages=None
        )
        assert hasattr(subs[0], 'movie_name')


# ===================================================================
# BASE PROVIDER TESTS (src/providers/base_provider.py)
# ===================================================================

class TestLanguage:

    def test_init_defaults(self):
        from src.providers.base_provider import Language
        lang = Language("EN")
        assert lang.code == "en"
        assert lang.name == "EN"
        assert lang.forced is False
        assert lang.hi is False

    def test_init_with_name(self):
        from src.providers.base_provider import Language
        lang = Language("en", name="English", forced=True, hi=True)
        assert lang.name == "English"
        assert lang.forced is True
        assert lang.hi is True

    def test_opensubtitles_property(self):
        from src.providers.base_provider import Language
        lang = Language("en")
        assert lang.opensubtitles == "en"

    def test_eq_same_type(self):
        from src.providers.base_provider import Language
        assert Language("en") == Language("en")
        assert Language("en") != Language("fr")

    def test_eq_string(self):
        from src.providers.base_provider import Language
        assert Language("en") == "en"
        assert Language("EN") == "en"

    def test_hash(self):
        from src.providers.base_provider import Language
        assert hash(Language("en")) == hash(Language("en"))
        s = {Language("en"), Language("en")}
        assert len(s) == 1

    def test_repr(self):
        from src.providers.base_provider import Language
        assert repr(Language("en")) == "Language('en')"

    def test_str(self):
        from src.providers.base_provider import Language
        assert str(Language("en")) == "en"


class TestVideo:

    def test_init_defaults(self):
        from src.providers.base_provider import Video
        v = Video("movie.mkv")
        assert v.name == "movie.mkv"
        assert v.size == 0
        assert v.hashes == {}
        assert v.imdb_id is None
        assert v.year is None
        assert v.title is None
        assert v.alternative_titles == []

    def test_init_with_args(self):
        from src.providers.base_provider import Video
        v = Video("movie.mkv", size=1024, hashes={"md5": "abc"})
        assert v.size == 1024
        assert v.hashes == {"md5": "abc"}

    def test_str(self):
        from src.providers.base_provider import Video
        assert str(Video("test.mkv")) == "test.mkv"


class TestEpisode:

    def test_init(self):
        from src.providers.base_provider import Episode
        ep = Episode("s01e01.mkv", series="Breaking Bad", season=1, episode=1)
        assert ep.series == "Breaking Bad"
        assert ep.season == 1
        assert ep.episode == 1
        assert ep.series_imdb_id is None
        assert ep.alternative_series == []
        assert ep.is_special is False

    def test_title_property(self):
        from src.providers.base_provider import Episode
        ep = Episode("s01e01.mkv", series="Breaking Bad", season=1, episode=1)
        assert ep.title == "Breaking Bad"

    def test_inherits_video(self):
        from src.providers.base_provider import Episode, Video
        ep = Episode("s01e01.mkv", series="BB", season=1, episode=1, size=500)
        assert isinstance(ep, Video)
        assert ep.size == 500


class TestMovie:

    def test_init(self):
        from src.providers.base_provider import Movie
        m = Movie("matrix.mkv", title="The Matrix", year=1999)
        assert m.title == "The Matrix"
        assert m.year == 1999
        assert m.alternative_titles == []

    def test_inherits_video(self):
        from src.providers.base_provider import Movie, Video
        m = Movie("m.mkv", title="T", year=2000, size=100)
        assert isinstance(m, Video)
        assert m.size == 100


class TestSubtitle:

    def test_init_defaults(self):
        from src.providers.base_provider import Subtitle, Language
        lang = Language("en")
        s = Subtitle(lang)
        assert s.language == lang
        assert s.hearing_impaired is False
        assert s.page_link is None
        assert s.content is None
        assert s.encoding == "utf-8"
        assert s.provider_name == "opensubtitles_scraper"
        assert s.subtitle_id is None
        assert s.release_info is None
        assert s.download_count == 0
        assert s.rating == 0.0
        assert s.fps is None

    def test_init_with_args(self):
        from src.providers.base_provider import Subtitle, Language
        s = Subtitle(Language("fr"), hearing_impaired=True, page_link="http://x.com")
        assert s.hearing_impaired is True
        assert s.page_link == "http://x.com"

    def test_get_matches_returns_empty_set(self):
        from src.providers.base_provider import Subtitle, Language, Video
        s = Subtitle(Language("en"))
        assert s.get_matches(Video("test.mkv")) == set()

    def test_str(self):
        from src.providers.base_provider import Subtitle, Language
        s = Subtitle(Language("en"))
        assert "[en]" in str(s)

    def test_repr(self):
        from src.providers.base_provider import Subtitle, Language
        s = Subtitle(Language("en"))
        r = repr(s)
        assert "Subtitle" in r
        assert "hearing_impaired=False" in r


class TestBaseProvider:

    def test_abstract_cannot_instantiate(self):
        from src.providers.base_provider import BaseProvider
        with pytest.raises(TypeError):
            BaseProvider()

    def test_subclass_works(self):
        from src.providers.base_provider import BaseProvider, Video, Subtitle

        class MyProvider(BaseProvider):
            def initialize(self): self.initialized = True
            def terminate(self): self.initialized = False
            def list_subtitles(self, video, languages): return []
            def download_subtitle(self, subtitle): pass

        p = MyProvider()
        assert p.initialized is False
        p.initialize()
        assert p.initialized is True

    def test_context_manager(self):
        from src.providers.base_provider import BaseProvider

        class MyProvider(BaseProvider):
            def initialize(self): self.initialized = True
            def terminate(self): self.initialized = False
            def list_subtitles(self, video, languages): return []
            def download_subtitle(self, subtitle): pass

        with MyProvider() as p:
            assert p.initialized is True
        assert p.initialized is False


# ===================================================================
# OPENSUBTITLES SCRAPER PROVIDER TESTS
# ===================================================================

class TestOpenSubtitlesScraperSubtitle:

    def _make_instance(self, **kwargs):
        from src.providers.base_provider import Language
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperSubtitle
        sub_info = make_subtitle_info(**{k: v for k, v in kwargs.items()
                                         if k in ('subtitle_id', 'language', 'filename',
                                                   'release_name', 'uploader', 'download_count',
                                                   'rating', 'hearing_impaired', 'forced', 'fps',
                                                   'download_url')})
        search_result = make_search_result(**{k: v for k, v in kwargs.items()
                                              if k in ('title', 'year', 'imdb_id', 'url',
                                                        'subtitle_count', 'kind')})
        lang = Language(sub_info.language)
        return OpenSubtitlesScraperSubtitle(
            language=lang,
            hearing_impaired=sub_info.hearing_impaired,
            page_link=sub_info.download_url or "",
            subtitle_info=sub_info,
            search_result=search_result,
        )

    def test_init(self):
        s = self._make_instance(subtitle_id="42", rating=9.5, uploader="bob")
        assert s.subtitle_id == "42"
        assert s.rating == 9.5
        assert s.uploader == "bob"

    def test_id_property(self):
        s = self._make_instance(subtitle_id="42")
        assert s.subtitle_id == "42"

    def test_release_info_property(self):
        s = self._make_instance(release_name="Test.Release.720p")
        assert s.release_info == "Test.Release.720p"

    def test_get_matches_movie_title(self):
        from src.providers.base_provider import Movie
        s = self._make_instance(title="The Matrix", year=1999, imdb_id="tt0133093")
        video = Movie("matrix.mkv", title="The Matrix", year=1999)
        video.imdb_id = "tt0133093"
        matches = s.get_matches(video)
        assert "title" in matches
        assert "year" in matches
        assert "imdb_id" in matches

    def test_get_matches_movie_no_title_match(self):
        from src.providers.base_provider import Movie
        s = self._make_instance(title="Different Movie")
        video = Movie("m.mkv", title="The Matrix")
        matches = s.get_matches(video)
        assert "title" not in matches

    def test_get_matches_episode_series(self):
        from src.providers.base_provider import Episode
        s = self._make_instance(title="Breaking Bad", kind="episode")
        video = Episode("s01e01.mkv", series="Breaking Bad", season=1, episode=1)
        matches = s.get_matches(video)
        assert "series" in matches

    def test_get_matches_episode_no_match(self):
        from src.providers.base_provider import Episode
        s = self._make_instance(title="Different Show", kind="episode")
        video = Episode("s01e01.mkv", series="Breaking Bad", season=1, episode=1)
        matches = s.get_matches(video)
        assert "series" not in matches

    def test_get_matches_release_group(self):
        from src.providers.base_provider import Movie
        s = self._make_instance(
            title="The Matrix",
            release_name="The.Matrix.1999.720p.BluRay",
        )
        video = Movie("The.Matrix.1999.720p.mkv", title="The Matrix")
        matches = s.get_matches(video)
        assert "release_group" in matches

    def test_get_matches_year_no_match(self):
        from src.providers.base_provider import Movie
        s = self._make_instance(title="The Matrix", year=1999)
        video = Movie("m.mkv", title="The Matrix", year=2020)
        video.imdb_id = None
        matches = s.get_matches(video)
        assert "year" not in matches


class TestOpenSubtitlesScraperProvider:

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_init_default(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider()
        assert p.timeout == 30
        assert p.scraper is None
        assert p.initialized is False

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_init_custom_timeout(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider(timeout=60)
        assert p.timeout == 60

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_initialize(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider()
        p.initialize()
        MockScraper.assert_called_once_with(timeout=30)
        assert p.initialized is True
        assert p.scraper is not None

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_terminate(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider()
        p.initialize()
        mock_scraper = p.scraper
        p.terminate()
        mock_scraper.close.assert_called_once()
        assert p.scraper is None
        assert p.initialized is False

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_terminate_when_no_scraper(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider()
        p.terminate()  # Should not raise
        assert p.initialized is False

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_list_subtitles_not_initialized(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        from src.providers.base_provider import Movie, Language
        p = OpenSubtitlesScraperProvider()
        video = Movie("m.mkv", title="T")
        with pytest.raises(ScrapingError, match="not initialized"):
            p.list_subtitles(video, {Language("en")})

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_list_subtitles_movie(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        from src.providers.base_provider import Movie, Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()

        search_result = make_search_result(title="The Matrix")
        sub_info = make_subtitle_info(language="en")
        p.scraper.search_movies.return_value = [search_result]
        p.scraper.get_movie_url.return_value = "http://example.com/subtitles/123"
        p.scraper.get_subtitles.return_value = [sub_info]

        video = Movie("m.mkv", title="The Matrix", year=1999)
        video.imdb_id = "tt0133093"
        langs = {Language("en")}
        subs = p.list_subtitles(video, langs)
        assert len(subs) == 1
        p.scraper.search_movies.assert_called_once()

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_list_subtitles_episode(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        from src.providers.base_provider import Episode, Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()

        search_result = make_search_result(title="Breaking Bad", kind="episode")
        sub_info = make_subtitle_info(language="en")
        p.scraper.search_tv_shows.return_value = [search_result]
        p.scraper.get_movie_url.return_value = "http://example.com/subtitles/456"
        p.scraper.get_subtitles.return_value = [sub_info]

        video = Episode("s01e01.mkv", series="Breaking Bad", season=1, episode=1)
        langs = {Language("en")}
        subs = p.list_subtitles(video, langs)
        assert len(subs) == 1
        p.scraper.search_tv_shows.assert_called_once()

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_list_subtitles_no_results(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        from src.providers.base_provider import Movie, Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()
        p.scraper.search_movies.return_value = []

        video = Movie("m.mkv", title="Nonexistent")
        subs = p.list_subtitles(video, {Language("en")})
        assert subs == []

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_list_subtitles_unsupported_language(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        from src.providers.base_provider import Movie, Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()

        search_result = make_search_result()
        sub_info = make_subtitle_info(language="xx_unknown")
        p.scraper.search_movies.return_value = [search_result]
        p.scraper.get_movie_url.return_value = "http://example.com"
        p.scraper.get_subtitles.return_value = [sub_info]

        video = Movie("m.mkv", title="T")
        subs = p.list_subtitles(video, {Language("en")})
        assert subs == []

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_list_subtitles_language_not_requested(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        from src.providers.base_provider import Movie, Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()

        search_result = make_search_result()
        sub_info = make_subtitle_info(language="fr")
        p.scraper.search_movies.return_value = [search_result]
        p.scraper.get_movie_url.return_value = "http://example.com"
        p.scraper.get_subtitles.return_value = [sub_info]

        video = Movie("m.mkv", title="T")
        # Only requesting English, but sub is French
        subs = p.list_subtitles(video, {Language("en")})
        assert subs == []

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_download_subtitle(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import (
            OpenSubtitlesScraperProvider, OpenSubtitlesScraperSubtitle,
        )
        from src.providers.base_provider import Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()

        sub_info = make_subtitle_info()
        search_result = make_search_result()
        subtitle = OpenSubtitlesScraperSubtitle(
            language=Language("en"),
            hearing_impaired=False,
            page_link="http://example.com",
            subtitle_info=sub_info,
            search_result=search_result,
        )

        p.scraper.download_subtitle.return_value = {
            "filename": "test.srt",
            "content": "1\n00:00:00,000 --> 00:00:01,000\nHi",
            "encoding": "utf-8",
        }

        p.download_subtitle(subtitle)
        assert subtitle.content is not None
        assert subtitle.encoding == "utf-8"
        p.scraper.download_subtitle.assert_called_once_with(sub_info)

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_download_subtitle_not_initialized(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import (
            OpenSubtitlesScraperProvider, OpenSubtitlesScraperSubtitle,
        )
        from src.providers.base_provider import Language

        p = OpenSubtitlesScraperProvider()
        sub_info = make_subtitle_info()
        search_result = make_search_result()
        subtitle = OpenSubtitlesScraperSubtitle(
            language=Language("en"),
            hearing_impaired=False,
            page_link="",
            subtitle_info=sub_info,
            search_result=search_result,
        )
        with pytest.raises(DownloadError, match="not initialized"):
            p.download_subtitle(subtitle)

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_download_subtitle_exception(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import (
            OpenSubtitlesScraperProvider, OpenSubtitlesScraperSubtitle,
        )
        from src.providers.base_provider import Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()

        sub_info = make_subtitle_info()
        search_result = make_search_result()
        subtitle = OpenSubtitlesScraperSubtitle(
            language=Language("en"),
            hearing_impaired=False,
            page_link="",
            subtitle_info=sub_info,
            search_result=search_result,
        )
        p.scraper.download_subtitle.side_effect = RuntimeError("fail")
        with pytest.raises(DownloadError, match="Subtitle download failed"):
            p.download_subtitle(subtitle)

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_get_language_by_code(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider()
        lang = p._get_language_by_code("en")
        assert lang is not None
        assert lang.code == "en"

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_get_language_by_code_not_found(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider()
        lang = p._get_language_by_code("xx_nonexistent")
        assert lang is None

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_get_language_by_code_case_insensitive(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        p = OpenSubtitlesScraperProvider()
        lang = p._get_language_by_code("EN")
        assert lang is not None
        assert lang.code == "en"

    @patch("src.providers.opensubtitles_scraper_provider.OpenSubtitlesScraper")
    def test_list_subtitles_exception_wraps(self, MockScraper):
        from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
        from src.providers.base_provider import Movie, Language

        p = OpenSubtitlesScraperProvider()
        p.initialize()
        p.scraper.search_movies.side_effect = RuntimeError("boom")

        video = Movie("m.mkv", title="T")
        with pytest.raises(ScrapingError, match="Subtitle listing failed"):
            p.list_subtitles(video, {Language("en")})
