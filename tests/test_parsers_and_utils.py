"""Comprehensive tests for parsers and utility modules."""

import io
import re
import time
import zipfile
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from bs4 import BeautifulSoup

from src.parsers.download_parser import DownloadParser, MAX_SUBTITLE_SIZE
from src.parsers.search_parser import SearchParser, SearchResult
from src.parsers.subtitle_parser import SubtitleParser, SubtitleInfo
from src.utils.helpers import (
    sanitize_filename,
    extract_imdb_id,
    normalize_title,
    build_url,
    extract_subtitle_info,
)
from src.utils.imdb_lookup import IMDBLookupService, MAX_CACHE_SIZE
from src.utils.url_validator import validate_target_url, ALLOWED_HOSTS
from src.utils.exceptions import DownloadError, ParseError


# ===========================================================================
# Helper to build in-memory ZIP files
# ===========================================================================

def _make_zip(files: dict) -> bytes:
    """Create an in-memory ZIP with {filename: content_bytes}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_zip_with_size(filename: str, size: int) -> bytes:
    """Create a ZIP where the uncompressed file size is *size* bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(filename, b"x" * size)
    return buf.getvalue()


# ===========================================================================
# DownloadParser tests
# ===========================================================================

class TestDownloadParserExtractDownloadLink:
    def setup_method(self):
        self.parser = DownloadParser()

    # Pattern 1: dl.opensubtitles.org/en/download/sub/ID
    def test_pattern1_dl_sub(self):
        html = '<html><body><a href="https://dl.opensubtitles.org/en/download/sub/12345">Download</a></body></html>'
        assert self.parser.extract_download_link(html, "12345") == "https://dl.opensubtitles.org/en/download/sub/12345"

    # Pattern 2: dl.opensubtitles.org/en/download/file/ID
    def test_pattern2_dl_file(self):
        html = '<html><body><a href="https://dl.opensubtitles.org/en/download/file/99999">Download</a></body></html>'
        assert self.parser.extract_download_link(html, "99999") == "https://dl.opensubtitles.org/en/download/file/99999"

    # Pattern 3: subtitleserve in script
    def test_pattern3_subtitleserve_script(self):
        html = '''<html><body>
        <script>var url="/en/subtitleserve/sub/55555";</script>
        </body></html>'''
        result = self.parser.extract_download_link(html, "55555")
        assert result == "https://www.opensubtitles.org/en/subtitleserve/sub/55555"

    def test_pattern3_script_no_string(self):
        # Script with no text content should be skipped gracefully
        html = '<html><body><script src="external.js"></script></body></html>'
        result = self.parser.extract_download_link(html, "123")
        assert result is None

    # Pattern 4: download link containing subtitle_id
    def test_pattern4_download_link_with_id_absolute(self):
        html = '<html><body><a href="https://example.com/download/sub12345">Get</a></body></html>'
        assert self.parser.extract_download_link(html, "12345") == "https://example.com/download/sub12345"

    def test_pattern4_download_link_with_id_relative(self):
        html = '<html><body><a href="/download/sub12345">Get</a></body></html>'
        assert self.parser.extract_download_link(html, "12345") == "https://www.opensubtitles.org/download/sub12345"

    # Pattern 5: generic .zip link
    def test_pattern5_zip_link_absolute(self):
        html = '<html><body><a href="https://example.com/file.zip">Download ZIP</a></body></html>'
        assert self.parser.extract_download_link(html, "x") == "https://example.com/file.zip"

    def test_pattern5_zip_link_relative(self):
        html = '<html><body><a href="/files/sub.zip">Download ZIP</a></body></html>'
        assert self.parser.extract_download_link(html, "x") == "https://www.opensubtitles.org/files/sub.zip"

    # Pattern 6: form-based download
    def test_pattern6_download_form_absolute(self):
        html = '<html><body><form action="https://example.com/download"><input/></form></body></html>'
        assert self.parser.extract_download_link(html, "x") == "https://example.com/download"

    def test_pattern6_download_form_relative(self):
        html = '<html><body><form action="/download"><input/></form></body></html>'
        assert self.parser.extract_download_link(html, "x") == "https://www.opensubtitles.org/download"

    # Pattern 7: meta refresh
    def test_pattern7_meta_refresh_absolute(self):
        html = '<html><head><meta http-equiv="refresh" content="5;url=https://example.com/dl"></head><body></body></html>'
        assert self.parser.extract_download_link(html, "x") == "https://example.com/dl"

    def test_pattern7_meta_refresh_relative(self):
        html = '<html><head><meta http-equiv="refresh" content="5;url=/dl/file"></head><body></body></html>'
        assert self.parser.extract_download_link(html, "x") == "https://www.opensubtitles.org/dl/file"

    def test_no_link_found(self):
        html = '<html><body><p>No links here</p></body></html>'
        assert self.parser.extract_download_link(html, "123") is None

    def test_invalid_html_raises_parse_error(self):
        # Passing something that triggers an internal exception
        with patch("src.parsers.download_parser.BeautifulSoup", side_effect=Exception("boom")):
            with pytest.raises(ParseError):
                self.parser.extract_download_link("<html></html>", "1")


class TestDownloadParserParseDownloadPage:
    def setup_method(self):
        self.parser = DownloadParser()

    def test_extracts_filename_srt(self):
        html = '<html><body><p>movie.srt - 45 KB</p></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["filename"] == "movie.srt"

    def test_extracts_size_kb(self):
        html = '<html><body><p>movie.srt - 45 KB</p></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["size"] == 45 * 1024

    def test_extracts_size_mb(self):
        html = '<html><body><p>movie.sub 2.5 MB</p></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["size"] == int(2.5 * 1024 * 1024)

    def test_extracts_size_bytes(self):
        html = '<html><body><p>movie.ass 512 bytes</p></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["size"] == 512

    def test_detects_captcha(self):
        html = '<html><body><div class="captcha-box">solve me</div></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["requires_captcha"] is True

    def test_no_captcha(self):
        html = '<html><body><p>no captcha</p></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["requires_captcha"] is False

    def test_detects_wait_time(self):
        html = '<html><body><p>Please wait 30 seconds before downloading</p></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["wait_time"] == 30

    def test_no_wait_time(self):
        html = '<html><body><p>Download now</p></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["wait_time"] == 0

    def test_default_values(self):
        html = '<html><body></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["filename"] is None
        assert info["size"] is None
        assert info["requires_captcha"] is False
        assert info["wait_time"] == 0

    def test_extracts_download_url(self):
        html = '<html><body><a href="https://dl.opensubtitles.org/en/download/sub/111">DL</a></body></html>'
        info = self.parser.parse_download_page(html)
        assert info["download_url"] == "https://dl.opensubtitles.org/en/download/sub/111"


class TestExtractSubtitleFromZip:
    def setup_method(self):
        self.parser = DownloadParser()

    def test_single_srt(self):
        content = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        zdata = _make_zip({"movie.srt": content})
        result = self.parser.extract_subtitle_from_zip(zdata)
        assert result["filename"] == "movie.srt"
        assert "Hello" in result["content"]
        assert result["size"] == len(content)

    def test_prefers_matching_filename(self):
        zdata = _make_zip({
            "a.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
            "b.srt": b"1\n00:00:01,000 --> 00:00:02,000\nBBBBBBBBBB\n",
        })
        result = self.parser.extract_subtitle_from_zip(zdata, preferred_filename="a")
        assert result["filename"] == "a.srt"

    def test_prefers_srt_over_other(self):
        zdata = _make_zip({
            "movie.ass": b"[Script Info]\nTitle: Test\n",
            "movie.srt": b"1\n00:00:01,000 --> 00:00:02,000\nHello\n",
        })
        result = self.parser.extract_subtitle_from_zip(zdata)
        assert result["filename"] == "movie.srt"

    def test_largest_srt_selected(self):
        small = b"1\n00:00:01,000 --> 00:00:02,000\nA\n"
        large = b"1\n00:00:01,000 --> 00:00:02,000\nAAAAAAAAAAAAAAAAAAAA\n"
        zdata = _make_zip({"small.srt": small, "large.srt": large})
        result = self.parser.extract_subtitle_from_zip(zdata)
        assert result["filename"] == "large.srt"

    def test_fallback_largest_non_srt(self):
        small_ass = b"[Script Info]\nA\n"
        large_ass = b"[Script Info]\n" + b"A" * 100 + b"\n"
        zdata = _make_zip({"small.ass": small_ass, "large.ass": large_ass})
        result = self.parser.extract_subtitle_from_zip(zdata)
        assert result["filename"] == "large.ass"

    def test_skips_directories(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("subdir/", "")
            zf.writestr("subdir/movie.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        result = self.parser.extract_subtitle_from_zip(buf.getvalue())
        assert "movie.srt" in result["filename"]

    def test_skips_non_subtitle_files(self):
        zdata = _make_zip({
            "readme.txt": b"Read me",
            "movie.srt": b"1\n00:00:01,000 --> 00:00:02,000\nHello\n",
        })
        result = self.parser.extract_subtitle_from_zip(zdata)
        assert result["filename"] == "movie.srt"

    def test_no_subtitle_files_raises(self):
        zdata = _make_zip({"readme.txt": b"nothing"})
        with pytest.raises(DownloadError, match="No subtitle files"):
            self.parser.extract_subtitle_from_zip(zdata)

    def test_bad_zip_raises(self):
        with pytest.raises(DownloadError, match="Invalid ZIP"):
            self.parser.extract_subtitle_from_zip(b"not a zip file")

    def test_max_subtitle_size_skipped(self):
        # Create ZIP with a file that exceeds MAX_SUBTITLE_SIZE
        oversized = _make_zip_with_size("huge.srt", MAX_SUBTITLE_SIZE + 1)
        with pytest.raises(DownloadError, match="No subtitle files"):
            self.parser.extract_subtitle_from_zip(oversized)

    def test_vtt_and_ssa_extensions(self):
        zdata = _make_zip({
            "sub.vtt": b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n",
            "sub.ssa": b"[Script Info]\nTitle: Test\n",
        })
        result = self.parser.extract_subtitle_from_zip(zdata)
        # Should pick the largest
        assert result["filename"] in ("sub.vtt", "sub.ssa")


class TestDecodeSubtitleContent:
    def setup_method(self):
        self.parser = DownloadParser()

    def test_utf8(self):
        text = "Hello World\n"
        result = self.parser._decode_subtitle_content(text.encode("utf-8"))
        assert "Hello World" in result

    def test_utf8_sig(self):
        text = "\ufeffHello BOM"
        result = self.parser._decode_subtitle_content(text.encode("utf-8-sig"))
        # _decode returns raw decoded text; BOM stripping is in _normalize
        assert "Hello BOM" in result

    def test_latin1(self):
        # Characters valid in latin1 but potentially invalid utf-8 sequences
        text = "caf\xe9"
        content = text.encode("latin1")
        result = self.parser._decode_subtitle_content(content)
        assert "caf" in result

    def test_fallback_replace(self):
        # Construct bytes that fail all standard encodings except fallback
        # This is hard to do since latin1 accepts everything. Mock instead.
        with patch.object(self.parser, "_normalize_subtitle_content", side_effect=lambda x: x):
            # latin1 will always succeed, so this always decodes fine
            result = self.parser._decode_subtitle_content(b"hello")
            assert result == "hello"


class TestNormalizeSubtitleContent:
    def setup_method(self):
        self.parser = DownloadParser()

    def test_crlf_to_lf(self):
        result = self.parser._normalize_subtitle_content("line1\r\nline2\r\n")
        assert "\r" not in result
        assert "line1\nline2" in result

    def test_cr_to_lf(self):
        result = self.parser._normalize_subtitle_content("line1\rline2\r")
        assert "\r" not in result

    def test_bom_removal(self):
        result = self.parser._normalize_subtitle_content("\ufeffHello")
        assert not result.startswith("\ufeff")

    def test_ends_with_newline(self):
        result = self.parser._normalize_subtitle_content("Hello")
        assert result.endswith("\n")

    def test_empty_lines_collapsed(self):
        result = self.parser._normalize_subtitle_content("a\n\n\n\nb")
        # Should only have one blank line between a and b
        assert "a\n\nb\n" == result

    def test_empty_content(self):
        result = self.parser._normalize_subtitle_content("")
        assert result == ""


class TestDetectEncoding:
    def setup_method(self):
        self.parser = DownloadParser()

    def test_utf8_bom(self):
        assert self.parser._detect_encoding(b"\xef\xbb\xbfHello") == "utf-8-sig"

    def test_utf16_le_bom(self):
        assert self.parser._detect_encoding(b"\xff\xfeHello") == "utf-16-le"

    def test_utf16_be_bom(self):
        assert self.parser._detect_encoding(b"\xfe\xffHello") == "utf-16-be"

    def test_plain_utf8(self):
        assert self.parser._detect_encoding(b"Hello world") == "utf-8"

    def test_default_fallback(self):
        # latin1 accepts all byte sequences so it always succeeds
        # Just verify valid content returns something
        assert self.parser._detect_encoding(b"\x80\x81") in ("utf-8", "latin1", "cp1252", "iso-8859-1")


class TestSelectBestSubtitle:
    def setup_method(self):
        self.parser = DownloadParser()

    def test_single_file(self):
        files = [{"filename": "a.srt", "size": 100}]
        assert self.parser._select_best_subtitle(files) == files[0]

    def test_preferred_filename(self):
        files = [
            {"filename": "a.srt", "size": 100},
            {"filename": "b.srt", "size": 200},
        ]
        result = self.parser._select_best_subtitle(files, "a")
        assert result["filename"] == "a.srt"

    def test_prefers_srt(self):
        files = [
            {"filename": "movie.ass", "size": 500},
            {"filename": "movie.srt", "size": 100},
        ]
        result = self.parser._select_best_subtitle(files)
        assert result["filename"] == "movie.srt"

    def test_largest_srt(self):
        files = [
            {"filename": "small.srt", "size": 100},
            {"filename": "large.srt", "size": 500},
        ]
        result = self.parser._select_best_subtitle(files)
        assert result["filename"] == "large.srt"

    def test_largest_non_srt_fallback(self):
        files = [
            {"filename": "small.ass", "size": 100},
            {"filename": "large.ass", "size": 500},
        ]
        result = self.parser._select_best_subtitle(files)
        assert result["filename"] == "large.ass"


class TestValidateSubtitleContent:
    def setup_method(self):
        self.parser = DownloadParser()

    def test_valid_srt(self):
        content = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        assert self.parser.validate_subtitle_content(content) is True

    def test_valid_ass(self):
        content = "[Script Info]\nTitle: Test\n[V4+ Styles]\nStyle: Default"
        assert self.parser.validate_subtitle_content(content) is True

    def test_valid_vtt(self):
        content = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n"
        assert self.parser.validate_subtitle_content(content) is True

    def test_valid_generic_timestamp(self):
        content = "Some format\n01:02:03 text here\n"
        assert self.parser.validate_subtitle_content(content) is True

    def test_invalid_content(self):
        content = "This is just plain text with no timestamps or markers."
        assert self.parser.validate_subtitle_content(content) is False

    def test_empty_content(self):
        assert self.parser.validate_subtitle_content("") is False

    def test_v4_styles_only(self):
        content = "Some header\n[V4+ Styles]\nStyle: Default"
        assert self.parser.validate_subtitle_content(content) is True


# ===========================================================================
# SearchParser tests
# ===========================================================================

SEARCH_RESULTS_HTML = """
<html><body>
<table id="search_results">
  <tr><th>Poster</th><th>Title</th><th>IMDB</th><th>Subs</th><th>Date</th></tr>
  <tr>
    <td><img src="poster.jpg"/></td>
    <td><a class="bnone" href="/en/movies/idmovies-12345">Inception (2010)</a></td>
    <td><a href="https://www.imdb.com/title/tt1375666/">8.8</a></td>
    <td>42</td>
    <td>2024-01-01</td>
  </tr>
  <tr>
    <td><img src="poster2.jpg"/></td>
    <td>
      <a class="bnone" href="/en/movies/idmovies-67890">"Breaking Bad" Pilot (2008)</a>
      <img title="TV Series"/>
    </td>
    <td><a href="https://www.imdb.com/title/tt0903747/">9.5</a></td>
    <td>100</td>
    <td>2024-02-01</td>
  </tr>
</table>
</body></html>
"""

SEARCH_NO_TABLE_HTML = "<html><body><p>No results</p></body></html>"


class TestSearchParserAutocomplete:
    def setup_method(self):
        self.parser = SearchParser()

    def test_parses_results(self):
        results = self.parser.parse_search_autocomplete(SEARCH_RESULTS_HTML)
        assert len(results) == 2

    def test_first_result_fields(self):
        results = self.parser.parse_search_autocomplete(SEARCH_RESULTS_HTML)
        r = results[0]
        assert r.title == "Inception"
        assert r.year == 2010
        assert r.imdb_id == "tt1375666"
        assert r.url == "https://www.opensubtitles.org/en/movies/idmovies-12345"
        assert r.subtitle_count == 42
        assert r.kind == "movie"

    def test_tv_series_detection(self):
        results = self.parser.parse_search_autocomplete(SEARCH_RESULTS_HTML)
        r = results[1]
        assert r.kind == "episode"
        # Quotes removed from TV title
        assert r.title == "Breaking Bad"

    def test_no_table_returns_empty(self):
        results = self.parser.parse_search_autocomplete(SEARCH_NO_TABLE_HTML)
        assert results == []

    def test_parse_search_page_delegates(self):
        results = self.parser.parse_search_page(SEARCH_RESULTS_HTML)
        assert len(results) == 2


class TestSearchResultRow:
    def setup_method(self):
        self.parser = SearchParser()

    def test_row_too_few_cells(self):
        html = '<tr><td>only one</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._parse_search_result_row(row) is None

    def test_ad_row_skipped(self):
        html = '<tr><td class="ads">Ad</td><td>x</td><td>x</td><td>x</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._parse_search_result_row(row) is None

    def test_no_title_link_returns_none(self):
        html = '<tr><td></td><td><span>No link</span></td><td></td><td></td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._parse_search_result_row(row) is None

    def test_fallback_link(self):
        # No class="bnone" but there's a plain <a>
        html = '''<tr>
            <td></td>
            <td><a href="/en/movies/idmovies-111">Test Movie (2020)</a></td>
            <td></td><td>5</td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        result = self.parser._parse_search_result_row(row)
        assert result is not None
        assert result.title == "Test Movie"
        assert result.year == 2020

    def test_episode_indicator_s0x(self):
        html = '''<tr>
            <td></td>
            <td><a class="bnone" href="/en/movies/idmovies-222">Show [S01E01] Pilot (2019)</a></td>
            <td></td><td>10</td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        result = self.parser._parse_search_result_row(row)
        assert result.kind == "episode"
        assert result.title == "Show"

    def test_no_year(self):
        html = '''<tr>
            <td></td>
            <td><a class="bnone" href="/en/movies/idmovies-333">No Year Movie</a></td>
            <td></td><td>0</td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        result = self.parser._parse_search_result_row(row)
        assert result.year is None


class TestSearchResultToDict:
    def test_to_dict(self):
        sr = SearchResult("Test", year=2020, imdb_id="tt123", url="http://x", subtitle_count=5, kind="movie")
        d = sr.to_dict()
        assert d["title"] == "Test"
        assert d["year"] == 2020
        assert d["imdb_id"] == "tt123"
        assert d["kind"] == "movie"


class TestExtractMovieIdFromUrl:
    def setup_method(self):
        self.parser = SearchParser()

    def test_idmovies_pattern(self):
        assert self.parser.extract_movie_id_from_url("/en/movies/idmovies-123456") == "123456"

    def test_ssearch_pattern(self):
        assert self.parser.extract_movie_id_from_url("/en/ssearch/idmovies-789") == "789"

    def test_no_match(self):
        assert self.parser.extract_movie_id_from_url("/en/other/page") is None

    def test_none_safe(self):
        # If someone passes a URL that causes regex to fail
        assert self.parser.extract_movie_id_from_url("") is None


# ===========================================================================
# SubtitleParser tests
# ===========================================================================

SUBTITLE_PAGE_HTML = """
<html><body>
<table id="search_results">
  <tr><th>Subtitle</th><th>Uploader</th><th>Downloads</th></tr>
  <tr>
    <td id="main12345">
      <strong><a href="/en/subtitles/12345/inception-en">
        Inception (2010)
      </a></strong><br/>
      Inception.2010.720p.BluRay
    </td>
    <td><a href="/en/profile/user1">user1</a></td>
    <td><a href="/subtitleserve/sub/12345">500x</a></td>
    <td><span class="p">23.976</span></td>
    <td><span title="10 votes">8.5</span></td>
    <td class="date">2024-06-15</td>
  </tr>
  <tr>
    <td id="main67890">
      <strong><a href="/en/subtitles/67890/inception-hi-en">
        Inception HI (2010)
      </a></strong><br/>
      Inception.2010.1080p.BluRay.HI
    </td>
    <td><a href="/en/profile/user2">user2</a></td>
    <td><a href="/subtitleserve/sub/67890">200x</a></td>
    <td></td>
    <td class="date">2024-07-01</td>
  </tr>
</table>
</body></html>
"""


class TestSubtitleParserParseSubtitlePage:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_parses_subtitles(self):
        subs = self.parser.parse_subtitle_page(SUBTITLE_PAGE_HTML, "https://example.com/movie")
        assert len(subs) == 2

    def test_first_subtitle_fields(self):
        subs = self.parser.parse_subtitle_page(SUBTITLE_PAGE_HTML, "https://example.com/movie")
        s = subs[0]
        assert s.subtitle_id == "12345"
        assert s.language == "en"
        assert s.uploader == "user1"
        assert s.download_count == 500
        assert s.fps == 23.976
        assert s.rating == 8.5

    def test_hearing_impaired_detection(self):
        subs = self.parser.parse_subtitle_page(SUBTITLE_PAGE_HTML, "https://example.com/movie")
        s = subs[1]
        assert s.hearing_impaired is True

    def test_empty_page(self):
        html = "<html><body><p>No subtitles</p></body></html>"
        subs = self.parser.parse_subtitle_page(html, "https://example.com/movie")
        assert subs == []

    def test_episode_list_delegates(self):
        # A page with Season headers should trigger episode list parsing
        ep_html = """
        <html><body>
        <table id="search_results">
          <tr><td colspan="5">Season 1</td></tr>
          <tr><td>1.<a href="/en/search/sublanguageid-all/imdbid-111">Pilot</a></td></tr>
        </table>
        </body></html>"""
        # Without session manager it returns empty
        subs = self.parser.parse_subtitle_page(ep_html, "https://example.com/series")
        assert subs == []

    def test_subtitle_row_with_class(self):
        """Rows with class containing 'subtitle' should be picked up."""
        html = """<html><body><table>
          <tr class="subtitle-row">
            <td id="main111">
              <strong><a href="/en/subtitles/111/test-en">Test (2020)</a></strong>
            </td>
            <td></td><td></td>
          </tr>
        </table></body></html>"""
        subs = self.parser.parse_subtitle_page(html, "https://example.com")
        assert len(subs) == 1


class TestIsSubtitleRow:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_header_row(self):
        html = '<tr><th>Header</th></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._is_subtitle_row(row) is False

    def test_iframe_row(self):
        html = '<tr><td><iframe></iframe></td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._is_subtitle_row(row) is False

    def test_colspan_row(self):
        html = '<tr><td colspan="5">Ad</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._is_subtitle_row(row) is False

    def test_valid_subtitle_row(self):
        html = '''<tr>
            <td id="main123"><a href="/en/subtitles/123/test-en">Test</a></td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._is_subtitle_row(row) is True

    def test_no_subtitle_link(self):
        html = '<tr><td id="main123"><a href="/en/other/page">Test</a></td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._is_subtitle_row(row) is False

    def test_no_main_cell(self):
        html = '<tr><td><a href="/en/subtitles/123/test-en">Test</a></td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._is_subtitle_row(row) is False


class TestParseSubtitleRow:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_full_row(self):
        html = '''<tr>
            <td id="main999">
                <strong><a href="/en/subtitles/999/movie-en">Movie Title (2022)</a></strong>
                <br/>Movie.2022.720p.BluRay
            </td>
            <td><a href="/en/profile/uploader1">uploader1</a></td>
            <td><a href="/subtitleserve/sub/999">750x</a></td>
            <td><span class="p">25.000</span></td>
            <td><span title="5 votes">7.2</span></td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        sub = self.parser._parse_subtitle_row(row, "https://example.com")
        assert sub is not None
        assert sub.subtitle_id == "999"
        assert sub.language == "en"
        assert sub.uploader == "uploader1"
        assert sub.download_count == 750
        assert sub.fps == 25.0
        assert sub.rating == 7.2

    def test_no_main_cell_returns_none(self):
        html = '<tr><td>No id</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._parse_subtitle_row(row, "") is None

    def test_no_subtitle_link_returns_none(self):
        html = '<tr><td id="main100"><a href="/en/other">Text</a></td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._parse_subtitle_row(row, "") is None

    def test_forced_detection(self):
        html = '''<tr>
            <td id="main444">
                <strong><a href="/en/subtitles/444/movie-forced-en">Movie Forced (2020)</a></strong>
            </td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        sub = self.parser._parse_subtitle_row(row, "")
        assert sub.forced is True

    def test_release_name_extraction(self):
        html = '''<tr>
            <td id="main555">
                <strong><a href="/en/subtitles/555/movie-en">Movie (2020)</a></strong>
                <br/>Movie.2020.PROPER.BluRay
            </td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        sub = self.parser._parse_subtitle_row(row, "")
        assert sub.release_name == "Movie.2020.PROPER.BluRay"

    def test_upload_date_extraction(self):
        html = '''<tr>
            <td id="main666">
                <strong><a href="/en/subtitles/666/movie-en">Movie (2020)</a></strong>
            </td>
            <td class="date">2023-11-25</td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        sub = self.parser._parse_subtitle_row(row, "")
        assert sub.upload_date == datetime(2023, 11, 25)

    def test_default_language_fallback(self):
        """URL without recognizable 2-char language code at end."""
        html = '''<tr>
            <td id="main777">
                <strong><a href="/en/subtitles/777/movie-title-with-long-slug">Movie (2020)</a></strong>
            </td>
        </tr>'''
        row = BeautifulSoup(html, "html.parser").find("tr")
        sub = self.parser._parse_subtitle_row(row, "")
        # Should use fallback from _extract_language_from_url or default "en"
        assert sub.language is not None


class TestExtractSubtitleId:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_subtitles_pattern(self):
        assert self.parser._extract_subtitle_id("/en/subtitles/12345/movie-en") == "12345"

    def test_download_pattern(self):
        assert self.parser._extract_subtitle_id("/download/67890") == "67890"

    def test_id_param(self):
        assert self.parser._extract_subtitle_id("?id=111") == "111"

    def test_sub_id_param(self):
        assert self.parser._extract_subtitle_id("?sub_id=222") == "222"

    def test_subtitle_id_param(self):
        assert self.parser._extract_subtitle_id("?subtitle_id=333") == "333"

    def test_no_match(self):
        assert self.parser._extract_subtitle_id("/en/other/page") is None


class TestExtractLanguageFromUrl:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_two_char_language(self):
        assert self.parser._extract_language_from_url("https://www.opensubtitles.org/en/subtitles/123/movie-fr") == "fr"

    def test_fallback_path_language(self):
        # The /en/ in the path
        assert self.parser._extract_language_from_url("https://www.opensubtitles.org/en/subtitles/123/movie-title-long-slug") == "en"

    def test_no_language(self):
        assert self.parser._extract_language_from_url("https://example.com") is None


class TestExtractUploadDateFromRow:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_yyyy_mm_dd(self):
        html = '<tr><td class="date">2024-06-15</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._extract_upload_date_from_row(row) == datetime(2024, 6, 15)

    def test_mm_dd_yyyy(self):
        html = '<tr><td class="date">06/15/2024</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._extract_upload_date_from_row(row) == datetime(2024, 6, 15)

    def test_dd_mm_yyyy(self):
        html = '<tr><td class="date">15-06-2024</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        result = self.parser._extract_upload_date_from_row(row)
        assert result is not None

    def test_no_date_cell(self):
        html = '<tr><td>No date</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._extract_upload_date_from_row(row) is None

    def test_no_matching_date_format(self):
        html = '<tr><td class="date">invalid-date</td></tr>'
        row = BeautifulSoup(html, "html.parser").find("tr")
        assert self.parser._extract_upload_date_from_row(row) is None


class TestSubtitleInfoInit:
    def test_basic_init(self):
        si = SubtitleInfo(
            subtitle_id="1", language="en", filename="test.srt",
            release_name="Test", uploader="user1"
        )
        assert si.subtitle_id == "1"
        assert si.language == "en"
        assert si.hearing_impaired is False
        assert si.forced is False
        assert si.fps is None
        assert si.download_count == 0

    def test_language_from_filename(self):
        """If language is empty, extract from filename."""
        si = SubtitleInfo(
            subtitle_id="2", language="", filename="movie.french.srt",
            release_name="Movie", uploader="user2"
        )
        # extract_subtitle_info should detect "french" -> language set
        assert si.language is not None and si.language != ""

    def test_hi_from_filename(self):
        si = SubtitleInfo(
            subtitle_id="3", language="en", filename="movie.HI.srt",
            release_name="Movie", uploader="user3"
        )
        assert si.hearing_impaired is True

    def test_forced_from_filename(self):
        si = SubtitleInfo(
            subtitle_id="4", language="en", filename="movie.forced.srt",
            release_name="Movie", uploader="user4"
        )
        assert si.forced is True


class TestSubtitleInfoToDict:
    def test_to_dict_with_date(self):
        dt = datetime(2024, 1, 15, 12, 0, 0)
        si = SubtitleInfo(
            subtitle_id="1", language="en", filename="test.srt",
            release_name="Test", uploader="user1", upload_date=dt
        )
        d = si.to_dict()
        assert d["subtitle_id"] == "1"
        assert d["upload_date"] == dt.isoformat()
        assert d["hearing_impaired"] is False

    def test_to_dict_without_date(self):
        si = SubtitleInfo(
            subtitle_id="2", language="fr", filename="test.srt",
            release_name="Test", uploader="user1"
        )
        d = si.to_dict()
        assert d["upload_date"] is None


class TestParseEpisodeListPage:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_without_session_manager(self):
        """Without session manager, should return empty list."""
        html = """<html><body>
        <table id="search_results">
          <tr><td colspan="5">Season 1</td></tr>
          <tr><td>1.<a href="/en/search/sublanguageid-all/imdbid-111">Pilot</a></td></tr>
        </table>
        </body></html>"""
        soup = BeautifulSoup(html, "html.parser")
        subs = self.parser._parse_episode_list_page(soup, "https://example.com/series")
        assert subs == []

    def test_with_session_manager(self):
        """With session manager, should fetch episode pages."""
        ep_page_html = """<html><body><table>
          <tr>
            <td id="main100"><strong><a href="/en/subtitles/100/pilot-en">Pilot (2020)</a></strong></td>
          </tr>
        </table></body></html>"""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = ep_page_html
        mock_session.get.return_value = mock_resp

        self.parser._session_manager = mock_session

        html = """<html><body>
        <table id="search_results">
          <tr><td colspan="5">Season 1</td></tr>
          <tr><td>1.<a href="/en/search/sublanguageid-all/imdbid-111">Pilot</a></td></tr>
        </table>
        </body></html>"""
        soup = BeautifulSoup(html, "html.parser")
        subs = self.parser._parse_episode_list_page(soup, "https://example.com/series")
        # Should have called session_manager.get for the episode
        mock_session.get.assert_called_once()


class TestGetEpisodeSubtitlesSubtitleParser:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_no_session_manager(self):
        subs = self.parser._get_episode_subtitles("https://example.com/ep", "Ep1")
        assert subs == []

    def test_with_session_returns_subtitles(self):
        ep_page_html = """<html><body>
        <a href="/en/subtitles/200/show-en">Show S01E01</a>
        </body></html>"""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = ep_page_html
        mock_session.get.return_value = mock_resp

        self.parser._session_manager = mock_session
        subs = self.parser._get_episode_subtitles("https://example.com/ep", "Episode 1")
        assert len(subs) >= 1
        assert subs[0].subtitle_id == "200"

    def test_session_error_returns_empty(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("network error")
        self.parser._session_manager = mock_session
        subs = self.parser._get_episode_subtitles("https://example.com/ep", "Ep1")
        assert subs == []

    def test_response_closed_on_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "<html><body></body></html>"
        mock_session.get.return_value = mock_resp
        self.parser._session_manager = mock_session
        self.parser._get_episode_subtitles("https://example.com/ep", "Ep1")
        mock_resp.close.assert_called()


class TestParseSubtitleLink:
    def setup_method(self):
        self.parser = SubtitleParser()

    def test_basic_link(self):
        html = '''<table><tr>
            <td><a href="/en/subtitles/300/movie-fr">Movie FR</a></td>
        </tr></table>'''
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        sub = self.parser._parse_subtitle_link(link, "Episode Title", "https://example.com/ep")
        assert sub is not None
        assert sub.subtitle_id == "300"
        assert sub.language == "fr"
        assert "Episode.Title" in sub.filename

    def test_link_with_uploader(self):
        html = '''<table><tr>
            <td><a href="/en/subtitles/400/movie-en">Movie EN</a></td>
            <td><a href="/user/abc">abc</a></td>
        </tr></table>'''
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a", href=re.compile(r"/subtitles/"))
        sub = self.parser._parse_subtitle_link(link, "Ep1", "https://example.com")
        assert sub.uploader == "abc"

    def test_link_with_download_count(self):
        html = '''<table><tr>
            <td><a href="/en/subtitles/500/movie-en">Movie EN</a> 1234x</td>
        </tr></table>'''
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a", href=re.compile(r"/subtitles/"))
        sub = self.parser._parse_subtitle_link(link, "Ep1", "https://example.com")
        assert sub.download_count == 1234

    def test_no_subtitle_id_returns_none(self):
        html = '<a href="/en/other/page">Text</a>'
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        assert self.parser._parse_subtitle_link(link, "Ep1", "") is None

    def test_relative_url_made_absolute(self):
        html = '<a href="/en/subtitles/600/movie-en">Movie</a>'
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        sub = self.parser._parse_subtitle_link(link, "Ep1", "")
        assert sub.download_url.startswith("https://www.opensubtitles.org")

    def test_hearing_impaired_from_row(self):
        html = '''<table><tr>
            <td><a href="/en/subtitles/700/movie-en">Movie HI</a> hearing impaired</td>
        </tr></table>'''
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a", href=re.compile(r"/subtitles/"))
        sub = self.parser._parse_subtitle_link(link, "Ep1", "")
        assert sub.hearing_impaired is True

    def test_forced_from_row(self):
        html = '''<table><tr>
            <td><a href="/en/subtitles/800/movie-en">Movie</a> forced</td>
        </tr></table>'''
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a", href=re.compile(r"/subtitles/"))
        sub = self.parser._parse_subtitle_link(link, "Ep1", "")
        assert sub.forced is True

    def test_empty_link_text_uses_episode_title(self):
        html = '<a href="/en/subtitles/900/movie-en"></a>'
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a")
        sub = self.parser._parse_subtitle_link(link, "My Episode", "")
        assert sub.release_name == "My Episode"


# ===========================================================================
# helpers.py tests
# ===========================================================================

class TestSanitizeFilename:
    def test_removes_special_chars(self):
        assert sanitize_filename('file<>:"/\\|?*name.srt') == "file_________name.srt"

    def test_strips_leading_dots(self):
        assert sanitize_filename("...file.srt") == "file.srt"

    def test_strips_trailing_dots(self):
        assert sanitize_filename("file.srt...") == "file.srt"

    def test_strips_whitespace(self):
        assert sanitize_filename("  file.srt  ") == "file.srt"

    def test_limits_length(self):
        long_name = "a" * 300 + ".srt"
        result = sanitize_filename(long_name)
        assert len(result) <= 255

    def test_normal_filename(self):
        assert sanitize_filename("movie.en.srt") == "movie.en.srt"

    def test_path_traversal(self):
        # .. and / are replaced
        result = sanitize_filename("../../etc/passwd")
        assert "/" not in result
        assert "\\" not in result


class TestExtractImdbId:
    def test_valid_7_digit(self):
        assert extract_imdb_id("https://imdb.com/title/tt1234567/") == "tt1234567"

    def test_valid_8_digit(self):
        assert extract_imdb_id("tt12345678") == "tt12345678"

    def test_no_match(self):
        assert extract_imdb_id("no imdb id here") is None

    def test_embedded_in_text(self):
        assert extract_imdb_id("check out tt9876543 for info") == "tt9876543"

    def test_too_few_digits(self):
        assert extract_imdb_id("tt123456") is None  # only 6 digits


class TestNormalizeTitle:
    def test_lowercase(self):
        assert normalize_title("The Matrix") == "the matrix"

    def test_removes_special_chars(self):
        assert normalize_title("Spider-Man: No Way Home") == "spider man no way home"

    def test_collapses_whitespace(self):
        result = normalize_title("The   Lord  of   the   Rings")
        assert "  " not in result

    def test_strips(self):
        assert normalize_title("  Hello  ") == "hello"



class TestBuildUrl:
    def test_simple(self):
        assert build_url("https://example.com", "/api/test") == "https://example.com/api/test"

    def test_trailing_slash_base(self):
        assert build_url("https://example.com/", "/path") == "https://example.com/path"

    def test_no_leading_slash_path(self):
        assert build_url("https://example.com", "path") == "https://example.com/path"

    def test_with_params(self):
        url = build_url("https://example.com", "/search", {"q": "test", "page": "1"})
        assert "?" in url
        assert "q=test" in url
        assert "page=1" in url

    def test_no_params(self):
        url = build_url("https://example.com", "/path")
        assert "?" not in url

    def test_empty_params(self):
        url = build_url("https://example.com", "/path", {})
        assert "?" not in url

    def test_existing_query_string(self):
        url = build_url("https://example.com", "/path?existing=1", {"new": "2"})
        assert "&new=2" in url



class TestExtractSubtitleInfo:
    def test_english_detection(self):
        info = extract_subtitle_info("movie.english.srt")
        assert info["language"] == "english"

    def test_eng_detection(self):
        info = extract_subtitle_info("movie.eng.srt")
        assert info["language"] == "eng"

    def test_french_detection(self):
        info = extract_subtitle_info("movie.french.srt")
        assert info["language"] == "french"

    def test_hearing_impaired(self):
        info = extract_subtitle_info("movie.HI.srt")
        assert info["hearing_impaired"] is True

    def test_sdh(self):
        info = extract_subtitle_info("movie.SDH.srt")
        assert info["hearing_impaired"] is True

    def test_forced(self):
        info = extract_subtitle_info("movie.forced.srt")
        assert info["forced"] is True

    def test_foreign(self):
        info = extract_subtitle_info("movie.foreign.srt")
        assert info["forced"] is True

    def test_release_group(self):
        info = extract_subtitle_info("Movie.2020.720p.BluRay-SPARKS.srt")
        # release_group extraction is not implemented; returns None
        assert info["release_group"] is None

    def test_quality_720p(self):
        info = extract_subtitle_info("movie.720p.bluray.srt")
        assert info["quality"] == "720p"

    def test_quality_1080p(self):
        info = extract_subtitle_info("movie.1080p.webrip.srt")
        assert info["quality"] == "1080p"

    def test_quality_bluray(self):
        info = extract_subtitle_info("movie.bluray.srt")
        assert info["quality"] == "bluray"

    def test_no_info(self):
        info = extract_subtitle_info("x.srt")
        # Should still find something due to generic 2-3 letter code matching "srt"
        assert isinstance(info, dict)

    def test_spanish(self):
        info = extract_subtitle_info("movie.spanish.srt")
        assert info["language"] == "spanish"

    def test_japanese(self):
        info = extract_subtitle_info("movie.japanese.srt")
        assert info["language"] == "japanese"

    def test_arabic(self):
        info = extract_subtitle_info("movie.arabic.srt")
        assert info["language"] == "arabic"

    def test_hindi(self):
        info = extract_subtitle_info("movie.hindi.srt")
        assert info["language"] == "hindi"

    def test_4k_quality(self):
        info = extract_subtitle_info("movie.4k.srt")
        assert info["quality"] == "4k"


# ===========================================================================
# IMDBLookupService tests
# ===========================================================================

class TestIMDBLookupService:
    def setup_method(self):
        self.mock_session = MagicMock()
        self.service = IMDBLookupService(self.mock_session)

    def test_invalid_imdb_id_none(self):
        assert self.service.lookup_title(None) is None

    def test_invalid_imdb_id_no_tt(self):
        assert self.service.lookup_title("1234567") is None

    def test_empty_imdb_id(self):
        assert self.service.lookup_title("") is None

    def test_successful_lookup(self):
        html = '<html><head><title>Inception (2010) - IMDb</title></head><body><h1 data-testid="hero__pageTitle">Inception</h1></body></html>'
        mock_resp = MagicMock()
        mock_resp.text = html
        self.mock_session.get.return_value = mock_resp

        title = self.service.lookup_title("tt1375666")
        assert title == "Inception"

    def test_cache_hit(self):
        self.service.cache["tt1234567"] = {
            "title": "Cached Movie",
            "timestamp": time.time()
        }
        title = self.service.lookup_title("tt1234567")
        assert title == "Cached Movie"
        self.mock_session.get.assert_not_called()

    def test_cache_expired(self):
        self.service.cache["tt1234567"] = {
            "title": "Old Movie",
            "timestamp": time.time() - 7200  # 2 hours ago, TTL is 1 hour
        }
        html = '<html><body><h1 data-testid="hero__pageTitle">New Title</h1></body></html>'
        mock_resp = MagicMock()
        mock_resp.text = html
        self.mock_session.get.return_value = mock_resp

        title = self.service.lookup_title("tt1234567")
        assert title == "New Title"

    def test_network_error_returns_none(self):
        self.mock_session.get.side_effect = Exception("Network error")
        assert self.service.lookup_title("tt1234567") is None

    def test_cache_eviction_on_full(self):
        # Fill cache to MAX_CACHE_SIZE
        for i in range(MAX_CACHE_SIZE):
            self.service.cache[f"tt{i:07d}"] = {
                "title": f"Movie {i}",
                "timestamp": time.time()
            }
        assert len(self.service.cache) == MAX_CACHE_SIZE

        html = '<html><body><h1 data-testid="hero__pageTitle">New Movie</h1></body></html>'
        mock_resp = MagicMock()
        mock_resp.text = html
        self.mock_session.get.return_value = mock_resp

        self.service.lookup_title("tt9999999")
        # Cache should have been cleared and then new entry added
        assert len(self.service.cache) == 1
        assert "tt9999999" in self.service.cache

    def test_response_closed_on_success(self):
        html = '<html><body><h1 data-testid="hero__pageTitle">Test</h1></body></html>'
        mock_resp = MagicMock()
        mock_resp.text = html
        self.mock_session.get.return_value = mock_resp

        self.service.lookup_title("tt1111111")
        mock_resp.close.assert_called()

    def test_no_title_found_returns_none(self):
        html = '<html><body><p>Nothing here</p></body></html>'
        mock_resp = MagicMock()
        mock_resp.text = html
        self.mock_session.get.return_value = mock_resp

        assert self.service.lookup_title("tt0000001") is None


class TestExtractTitleFromHtml:
    def setup_method(self):
        self.service = IMDBLookupService(MagicMock())

    def test_method1_h1_data_testid(self):
        html = '<html><body><h1 data-testid="hero__pageTitle">The Matrix</h1></body></html>'
        assert self.service._extract_title_from_html(html) == "The Matrix"

    def test_method1_strips_year(self):
        html = '<html><body><h1 data-testid="hero__pageTitle">The Matrix (1999)</h1></body></html>'
        assert self.service._extract_title_from_html(html) == "The Matrix"

    def test_method2_span_hero_primary(self):
        html = '<html><body><span class="hero__primary-text">Inception</span></body></html>'
        assert self.service._extract_title_from_html(html) == "Inception"

    def test_method2_strips_year(self):
        html = '<html><body><span class="hero__primary-text">Inception (2010)</span></body></html>'
        assert self.service._extract_title_from_html(html) == "Inception"

    def test_method3_og_title(self):
        html = '<html><head><meta property="og:title" content="Interstellar (2014) - IMDb"/></head><body></body></html>'
        assert self.service._extract_title_from_html(html) == "Interstellar"

    def test_method4_page_title(self):
        html = '<html><head><title>The Exchange (TV Series 2023) - IMDb</title></head><body></body></html>'
        assert self.service._extract_title_from_html(html) == "The Exchange"

    def test_no_title(self):
        html = '<html><body></body></html>'
        assert self.service._extract_title_from_html(html) is None

    def test_method_priority(self):
        """Method 1 (h1 data-testid) should take priority over others."""
        html = '''<html><head><title>Wrong Title - IMDb</title></head>
        <body><h1 data-testid="hero__pageTitle">Correct Title</h1></body></html>'''
        assert self.service._extract_title_from_html(html) == "Correct Title"


class TestIMDBCacheOps:
    def setup_method(self):
        self.service = IMDBLookupService(MagicMock())

    def test_clear_cache(self):
        self.service.cache["tt1234567"] = {"title": "X", "timestamp": time.time()}
        self.service.clear_cache()
        assert len(self.service.cache) == 0

    def test_get_cache_stats_empty(self):
        stats = self.service.get_cache_stats()
        assert stats["total_entries"] == 0
        assert stats["valid_entries"] == 0
        assert stats["expired_entries"] == 0

    def test_get_cache_stats_valid(self):
        self.service.cache["tt1"] = {"title": "A", "timestamp": time.time()}
        self.service.cache["tt2"] = {"title": "B", "timestamp": time.time()}
        stats = self.service.get_cache_stats()
        assert stats["total_entries"] == 2
        assert stats["valid_entries"] == 2
        assert stats["expired_entries"] == 0

    def test_get_cache_stats_expired(self):
        self.service.cache["tt1"] = {"title": "A", "timestamp": time.time() - 7200}
        stats = self.service.get_cache_stats()
        assert stats["total_entries"] == 1
        assert stats["valid_entries"] == 0
        assert stats["expired_entries"] == 1

    def test_get_cache_stats_mixed(self):
        self.service.cache["tt1"] = {"title": "A", "timestamp": time.time()}
        self.service.cache["tt2"] = {"title": "B", "timestamp": time.time() - 7200}
        stats = self.service.get_cache_stats()
        assert stats["total_entries"] == 2
        assert stats["valid_entries"] == 1
        assert stats["expired_entries"] == 1


# ===========================================================================
# url_validator.py tests
# ===========================================================================

class TestUrlValidator:
    def test_allowed_opensubtitles(self):
        validate_target_url("https://www.opensubtitles.org/en/subtitles/123")
        # Should not raise

    def test_allowed_dl_opensubtitles(self):
        validate_target_url("https://dl.opensubtitles.org/en/download/sub/123")

    def test_allowed_imdb(self):
        validate_target_url("https://www.imdb.com/title/tt1234567/")

    def test_allowed_imdb_no_www(self):
        validate_target_url("https://imdb.com/title/tt1234567/")

    def test_disallowed_host(self):
        with pytest.raises(ValueError, match="not allowed"):
            validate_target_url("https://evil.com/attack")

    def test_bad_scheme_ftp(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_target_url("ftp://www.opensubtitles.org/file")

    def test_bad_scheme_file(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_target_url("file:///etc/passwd")

    def test_bad_scheme_javascript(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_target_url("javascript:alert(1)")

    def test_http_allowed(self):
        validate_target_url("http://www.opensubtitles.org/en/search")

    def test_extra_allowed_hosts(self):
        validate_target_url(
            "https://custom.example.com/api",
            extra_allowed_hosts=frozenset({"custom.example.com"})
        )

    def test_extra_allowed_does_not_override_base(self):
        # Original hosts still allowed when extra hosts provided
        validate_target_url(
            "https://www.opensubtitles.org/en/search",
            extra_allowed_hosts=frozenset({"custom.example.com"})
        )

    def test_empty_scheme(self):
        with pytest.raises(ValueError):
            validate_target_url("://www.opensubtitles.org")
