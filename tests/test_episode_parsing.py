"""Tests for TV episode search parsing and subtitle_parser episode detection."""

import re
import pytest
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup

from src.core.scraper import OpenSubtitlesScraper
from src.parsers.subtitle_parser import SubtitleParser


# ---------------------------------------------------------------------------
# Minimal HTML fixtures
# ---------------------------------------------------------------------------

SERIES_OVERVIEW_HTML = """
<html><body>
<table id="search_results">
  <tr><td>Movie name</td><td>#Latest</td></tr>
  <tr><td colspan="5">Season 1</td></tr>
  <tr><td>0.<br/>Game of Thrones: Unaired Pilot</td><td></td><td></td><td></td><td></td></tr>
  <tr>
    <td>1.<a href="/en/search/sublanguageid-eng/imdbid-1480055">Winter Is Coming</a></td>
    <td>41</td><td>8897640x</td><td>8.9</td><td>09/05/25</td>
  </tr>
  <tr>
    <td>2.<a href="/en/search/sublanguageid-eng/imdbid-1668746">The Kingsroad</a></td>
    <td>28</td><td>6455460x</td><td>8.6</td><td>09/05/25</td>
  </tr>
  <tr>
    <td>3.<a href="/en/search/sublanguageid-eng/imdbid-1829962">Lord Snow</a></td>
    <td>44</td><td>6085420x</td><td>8.5</td><td>01/07/24</td>
  </tr>
  <tr><td colspan="5">Season 2</td></tr>
  <tr>
    <td>1.<a href="/en/search/sublanguageid-eng/imdbid-1971833">The North Remembers</a></td>
    <td>38</td><td>5615949x</td><td>8.6</td><td>03/08/25</td>
  </tr>
  <tr>
    <td>2.<a href="/en/search/sublanguageid-eng/imdbid-2069318">The Night Lands</a></td>
    <td>36</td><td>4346275x</td><td>8.3</td><td>03/08/25</td>
  </tr>
</table>
</body></html>
"""

SUBTITLE_LISTING_HTML = """
<html><body>
<table id="search_results">
  <tr><th>Subtitle</th><th>Lang</th><th>Downloads</th></tr>
  <tr>
    <td id="main13091386">
      <strong><a href="/en/subtitles/13091386/got-winter-is-coming-en">
        "Game of Thrones" Winter Is Coming (2011)
      </a></strong><br/>
      Game.of.Thrones.S01E01.720p.BluRay
    </td>
    <td><a href="/en/profile/hanspete">hanspete</a></td>
    <td><a href="/subtitleserve/sub/13091386">1340x</a></td>
    <td><span class="p">23.976</span></td>
  </tr>
  <tr>
    <td id="main10744048">
      <strong><a href="/en/subtitles/10744048/got-winter-is-coming-en">
        "Game of Thrones" Winter Is Coming (2011)
      </a></strong><br/>
      Game.of.Thrones.S01E01.1080p.WEB-DL
    </td>
    <td><a href="/en/profile/r3ps4j">r3ps4j</a></td>
    <td><a href="/subtitleserve/sub/10744048">1147x</a></td>
    <td></td>
  </tr>
</table>
</body></html>
"""

# A page that has imdbid links AND S01E01 text, but IS a subtitle listing
# (has td[id^=main] cells). This is what was misdetected before the fix.
EPISODE_SUBTITLE_PAGE_HTML = """
<html><body>
<table id="search_results">
  <tr>
    <td id="main9999">
      <strong><a href="/en/subtitles/9999/show-s01e01-en">
        "Show" S01E01 Pilot (2020)
      </a></strong>
    </td>
  </tr>
</table>
<!-- sidebar with related episode links -->
<a href="/en/search/sublanguageid-eng/imdbid-111">Ep1</a>
<a href="/en/search/sublanguageid-eng/imdbid-222">Ep2</a>
<a href="/en/search/sublanguageid-eng/imdbid-333">Ep3</a>
<a href="/en/search/sublanguageid-eng/imdbid-444">Ep4</a>
</body></html>
"""

# A page with NO subtitles and NO season headers
MOVIE_SEARCH_RESULTS_HTML = """
<html><body>
<table id="search_results">
  <tr><td>Some Movie (2020)</td></tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Tests for SubtitleParser._is_episode_list_page
# ---------------------------------------------------------------------------

class TestIsEpisodeListPage:
    """Tests for the _is_episode_list_page heuristic."""

    def _make_parser(self):
        return SubtitleParser()

    def test_series_overview_is_episode_list(self):
        parser = self._make_parser()
        soup = BeautifulSoup(SERIES_OVERVIEW_HTML, "html.parser")
        assert parser._is_episode_list_page(soup) is True

    def test_subtitle_listing_is_not_episode_list(self):
        """A page with td[id^=main] cells is a subtitle listing, NOT an episode list."""
        parser = self._make_parser()
        soup = BeautifulSoup(SUBTITLE_LISTING_HTML, "html.parser")
        assert parser._is_episode_list_page(soup) is False

    def test_episode_subtitle_page_with_sidebar_links_is_not_episode_list(self):
        """Page with imdbid links and S01E01 text but td[id^=main] -> NOT episode list."""
        parser = self._make_parser()
        soup = BeautifulSoup(EPISODE_SUBTITLE_PAGE_HTML, "html.parser")
        assert parser._is_episode_list_page(soup) is False

    def test_movie_search_results_is_not_episode_list(self):
        parser = self._make_parser()
        soup = BeautifulSoup(MOVIE_SEARCH_RESULTS_HTML, "html.parser")
        assert parser._is_episode_list_page(soup) is False

    def test_empty_html_is_not_episode_list(self):
        parser = self._make_parser()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        assert parser._is_episode_list_page(soup) is False


# ---------------------------------------------------------------------------
# Tests for OpenSubtitlesScraper._get_episode_subtitles
# ---------------------------------------------------------------------------

class TestGetEpisodeSubtitles:
    """Tests for the rewritten _get_episode_subtitles method."""

    def _make_scraper(self):
        """Create a scraper with mocked session manager."""
        scraper = OpenSubtitlesScraper.__new__(OpenSubtitlesScraper)
        scraper.session_manager = MagicMock()
        scraper.search_parser = MagicMock()
        scraper.subtitle_parser = SubtitleParser()
        scraper.download_parser = MagicMock()
        scraper.imdb_lookup = MagicMock()
        scraper.base_url = "https://www.opensubtitles.org"
        return scraper

    def test_finds_season1_episode1(self):
        scraper = self._make_scraper()

        # Mock the episode page fetch to return subtitle listing HTML
        mock_resp = MagicMock()
        mock_resp.text = SUBTITLE_LISTING_HTML
        scraper.session_manager.get.return_value = mock_resp

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=1, episode=1
        )

        # Should have fetched the episode URL
        scraper.session_manager.get.assert_called_once()
        call_url = scraper.session_manager.get.call_args[0][0]
        assert "imdbid-1480055" in call_url

        # Should have parsed subtitles from the episode page
        assert len(subtitles) == 2
        assert subtitles[0].subtitle_id == "13091386"
        assert subtitles[1].subtitle_id == "10744048"

    def test_finds_season1_episode3(self):
        scraper = self._make_scraper()

        mock_resp = MagicMock()
        mock_resp.text = SUBTITLE_LISTING_HTML
        scraper.session_manager.get.return_value = mock_resp

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=1, episode=3
        )

        call_url = scraper.session_manager.get.call_args[0][0]
        assert "imdbid-1829962" in call_url  # Lord Snow

    def test_finds_season2_episode1(self):
        scraper = self._make_scraper()

        mock_resp = MagicMock()
        mock_resp.text = SUBTITLE_LISTING_HTML
        scraper.session_manager.get.return_value = mock_resp

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=2, episode=1
        )

        call_url = scraper.session_manager.get.call_args[0][0]
        assert "imdbid-1971833" in call_url  # The North Remembers

    def test_finds_season2_episode2(self):
        scraper = self._make_scraper()

        mock_resp = MagicMock()
        mock_resp.text = SUBTITLE_LISTING_HTML
        scraper.session_manager.get.return_value = mock_resp

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=2, episode=2
        )

        call_url = scraper.session_manager.get.call_args[0][0]
        assert "imdbid-2069318" in call_url  # The Night Lands

    def test_returns_empty_for_nonexistent_episode(self):
        scraper = self._make_scraper()

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=1, episode=99
        )

        assert subtitles == []
        scraper.session_manager.get.assert_not_called()

    def test_returns_empty_for_nonexistent_season(self):
        scraper = self._make_scraper()

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=5, episode=1
        )

        assert subtitles == []
        scraper.session_manager.get.assert_not_called()

    def test_returns_empty_for_no_search_results_table(self):
        scraper = self._make_scraper()

        subtitles = scraper._get_episode_subtitles(
            "<html><body>No table here</body></html>",
            "https://example.com/series", season=1, episode=1
        )

        assert subtitles == []

    def test_skips_row_without_episode_number(self):
        """Row 2 in the fixture (unaired pilot) has '0.' prefix, should be skippable."""
        scraper = self._make_scraper()

        mock_resp = MagicMock()
        mock_resp.text = SUBTITLE_LISTING_HTML
        scraper.session_manager.get.return_value = mock_resp

        # Episode 0 should not match any numbered episode (our fixture has 0. but no link)
        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=1, episode=0
        )

        # The unaired pilot row has no episode link, so it should return empty
        assert subtitles == []

    def test_sets_movie_name_on_subtitles(self):
        """Subtitles should have movie_name set to the episode title."""
        scraper = self._make_scraper()

        mock_resp = MagicMock()
        mock_resp.text = SUBTITLE_LISTING_HTML
        scraper.session_manager.get.return_value = mock_resp

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=1, episode=1
        )

        for sub in subtitles:
            assert sub.movie_name == "Winter Is Coming"

    def test_handles_session_error_gracefully(self):
        scraper = self._make_scraper()
        scraper.session_manager.get.side_effect = Exception("Connection failed")

        subtitles = scraper._get_episode_subtitles(
            SERIES_OVERVIEW_HTML, "https://example.com/series", season=1, episode=1
        )

        assert subtitles == []
