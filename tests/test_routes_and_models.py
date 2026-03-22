"""Tests for src/api/routes.py and src/api/models.py"""

import base64
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Models tests
# ---------------------------------------------------------------------------

class TestSearchRequest:
    def test_valid(self):
        from src.api.models import SearchRequest
        r = SearchRequest(query="The Matrix", year=1999, imdb_id="tt0133093", kind="movie")
        assert r.query == "The Matrix"
        assert r.year == 1999

    def test_defaults(self):
        from src.api.models import SearchRequest
        r = SearchRequest(query="test")
        assert r.year is None
        assert r.imdb_id is None
        assert r.kind == "movie"

    def test_max_length(self):
        from src.api.models import SearchRequest
        # exactly 500 should be fine
        SearchRequest(query="x" * 500)
        # 501 should fail
        with pytest.raises(ValidationError):
            SearchRequest(query="x" * 501)


class TestSearchResult:
    def test_defaults(self):
        from src.api.models import SearchResult
        r = SearchResult(title="Test")
        assert r.subtitle_count == 0
        assert r.kind == "movie"
        assert r.year is None

    def test_full(self):
        from src.api.models import SearchResult
        r = SearchResult(title="T", year=2020, imdb_id="tt123", url="http://x", subtitle_count=5, kind="episode")
        assert r.subtitle_count == 5


class TestSearchResponse:
    def test_valid(self):
        from src.api.models import SearchResponse, SearchResult
        resp = SearchResponse(results=[SearchResult(title="A")], total=1, query="q")
        assert resp.total == 1


class TestSubtitleRequest:
    def test_valid_url(self):
        from src.api.models import SubtitleRequest
        r = SubtitleRequest(movie_url="https://www.opensubtitles.org/en/search/sublanguageid-eng")
        assert r.movie_url.startswith("https://")

    def test_disallowed_host(self):
        from src.api.models import SubtitleRequest
        with pytest.raises(ValidationError, match="URL host not allowed"):
            SubtitleRequest(movie_url="https://evil.example.com/path")

    def test_bad_scheme(self):
        from src.api.models import SubtitleRequest
        with pytest.raises(ValidationError, match="Unsupported URL scheme"):
            SubtitleRequest(movie_url="ftp://www.opensubtitles.org/en/foo")

    def test_allowed_hosts(self):
        from src.api.models import SubtitleRequest
        for host in ("www.opensubtitles.org", "opensubtitles.org", "dl.opensubtitles.org"):
            SubtitleRequest(movie_url=f"https://{host}/path")


class TestSubtitleInfo:
    def test_defaults(self):
        from src.api.models import SubtitleInfo
        s = SubtitleInfo(subtitle_id="1", language="en", filename="f.srt", release_name="r", uploader="u")
        assert s.download_count == 0
        assert s.rating == 0.0
        assert s.hearing_impaired is False
        assert s.forced is False
        assert s.fps is None
        assert s.download_url is None
        assert s.upload_date is None

    def test_full(self):
        from src.api.models import SubtitleInfo
        from datetime import datetime
        s = SubtitleInfo(
            subtitle_id="1", language="en", filename="f.srt",
            release_name="r", uploader="u", download_count=10,
            rating=8.5, hearing_impaired=True, forced=True,
            fps=23.976, download_url="https://www.opensubtitles.org/dl",
            upload_date=datetime(2024, 1, 1)
        )
        assert s.fps == 23.976


class TestSubtitleResponse:
    def test_valid(self):
        from src.api.models import SubtitleResponse, SubtitleInfo
        resp = SubtitleResponse(
            subtitles=[SubtitleInfo(subtitle_id="1", language="en", filename="f.srt", release_name="r", uploader="u")],
            total=1,
            movie_url="https://www.opensubtitles.org/x"
        )
        assert resp.total == 1


class TestDownloadRequest:
    def test_valid(self):
        from src.api.models import DownloadRequest
        r = DownloadRequest(subtitle_id="123", download_url="https://www.opensubtitles.org/en/subtitles/123")
        assert r.subtitle_id == "123"

    def test_disallowed_host(self):
        from src.api.models import DownloadRequest
        with pytest.raises(ValidationError, match="URL host not allowed"):
            DownloadRequest(subtitle_id="1", download_url="https://evil.com/x")

    def test_bad_scheme(self):
        from src.api.models import DownloadRequest
        with pytest.raises(ValidationError, match="Unsupported URL scheme"):
            DownloadRequest(subtitle_id="1", download_url="ftp://www.opensubtitles.org/x")

    def test_all_allowed_hosts(self):
        from src.api.models import DownloadRequest
        for host in ("www.opensubtitles.org", "dl.opensubtitles.org", "www.imdb.com", "imdb.com"):
            DownloadRequest(subtitle_id="1", download_url=f"https://{host}/path")


class TestDownloadResponse:
    def test_valid(self):
        from src.api.models import DownloadResponse
        r = DownloadResponse(filename="f.srt", content="abc", size=3)
        assert r.encoding == "utf-8"

    def test_custom_encoding(self):
        from src.api.models import DownloadResponse
        r = DownloadResponse(filename="f.srt", content="abc", size=3, encoding="latin-1")
        assert r.encoding == "latin-1"


class TestHealthResponse:
    def test_defaults(self):
        from src.api.models import HealthResponse
        r = HealthResponse(version="1.0", uptime=1.5, scraper_status="healthy")
        assert r.status == "healthy"
        assert r.flaresolverr_status == "not_configured"


class TestErrorResponse:
    def test_valid(self):
        from src.api.models import ErrorResponse
        r = ErrorResponse(error="not_found", message="Not found")
        assert r.details is None

    def test_with_details(self):
        from src.api.models import ErrorResponse
        r = ErrorResponse(error="err", message="msg", details={"key": "val"})
        assert r.details["key"] == "val"


# ---------------------------------------------------------------------------
# Helpers for route tests
# ---------------------------------------------------------------------------

def _make_search_result(**kwargs):
    defaults = dict(title="Test Movie", year=2020, imdb_id="tt1234567",
                    url="https://www.opensubtitles.org/en/search/sublanguageid-all/idmovie-12345",
                    subtitle_count=42, kind="movie")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_subtitle_info(**kwargs):
    defaults = dict(
        subtitle_id="999", language="eng", filename="test.srt",
        release_name="Test.Movie.2020.720p", uploader="tester",
        download_count=100, rating=8.0, hearing_impaired=False,
        forced=False, fps=23.976,
        download_url="https://www.opensubtitles.org/en/subtitles/999",
        upload_date=None, movie_name="", movie_year="2020"
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.fixture()
def mock_scraper():
    scraper = MagicMock()
    scraper.search_movies.return_value = [_make_search_result()]
    scraper.search_tv_shows.return_value = [_make_search_result(kind="episode")]
    scraper.get_subtitles.return_value = [_make_subtitle_info()]
    scraper.download_subtitle.return_value = {
        "filename": "test.srt",
        "content": "subtitle content here",
        "size": 21,
        "encoding": "utf-8",
    }
    return scraper


@pytest.fixture()
def client(mock_scraper):
    from src.api.routes import get_scraper
    from main import app

    # Override the FastAPI dependency so all Depends(get_scraper) return the mock
    app.dependency_overrides[get_scraper] = lambda: mock_scraper

    # Also patch the direct get_scraper() calls in main.py wrappers
    with patch("main.get_scraper", return_value=mock_scraper):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Route tests — root-level endpoints from main.py
# ---------------------------------------------------------------------------

class TestRootEndpoint:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "OpenSubtitles Scraper"

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# Route tests — /api/v1 prefix endpoints
# ---------------------------------------------------------------------------

class TestApiV1Health:
    def test_health_check(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "uptime" in data
        assert data["scraper_status"] == "healthy"

    def test_health_flaresolverr_not_configured(self, client):
        with patch.dict("os.environ", {"FLARESOLVERR_URL": ""}, clear=False):
            resp = client.get("/api/v1/health")
            assert resp.json()["flaresolverr_status"] == "not_configured"

    def test_health_flaresolverr_available(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.dict("os.environ", {"FLARESOLVERR_URL": "http://flare:8191/v1"}, clear=False), \
             patch("requests.get", return_value=mock_resp):
            resp = client.get("/api/v1/health")
            assert resp.json()["flaresolverr_status"] == "available"

    def test_health_flaresolverr_unavailable(self, client):
        with patch.dict("os.environ", {"FLARESOLVERR_URL": "http://flare:8191/v1"}, clear=False), \
             patch("requests.get", side_effect=Exception("conn refused")):
            resp = client.get("/api/v1/health")
            assert resp.json()["flaresolverr_status"] == "unavailable"

    def test_health_flaresolverr_bad_status(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch.dict("os.environ", {"FLARESOLVERR_URL": "http://flare:8191/v1"}, clear=False), \
             patch("requests.get", return_value=mock_resp):
            resp = client.get("/api/v1/health")
            assert resp.json()["flaresolverr_status"] == "unavailable"

    def test_health_exception_returns_unhealthy(self, client):
        with patch("src.api.routes.get_scraper", side_effect=RuntimeError("boom")):
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "unhealthy"
            assert data["scraper_status"] == "error"


class TestSearchMovies:
    def test_success(self, client, mock_scraper):
        resp = client.post("/api/v1/search/movies", json={"query": "The Matrix"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["query"] == "The Matrix"
        assert data["results"][0]["title"] == "Test Movie"
        mock_scraper.search_movies.assert_called_once_with(query="The Matrix", year=None, imdb_id=None)

    def test_with_year_and_imdb(self, client, mock_scraper):
        resp = client.post("/api/v1/search/movies", json={"query": "Matrix", "year": 1999, "imdb_id": "tt0133093"})
        assert resp.status_code == 200
        mock_scraper.search_movies.assert_called_once_with(query="Matrix", year=1999, imdb_id="tt0133093")

    def test_search_error(self, client, mock_scraper):
        from src.utils.exceptions import SearchError
        mock_scraper.search_movies.side_effect = SearchError("no results")
        resp = client.post("/api/v1/search/movies", json={"query": "xyz"})
        assert resp.status_code == 400

    def test_cloudflare_error(self, client, mock_scraper):
        from src.utils.exceptions import CloudflareError
        mock_scraper.search_movies.side_effect = CloudflareError("blocked")
        resp = client.post("/api/v1/search/movies", json={"query": "xyz"})
        assert resp.status_code == 503
        assert "Cloudflare" in resp.json()["detail"]

    def test_service_unavailable(self, client, mock_scraper):
        from src.utils.exceptions import ServiceUnavailableError
        mock_scraper.search_movies.side_effect = ServiceUnavailableError("down")
        resp = client.post("/api/v1/search/movies", json={"query": "xyz"})
        assert resp.status_code == 503

    def test_generic_exception(self, client, mock_scraper):
        mock_scraper.search_movies.side_effect = RuntimeError("unexpected")
        resp = client.post("/api/v1/search/movies", json={"query": "xyz"})
        assert resp.status_code == 500


class TestSearchTv:
    def test_success(self, client, mock_scraper):
        resp = client.post("/api/v1/search/tv", json={"query": "Breaking Bad"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        mock_scraper.search_tv_shows.assert_called_once()

    def test_search_error(self, client, mock_scraper):
        from src.utils.exceptions import SearchError
        mock_scraper.search_tv_shows.side_effect = SearchError("fail")
        resp = client.post("/api/v1/search/tv", json={"query": "xyz"})
        assert resp.status_code == 400

    def test_cloudflare_error(self, client, mock_scraper):
        from src.utils.exceptions import CloudflareError
        mock_scraper.search_tv_shows.side_effect = CloudflareError("blocked")
        resp = client.post("/api/v1/search/tv", json={"query": "xyz"})
        assert resp.status_code == 503

    def test_service_unavailable(self, client, mock_scraper):
        from src.utils.exceptions import ServiceUnavailableError
        mock_scraper.search_tv_shows.side_effect = ServiceUnavailableError("down")
        resp = client.post("/api/v1/search/tv", json={"query": "xyz"})
        assert resp.status_code == 503

    def test_generic_exception(self, client, mock_scraper):
        mock_scraper.search_tv_shows.side_effect = RuntimeError("boom")
        resp = client.post("/api/v1/search/tv", json={"query": "xyz"})
        assert resp.status_code == 500


class TestSubtitles:
    def test_success(self, client, mock_scraper):
        resp = client.post("/api/v1/subtitles", json={
            "movie_url": "https://www.opensubtitles.org/en/search/sublanguageid-eng"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["subtitles"][0]["subtitle_id"] == "999"

    def test_with_languages(self, client, mock_scraper):
        resp = client.post("/api/v1/subtitles", json={
            "movie_url": "https://www.opensubtitles.org/en/foo",
            "languages": ["eng", "fre"]
        })
        assert resp.status_code == 200
        mock_scraper.get_subtitles.assert_called_once_with(
            movie_url="https://www.opensubtitles.org/en/foo",
            languages=["eng", "fre"]
        )

    def test_bad_url_host(self, client):
        resp = client.post("/api/v1/subtitles", json={
            "movie_url": "https://evil.com/foo"
        })
        assert resp.status_code == 422  # validation error

    def test_scraping_error(self, client, mock_scraper):
        from src.utils.exceptions import ScrapingError
        mock_scraper.get_subtitles.side_effect = ScrapingError("parse fail")
        resp = client.post("/api/v1/subtitles", json={
            "movie_url": "https://www.opensubtitles.org/en/foo"
        })
        assert resp.status_code == 400

    def test_cloudflare_error(self, client, mock_scraper):
        from src.utils.exceptions import CloudflareError
        mock_scraper.get_subtitles.side_effect = CloudflareError("blocked")
        resp = client.post("/api/v1/subtitles", json={
            "movie_url": "https://www.opensubtitles.org/en/foo"
        })
        assert resp.status_code == 503

    def test_service_unavailable(self, client, mock_scraper):
        from src.utils.exceptions import ServiceUnavailableError
        mock_scraper.get_subtitles.side_effect = ServiceUnavailableError("down")
        resp = client.post("/api/v1/subtitles", json={
            "movie_url": "https://www.opensubtitles.org/en/foo"
        })
        assert resp.status_code == 503

    def test_generic_exception(self, client, mock_scraper):
        mock_scraper.get_subtitles.side_effect = RuntimeError("boom")
        resp = client.post("/api/v1/subtitles", json={
            "movie_url": "https://www.opensubtitles.org/en/foo"
        })
        assert resp.status_code == 500


class TestDownloadSubtitle:
    def test_success(self, client, mock_scraper):
        resp = client.post("/api/v1/download/subtitle", json={
            "subtitle_id": "123",
            "download_url": "https://www.opensubtitles.org/en/subtitles/123"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "test.srt"
        assert data["content"] == "subtitle content here"
        assert data["size"] == 21

    def test_download_error(self, client, mock_scraper):
        from src.utils.exceptions import DownloadError
        mock_scraper.download_subtitle.side_effect = DownloadError("fail")
        resp = client.post("/api/v1/download/subtitle", json={
            "subtitle_id": "123",
            "download_url": "https://www.opensubtitles.org/en/subtitles/123"
        })
        assert resp.status_code == 400

    def test_cloudflare_error(self, client, mock_scraper):
        from src.utils.exceptions import CloudflareError
        mock_scraper.download_subtitle.side_effect = CloudflareError("blocked")
        resp = client.post("/api/v1/download/subtitle", json={
            "subtitle_id": "123",
            "download_url": "https://www.opensubtitles.org/en/subtitles/123"
        })
        assert resp.status_code == 503

    def test_service_unavailable(self, client, mock_scraper):
        from src.utils.exceptions import ServiceUnavailableError
        mock_scraper.download_subtitle.side_effect = ServiceUnavailableError("down")
        resp = client.post("/api/v1/download/subtitle", json={
            "subtitle_id": "123",
            "download_url": "https://www.opensubtitles.org/en/subtitles/123"
        })
        assert resp.status_code == 503

    def test_generic_exception(self, client, mock_scraper):
        mock_scraper.download_subtitle.side_effect = RuntimeError("boom")
        resp = client.post("/api/v1/download/subtitle", json={
            "subtitle_id": "123",
            "download_url": "https://www.opensubtitles.org/en/subtitles/123"
        })
        assert resp.status_code == 500

    def test_bad_url(self, client):
        resp = client.post("/api/v1/download/subtitle", json={
            "subtitle_id": "123",
            "download_url": "https://evil.com/dl"
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Bazarr-compatible endpoints (root-level /search and /download)
# ---------------------------------------------------------------------------

class TestBazarrSearch:
    def test_movie_search_by_imdb(self, client, mock_scraper):
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "1234567"}],
            "only_foreign": False,
            "also_foreign": False
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "200 OK"
        assert len(data["data"]) == 1
        assert data["data"][0]["IDSubtitleFile"] == "999"
        assert data["data"][0]["MovieKind"] == "movie"
        mock_scraper.search_movies.assert_called_once()

    def test_tv_search_by_imdb(self, client, mock_scraper):
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "1234567", "season": 1, "episode": 2}],
            "only_foreign": False,
            "also_foreign": False
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "200 OK"
        assert len(data["data"]) == 1
        assert data["data"][0]["MovieKind"] == "episode"
        assert data["data"][0]["SeriesSeason"] == 1
        assert data["data"][0]["SeriesEpisode"] == 2
        mock_scraper.search_tv_shows.assert_called_once()

    def test_empty_criteria(self, client, mock_scraper):
        resp = client.post("/search", json={"criteria": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []

    def test_hash_search_skipped(self, client, mock_scraper):
        resp = client.post("/search", json={
            "criteria": [{"moviehash": "abc123"}]
        })
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_tag_search_skipped(self, client, mock_scraper):
        resp = client.post("/search", json={
            "criteria": [{"tag": "some tag"}]
        })
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_only_foreign_filters(self, client, mock_scraper):
        # With only_foreign=True, non-forced subs should be excluded
        mock_scraper.get_subtitles.return_value = [
            _make_subtitle_info(forced=False),
            _make_subtitle_info(subtitle_id="1000", forced=True),
        ]
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "1234567"}],
            "only_foreign": True,
        })
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["IDSubtitleFile"] == "1000"

    def test_exclude_forced_by_default(self, client, mock_scraper):
        # By default (only_foreign=False, also_foreign=False), forced subs excluded
        mock_scraper.get_subtitles.return_value = [
            _make_subtitle_info(forced=True),
            _make_subtitle_info(subtitle_id="1001", forced=False),
        ]
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "1234567"}],
            "only_foreign": False,
            "also_foreign": False,
        })
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["IDSubtitleFile"] == "1001"

    def test_also_foreign_includes_all(self, client, mock_scraper):
        mock_scraper.get_subtitles.return_value = [
            _make_subtitle_info(forced=True),
            _make_subtitle_info(subtitle_id="1001", forced=False),
        ]
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "1234567"}],
            "only_foreign": False,
            "also_foreign": True,
        })
        data = resp.json()
        assert len(data["data"]) == 2

    def test_no_search_results(self, client, mock_scraper):
        mock_scraper.search_movies.return_value = []
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "0000000"}]
        })
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_tv_movie_name_from_release_name_quoted(self, client, mock_scraper):
        """When movie_name is empty and release_name has quoted series title"""
        sub = _make_subtitle_info(
            movie_name="",
            release_name='"Breaking Bad" Ozymandias',
            forced=False,
        )
        mock_scraper.get_subtitles.return_value = [sub]
        search_result = _make_search_result(title="Breaking Bad", kind="episode")
        mock_scraper.search_tv_shows.return_value = [search_result]
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "123", "season": 5, "episode": 14}],
        })
        data = resp.json()["data"]
        assert len(data) == 1
        # The movie_name should contain the release name (already starts with quote)
        assert "Breaking Bad" in data[0]["MovieName"]

    def test_tv_movie_name_fallback(self, client, mock_scraper):
        """When movie_name is empty and release_name has no quotes"""
        sub = _make_subtitle_info(
            movie_name="",
            release_name="Breaking.Bad.S05E14.720p",
            forced=False,
        )
        mock_scraper.get_subtitles.return_value = [sub]
        search_result = _make_search_result(title="Breaking Bad", kind="episode")
        mock_scraper.search_tv_shows.return_value = [search_result]
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "123", "season": 5, "episode": 14}],
        })
        data = resp.json()["data"]
        assert len(data) == 1
        assert "Breaking Bad" in data[0]["MovieName"]

    def test_tv_movie_name_empty_release_name(self, client, mock_scraper):
        """When both movie_name and release_name are empty"""
        sub = _make_subtitle_info(
            movie_name="",
            release_name="",
            forced=False,
        )
        mock_scraper.get_subtitles.return_value = [sub]
        search_result = _make_search_result(title="Breaking Bad", kind="episode")
        mock_scraper.search_tv_shows.return_value = [search_result]
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "123", "season": 5, "episode": 14}],
        })
        data = resp.json()["data"]
        assert len(data) == 1

    def test_cloudflare_error(self, client, mock_scraper):
        from src.utils.exceptions import CloudflareError
        mock_scraper.search_movies.side_effect = CloudflareError("blocked")
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "123"}]
        })
        assert resp.status_code == 503

    def test_service_unavailable(self, client, mock_scraper):
        from src.utils.exceptions import ServiceUnavailableError
        mock_scraper.search_movies.side_effect = ServiceUnavailableError("down")
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "123"}]
        })
        assert resp.status_code == 503

    def test_generic_exception(self, client, mock_scraper):
        mock_scraper.search_movies.side_effect = RuntimeError("boom")
        resp = client.post("/search", json={
            "criteria": [{"imdbid": "123"}]
        })
        assert resp.status_code == 500


class TestBazarrDownload:
    def test_success(self, client, mock_scraper):
        resp = client.post("/download", json={"subtitle_id": "12345"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "200 OK"
        # Verify base64 encoding
        decoded = base64.b64decode(data["data"]).decode("utf-8")
        assert decoded == "subtitle content here"

    def test_missing_subtitle_id(self, client, mock_scraper):
        resp = client.post("/download", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "400 Bad Request"
        assert data["data"] == []

    def test_cloudflare_error(self, client, mock_scraper):
        from src.utils.exceptions import CloudflareError
        mock_scraper.download_subtitle.side_effect = CloudflareError("blocked")
        resp = client.post("/download", json={"subtitle_id": "123"})
        assert resp.status_code == 503

    def test_service_unavailable(self, client, mock_scraper):
        from src.utils.exceptions import ServiceUnavailableError
        mock_scraper.download_subtitle.side_effect = ServiceUnavailableError("down")
        resp = client.post("/download", json={"subtitle_id": "123"})
        assert resp.status_code == 503

    def test_generic_exception(self, client, mock_scraper):
        mock_scraper.download_subtitle.side_effect = RuntimeError("boom")
        resp = client.post("/download", json={"subtitle_id": "123"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Bazarr endpoints via /api/v1 prefix
# ---------------------------------------------------------------------------

class TestBazarrSearchApiV1:
    def test_movie_search(self, client, mock_scraper):
        resp = client.post("/api/v1/search", json={
            "criteria": [{"imdbid": "1234567"}],
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "200 OK"

    def test_tv_search(self, client, mock_scraper):
        resp = client.post("/api/v1/search", json={
            "criteria": [{"imdbid": "1234567", "season": 1, "episode": 1}],
        })
        assert resp.status_code == 200


class TestBazarrDownloadApiV1:
    def test_success(self, client, mock_scraper):
        resp = client.post("/api/v1/download", json={"subtitle_id": "555"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "200 OK"


# ---------------------------------------------------------------------------
# request_limit / semaphore tests
# ---------------------------------------------------------------------------

class TestRequestLimit:
    def test_429_when_semaphore_exhausted(self, client, mock_scraper):
        """When semaphore is exhausted, requests should get 429"""
        import src.api.routes as routes_mod

        # Replace the module-level semaphore with one that's already full
        original_sem = routes_mod._request_semaphore
        routes_mod._request_semaphore = threading.BoundedSemaphore(1)
        # Acquire the only slot
        routes_mod._request_semaphore.acquire(blocking=False)

        try:
            resp = client.post("/api/v1/search/movies", json={"query": "test"})
            assert resp.status_code == 429
            assert "Scraper busy" in resp.json()["detail"]
        finally:
            routes_mod._request_semaphore.release()
            routes_mod._request_semaphore = original_sem


# ---------------------------------------------------------------------------
# get_scraper / cleanup_scraper
# ---------------------------------------------------------------------------

class TestScraperLifecycle:
    def test_get_scraper_singleton(self):
        import src.api.routes as routes_mod
        original = routes_mod._scraper_instance

        with patch("src.api.routes.OpenSubtitlesScraper") as MockScraper:
            mock_instance = MagicMock()
            MockScraper.return_value = mock_instance
            routes_mod._scraper_instance = None

            s1 = routes_mod.get_scraper()
            s2 = routes_mod.get_scraper()
            assert s1 is s2
            MockScraper.assert_called_once()

        routes_mod._scraper_instance = original

    def test_cleanup_scraper(self):
        import src.api.routes as routes_mod
        original = routes_mod._scraper_instance

        mock_instance = MagicMock()
        routes_mod._scraper_instance = mock_instance

        routes_mod.cleanup_scraper()
        mock_instance.close.assert_called_once()
        assert routes_mod._scraper_instance is None

        routes_mod._scraper_instance = original

    def test_cleanup_scraper_when_none(self):
        import src.api.routes as routes_mod
        original = routes_mod._scraper_instance

        routes_mod._scraper_instance = None
        routes_mod.cleanup_scraper()  # should not raise
        assert routes_mod._scraper_instance is None

        routes_mod._scraper_instance = original


