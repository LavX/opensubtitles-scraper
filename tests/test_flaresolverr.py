# tests/test_flaresolverr.py
"""Tests for FlareSolverr integration in SessionManager."""

import pytest
from unittest.mock import patch, MagicMock
from requests import Response

from src.core.session_manager import SessionManager
from src.utils.exceptions import CloudflareError


class TestCloudflareDetection:
    """Tests for _is_cloudflare_challenge detection."""

    def _make_response(self, status_code, headers=None, body=""):
        resp = Response()
        resp.status_code = status_code
        resp.headers.update(headers or {})
        resp._content = body.encode("utf-8")
        return resp

    def test_detects_403_with_cf_ray(self):
        sm = SessionManager()
        resp = self._make_response(403, {"cf-ray": "abc123"}, "Blocked")
        assert sm._is_cloudflare_challenge(resp) is True

    def test_detects_503_with_cf_ray(self):
        sm = SessionManager()
        resp = self._make_response(503, {"cf-ray": "abc123"}, "Unavailable")
        assert sm._is_cloudflare_challenge(resp) is True

    def test_detects_200_with_just_a_moment(self):
        sm = SessionManager()
        resp = self._make_response(
            200, {"cf-ray": "abc123"}, "<title>Just a moment...</title>"
        )
        assert sm._is_cloudflare_challenge(resp) is True

    def test_detects_200_with_challenge_platform(self):
        sm = SessionManager()
        resp = self._make_response(
            200, {"cf-ray": "abc123"}, "challenge-platform/h/g/orchestrate"
        )
        assert sm._is_cloudflare_challenge(resp) is True

    def test_ignores_403_without_cf_ray(self):
        sm = SessionManager()
        resp = self._make_response(403, {}, "Forbidden")
        assert sm._is_cloudflare_challenge(resp) is False

    def test_ignores_normal_200(self):
        sm = SessionManager()
        resp = self._make_response(
            200, {"cf-ray": "abc123"}, "<html>Normal page</html>"
        )
        assert sm._is_cloudflare_challenge(resp) is False

    def test_ignores_none_response(self):
        sm = SessionManager()
        assert sm._is_cloudflare_challenge(None) is False


class TestFlareSolverrClient:
    """Tests for _solve_with_flaresolverr."""

    def test_successful_solve(self):
        sm = SessionManager()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "ok",
            "solution": {
                "url": "https://www.opensubtitles.org/en/search",
                "status": 200,
                "cookies": [
                    {"name": "cf_clearance", "value": "abc123", "domain": ".opensubtitles.org"},
                    {"name": "PHPSESSID", "value": "xyz", "domain": ".opensubtitles.org"},
                ],
                "userAgent": "Mozilla/5.0 Test UA",
                "response": "<html>Search results</html>",
            },
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            result = sm._solve_with_flaresolverr("https://www.opensubtitles.org/en/search")

        assert result["html"] == "<html>Search results</html>"
        assert result["url"] == "https://www.opensubtitles.org/en/search"
        assert result["status"] == 200
        assert result["user_agent"] == "Mozilla/5.0 Test UA"
        assert len(result["cookies"]) == 2

    def test_solve_error_status(self):
        sm = SessionManager()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "error",
            "message": "Challenge not detected!",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(CloudflareError, match="Challenge not detected"):
                sm._solve_with_flaresolverr("https://example.com")

    def test_solve_unreachable(self):
        sm = SessionManager()
        with patch("requests.post", side_effect=Exception("Connection refused")):
            with pytest.raises(CloudflareError, match="FlareSolverr"):
                sm._solve_with_flaresolverr("https://example.com")

    def test_solve_uses_correct_timeout(self):
        sm = SessionManager()
        sm.flaresolverr_timeout = 45
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "ok",
            "solution": {
                "url": "https://example.com",
                "status": 200,
                "cookies": [],
                "userAgent": "UA",
                "response": "<html></html>",
            },
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            sm._solve_with_flaresolverr("https://example.com")

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["maxTimeout"] == 45000  # seconds * 1000

    def test_solve_missing_solution_key(self):
        sm = SessionManager()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "ok",
            # no "solution" key
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(CloudflareError, match="solution"):
                sm._solve_with_flaresolverr("https://example.com")


class TestCookieInjection:
    """Tests for _inject_flaresolverr_cookies."""

    def test_injects_cookies_into_session(self):
        sm = SessionManager()
        sm.get_session()  # Create a session
        cookies = [
            {"name": "cf_clearance", "value": "abc", "domain": ".opensubtitles.org"},
            {"name": "PHPSESSID", "value": "xyz", "domain": ".opensubtitles.org"},
        ]
        sm._inject_flaresolverr_cookies(cookies, "Mozilla/5.0 Test")

        assert sm._flaresolverr_cookies == cookies
        assert sm._flaresolverr_user_agent == "Mozilla/5.0 Test"

    def test_cookies_stored_for_session_recreation(self):
        sm = SessionManager()
        sm.get_session()
        cookies = [
            {"name": "cf_clearance", "value": "abc", "domain": ".opensubtitles.org"},
        ]
        sm._inject_flaresolverr_cookies(cookies, "Mozilla/5.0 Test")
        assert sm._flaresolverr_cookies == cookies

        # Simulate session recreation
        sm.close()
        sm.get_session()

        # Cookies should be re-injected
        jar = sm.session.cookies
        assert jar.get("cf_clearance", domain=".opensubtitles.org") == "abc"


class TestCreateResponseFromHtml:
    """Tests for _create_response_from_html."""

    def test_creates_valid_response(self):
        sm = SessionManager()
        html = "<html><head><title>Test</title></head><body>Hello</body></html>"
        resp = sm._create_response_from_html(html, "https://example.com/page", 200)

        assert resp.status_code == 200
        assert resp.url == "https://example.com/page"
        assert resp.text == html
        assert resp.content == html.encode("utf-8")
        assert resp.encoding == "utf-8"
        assert "text/html" in resp.headers.get("Content-Type", "")

    def test_works_with_beautifulsoup(self):
        from bs4 import BeautifulSoup

        sm = SessionManager()
        html = "<html><head><title>Matrix</title></head><body><a href='/sub/1'>Sub</a></body></html>"
        resp = sm._create_response_from_html(html, "https://www.opensubtitles.org/en/search", 200)

        soup = BeautifulSoup(resp.text, "html.parser")
        assert soup.find("title").text == "Matrix"
        assert soup.find("a")["href"] == "/sub/1"


class TestMakeRequestFallback:
    """Tests for make_request with FlareSolverr fallback."""

    def _make_sm(self):
        """Create a SessionManager with rate limiting disabled for fast tests."""
        sm = SessionManager()
        sm.last_request_time = 0
        sm.min_request_interval = 0
        sm._rate_limit_per_minute = 9999
        return sm

    def test_returns_directly_when_no_challenge(self):
        sm = self._make_sm()
        normal_resp = Response()
        normal_resp.status_code = 200
        normal_resp._content = b"<html>Normal</html>"
        normal_resp.headers["Content-Type"] = "text/html"

        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session:
            mock_session.return_value.request.return_value = normal_resp
            resp = sm.make_request("GET", "https://www.opensubtitles.org/en/search")

        assert resp.status_code == 200
        assert resp.text == "<html>Normal</html>"

    def test_falls_back_to_flaresolverr_on_challenge(self):
        sm = self._make_sm()

        # First request returns a challenge
        challenge_resp = Response()
        challenge_resp.status_code = 403
        challenge_resp.headers["cf-ray"] = "abc123"
        challenge_resp._content = b"Cloudflare challenge"

        flaresolverr_result = {
            "html": "<html>Solved page</html>",
            "url": "https://www.opensubtitles.org/en/search",
            "status": 200,
            "cookies": [{"name": "cf_clearance", "value": "solved", "domain": ".opensubtitles.org"}],
            "user_agent": "Mozilla/5.0 Solved UA",
        }

        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session, \
             patch.object(sm, "_solve_with_flaresolverr", return_value=flaresolverr_result):
            mock_session.return_value.request.return_value = challenge_resp
            resp = sm.make_request("GET", "https://www.opensubtitles.org/en/search")

        assert resp.status_code == 200
        assert "Solved page" in resp.text

    def test_falls_back_to_flaresolverr_on_preflight_detection(self):
        sm = self._make_sm()

        flaresolverr_result = {
            "html": "<html>Solved via preflight</html>",
            "url": "https://www.opensubtitles.org/en/search",
            "status": 200,
            "cookies": [{"name": "cf_clearance", "value": "solved", "domain": ".opensubtitles.org"}],
            "user_agent": "Mozilla/5.0 Solved UA",
        }

        with patch.object(sm, "_is_cloudflare_active", return_value=True), \
             patch.object(sm, "_solve_with_flaresolverr", return_value=flaresolverr_result):
            resp = sm.make_request("GET", "https://www.opensubtitles.org/en/search")

        assert resp.status_code == 200
        assert "Solved via preflight" in resp.text

    def test_falls_back_to_flaresolverr_on_timeout(self):
        sm = self._make_sm()

        flaresolverr_result = {
            "html": "<html>Solved after timeout</html>",
            "url": "https://www.opensubtitles.org/en/search",
            "status": 200,
            "cookies": [{"name": "cf_clearance", "value": "solved", "domain": ".opensubtitles.org"}],
            "user_agent": "Mozilla/5.0 Solved UA",
        }

        from requests.exceptions import Timeout

        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session, \
             patch.object(sm, "_solve_with_flaresolverr", return_value=flaresolverr_result):
            mock_session.return_value.request.side_effect = Timeout("timed out")
            resp = sm.make_request("GET", "https://www.opensubtitles.org/en/search")

        assert resp.status_code == 200
        assert "Solved after timeout" in resp.text

    def test_flaresolverr_non_200_raises(self):
        sm = self._make_sm()

        flaresolverr_result = {
            "html": "<html>Not Found</html>",
            "url": "https://www.opensubtitles.org/en/search",
            "status": 404,
            "cookies": [],
            "user_agent": "Mozilla/5.0",
        }

        with patch.object(sm, "_is_cloudflare_active", return_value=True), \
             patch.object(sm, "_solve_with_flaresolverr", return_value=flaresolverr_result):
            with pytest.raises(Exception):
                sm.make_request("GET", "https://www.opensubtitles.org/en/search")


import threading
import time as time_module


class TestThreadSafety:
    """Tests for thread-safety of FlareSolverr integration."""

    def test_double_solve_prevented(self):
        """Two concurrent challenges should only trigger one FlareSolverr call."""
        sm = SessionManager()
        sm.min_request_interval = 0
        sm._rate_limit_per_minute = 9999
        solve_call_count = 0
        solve_lock = threading.Lock()

        original_solve = sm._solve_with_flaresolverr

        def counting_solve(url):
            nonlocal solve_call_count
            with solve_lock:
                solve_call_count += 1
            # Simulate FlareSolverr taking time
            time_module.sleep(0.5)
            return {
                "html": "<html>Solved</html>",
                "url": url,
                "status": 200,
                "cookies": [{"name": "cf_clearance", "value": "abc", "domain": ".opensubtitles.org"}],
                "user_agent": "Mozilla/5.0 Test",
            }

        sm._solve_with_flaresolverr = counting_solve

        results = []
        errors = []

        def make_request_thread():
            try:
                resp = sm._fallback_to_flaresolverr("https://www.opensubtitles.org/test")
                results.append(resp)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=make_request_thread)
        t2 = threading.Thread(target=make_request_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 2
        # Key assertion: only ONE solve call should have been made
        assert solve_call_count == 1, f"Expected 1 solve call, got {solve_call_count}"

    def test_rate_limiter_thread_safe(self):
        """Two concurrent calls should not bypass rate limiting."""
        sm = SessionManager()
        sm.min_request_interval = 0.5  # 500ms minimum between requests
        sm._rate_limit_per_minute = 9999

        timestamps = []
        ts_lock = threading.Lock()

        original_wait = sm._wait_for_rate_limit

        def recording_wait():
            original_wait()
            with ts_lock:
                timestamps.append(time_module.time())

        sm._wait_for_rate_limit = recording_wait

        t1 = threading.Thread(target=sm._wait_for_rate_limit)
        t2 = threading.Thread(target=sm._wait_for_rate_limit)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(timestamps) == 2
        # The two calls should be at least min_request_interval apart
        gap = abs(timestamps[1] - timestamps[0])
        assert gap >= 0.4, f"Rate limit bypassed: gap was only {gap:.3f}s (expected >= 0.5s)"


class TestCookieTTL:
    """Tests for cookie expiry-based pre-flight skip."""

    def test_skips_preflight_with_valid_cookies(self):
        sm = SessionManager()
        # Set cookies with expiry 1 hour from now
        sm._flaresolverr_cookies = [
            {"name": "cf_clearance", "value": "abc", "domain": ".opensubtitles.org",
             "expiry": time_module.time() + 3600},
        ]
        assert sm._is_cloudflare_active("https://www.opensubtitles.org/test") is False

    def test_does_not_skip_preflight_with_expired_cookies(self):
        sm = SessionManager()
        # Set cookies with expiry in the past
        sm._flaresolverr_cookies = [
            {"name": "cf_clearance", "value": "abc", "domain": ".opensubtitles.org",
             "expiry": time_module.time() - 60},
        ]
        # Should NOT skip — cookies are expired, need to re-check
        # Mock the actual HEAD request to return non-challenge
        with patch("requests.head") as mock_head:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_head.return_value = mock_resp
            result = sm._is_cloudflare_active("https://www.opensubtitles.org/test")
        assert result is False  # not a challenge, but preflight DID run
        mock_head.assert_called_once()  # proves preflight was NOT skipped
        # Expired cookies should be cleared
        assert sm._flaresolverr_cookies == []

    def test_clears_expired_cookies_and_user_agent(self):
        sm = SessionManager()
        sm._flaresolverr_cookies = [
            {"name": "cf_clearance", "value": "old", "domain": ".opensubtitles.org",
             "expiry": time_module.time() - 120},
        ]
        sm._flaresolverr_user_agent = "Old UA"
        with patch("requests.head") as mock_head:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_head.return_value = mock_resp
            sm._is_cloudflare_active("https://www.opensubtitles.org/test")
        assert sm._flaresolverr_cookies == []
        assert sm._flaresolverr_user_agent is None


class TestMakeRequestErrors:
    """Tests for make_request error handling branches."""

    def _make_sm(self):
        sm = SessionManager()
        sm.last_request_time = 0
        sm.min_request_interval = 0
        sm._rate_limit_per_minute = 9999
        return sm

    def test_semaphore_timeout_raises_scraping_error(self):
        """Line 390: semaphore exhaustion."""
        from src.utils.exceptions import ScrapingError
        sm = self._make_sm()
        # Patch acquire() to return False immediately, simulating timeout
        with patch.object(sm._request_semaphore, "acquire", return_value=False):
            with pytest.raises(ScrapingError, match="too many concurrent"):
                sm.make_request("GET", "https://example.com")

    def test_connection_error_raises_service_unavailable(self):
        """Lines 434-437: ConnectionError -> ServiceUnavailableError."""
        from src.utils.exceptions import ServiceUnavailableError
        from requests.exceptions import ConnectionError as ReqConnectionError
        sm = self._make_sm()
        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session:
            mock_session.return_value.request.side_effect = ReqConnectionError("refused")
            with pytest.raises(ServiceUnavailableError, match="Connection error"):
                sm.make_request("GET", "https://example.com")

    def test_request_exception_403_raises_cloudflare_error(self):
        """Line 451: RequestException with 403 status."""
        from src.utils.exceptions import CloudflareError
        from requests.exceptions import HTTPError
        sm = self._make_sm()
        # Build a response that raise_for_status() will raise HTTPError for,
        # with e.response set so the except RequestException branch sees status 403.
        mock_resp = Response()
        mock_resp.status_code = 403
        mock_resp._content = b"Forbidden"
        mock_resp.headers["Content-Type"] = "text/html"
        exc = HTTPError(response=mock_resp)
        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session:
            # Make session.request return the response, then raise_for_status raises.
            # We bypass raise_for_status by making session.request itself raise the
            # HTTPError (which has e.response attached).
            mock_session.return_value.request.side_effect = exc
            with pytest.raises(CloudflareError, match="Access forbidden"):
                sm.make_request("GET", "https://example.com")

    def test_request_exception_503_raises_service_unavailable(self):
        """Line 453: RequestException with 503 status."""
        from src.utils.exceptions import ServiceUnavailableError
        from requests.exceptions import HTTPError
        sm = self._make_sm()
        mock_resp = Response()
        mock_resp.status_code = 503
        mock_resp._content = b"Service Unavailable"
        mock_resp.headers["Content-Type"] = "text/html"
        exc = HTTPError(response=mock_resp)
        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session:
            mock_session.return_value.request.side_effect = exc
            with pytest.raises(ServiceUnavailableError, match="temporarily unavailable"):
                sm.make_request("GET", "https://example.com")

    def test_request_exception_other_status_raises_scraping_error(self):
        """Line 455: RequestException with non-403/503 status."""
        from src.utils.exceptions import ScrapingError
        from requests.exceptions import HTTPError
        sm = self._make_sm()
        mock_resp = Response()
        mock_resp.status_code = 500
        mock_resp._content = b"Internal Server Error"
        mock_resp.headers["Content-Type"] = "text/html"
        exc = HTTPError(response=mock_resp)
        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session:
            mock_session.return_value.request.side_effect = exc
            with pytest.raises(ScrapingError, match="HTTP error 500"):
                sm.make_request("GET", "https://example.com")

    def test_request_exception_no_response_raises_scraping_error(self):
        """Line 457: RequestException without response object."""
        from src.utils.exceptions import ScrapingError
        from requests.exceptions import RequestException
        sm = self._make_sm()
        with patch.object(sm, "_is_cloudflare_active", return_value=False), \
             patch.object(sm, "get_session") as mock_session:
            mock_session.return_value.request.side_effect = RequestException("weird error")
            with pytest.raises(ScrapingError, match="Request error"):
                sm.make_request("GET", "https://example.com")

    def test_cleanup_on_error_suppresses_cleanup_exception(self):
        """Lines 463-466: _cleanup_on_error swallows exceptions from _cleanup_idle_connections."""
        sm = self._make_sm()
        with patch.object(sm, "_cleanup_idle_connections", side_effect=RuntimeError("boom")):
            # Should not raise — the warning is logged and exception is swallowed
            sm._cleanup_on_error()

    def test_get_delegates_to_make_request(self):
        """Line 470: get() calls make_request."""
        sm = self._make_sm()
        with patch.object(sm, "make_request", return_value="ok") as mock:
            result = sm.get("https://example.com", headers={"X-Test": "1"})
        mock.assert_called_once_with("GET", "https://example.com", headers={"X-Test": "1"})
        assert result == "ok"

    def test_post_delegates_to_make_request(self):
        """Line 474: post() calls make_request."""
        sm = self._make_sm()
        with patch.object(sm, "make_request", return_value="ok") as mock:
            result = sm.post("https://example.com", data="body")
        mock.assert_called_once_with("POST", "https://example.com", data="body")
        assert result == "ok"


class TestFallbackEdgeCases:
    """Tests for edge cases in _is_cloudflare_active and _fallback_to_flaresolverr."""

    def test_preflight_network_error_returns_false(self):
        """Lines 217-218: network errors in preflight are swallowed."""
        sm = SessionManager()
        # No cookies, so preflight will run
        sm._flaresolverr_cookies = []
        with patch("requests.head", side_effect=Exception("DNS resolution failed")):
            result = sm._is_cloudflare_active("https://www.opensubtitles.org/test")
        assert result is False

    def test_preflight_timeout_returns_false(self):
        """Lines 217-218: timeout in preflight is swallowed."""
        from requests.exceptions import Timeout
        sm = SessionManager()
        sm._flaresolverr_cookies = []
        with patch("requests.head", side_effect=Timeout("timed out")):
            result = sm._is_cloudflare_active("https://www.opensubtitles.org/test")
        assert result is False

    def test_preflight_connection_refused_returns_false(self):
        """Lines 217-218: connection refused is swallowed."""
        from requests.exceptions import ConnectionError
        sm = SessionManager()
        sm._flaresolverr_cookies = []
        with patch("requests.head", side_effect=ConnectionError("refused")):
            result = sm._is_cloudflare_active("https://www.opensubtitles.org/test")
        assert result is False

    def test_preflight_detects_cloudflare_via_head(self):
        """Lines 249-251: HEAD returns 403 + cf-ray -> cloudflare active."""
        sm = SessionManager()
        sm._flaresolverr_cookies = []
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"cf-ray": "abc123"}
        mock_resp.text = "Blocked"
        with patch("requests.head", return_value=mock_resp):
            result = sm._is_cloudflare_active("https://www.opensubtitles.org/test")
        assert result is True

    def test_preflight_no_cloudflare(self):
        """Lines 249-251: HEAD returns 200, no cf-ray -> not active."""
        sm = SessionManager()
        sm._flaresolverr_cookies = []
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        with patch("requests.head", return_value=mock_resp):
            result = sm._is_cloudflare_active("https://www.opensubtitles.org/test")
        assert result is False

    def test_fallback_reuses_existing_cookies(self):
        """Lines 338-349: when cookies already exist, try direct request first."""
        sm = SessionManager()
        sm._flaresolverr_cookies = [
            {"name": "cf_clearance", "value": "existing", "domain": ".opensubtitles.org",
             "expiry": time_module.time() + 3600}
        ]
        sm._flaresolverr_user_agent = "Mozilla/5.0"
        sm.get_session()  # create session
        sm._inject_flaresolverr_cookies(sm._flaresolverr_cookies, sm._flaresolverr_user_agent)

        # Mock the session's direct request to succeed
        normal_resp = Response()
        normal_resp.status_code = 200
        normal_resp._content = b"<html>Direct success</html>"
        normal_resp.headers["Content-Type"] = "text/html"

        with patch.object(sm.session, "request", return_value=normal_resp):
            resp = sm._fallback_to_flaresolverr("https://www.opensubtitles.org/test")

        assert resp.status_code == 200
        assert "Direct success" in resp.text


class TestSessionLifecycle:
    """Tests for session lifecycle, rate limiting, and cleanup."""

    def test_per_minute_rate_limit_evicts_old_timestamps(self):
        """Lines 158-159: old timestamps are evicted from sliding window."""
        import time as t
        sm = SessionManager()
        sm.min_request_interval = 0
        sm._rate_limit_per_minute = 5
        # Pre-fill with timestamps from 61 seconds ago (should be evicted)
        old_time = t.time() - 61
        for _ in range(5):
            sm._request_timestamps.append(old_time)
        # This should evict all old ones and proceed without sleeping
        sm._wait_for_rate_limit()
        # Old timestamps evicted; one new one appended at the end of the call
        assert len(sm._request_timestamps) == 1

    def test_per_minute_rate_limit_sleeps_when_window_full(self):
        """Lines 161-175: sleeps when rate limit window is full (placeholder)."""
        # Practical timing version is covered by test_per_minute_rate_limit_practical.
        pass

    def test_per_minute_rate_limit_practical(self):
        """Lines 161-175: practical test with short sleep."""
        import time as t
        sm = SessionManager()
        sm.min_request_interval = 0
        sm._rate_limit_per_minute = 2
        # Fill window with timestamps that expire in ~0.3s.
        # 59.7 seconds ago => still inside 60s window, expires in ~0.3s.
        boundary = t.time() - 59.7
        sm._request_timestamps.append(boundary)
        sm._request_timestamps.append(boundary + 0.01)
        start = t.time()
        sm._wait_for_rate_limit()
        elapsed = t.time() - start
        # Should have slept approximately 0.3 seconds
        assert elapsed >= 0.2, f"Expected sleep >= 0.2s, got {elapsed:.3f}s"
        assert elapsed < 2.0, f"Slept too long: {elapsed:.3f}s"

    def test_maybe_cleanup_triggers_at_threshold(self):
        """Lines 184-187: cleanup triggers when request_count hits threshold."""
        sm = SessionManager()
        sm.get_session()  # create session
        sm.max_requests_before_cleanup = 3
        # _maybe_cleanup_connections increments request_count first, then checks.
        # Setting to 2 means one more increment reaches 3 == max_requests_before_cleanup.
        sm.request_count = 2
        with patch.object(sm, "_cleanup_idle_connections") as mock_cleanup:
            sm._maybe_cleanup_connections()
        mock_cleanup.assert_called_once()
        assert sm.request_count == 0

    def test_cleanup_idle_connections_clears_pools(self):
        """Lines 191-199: pool cleanup does not raise."""
        sm = SessionManager()
        sm.get_session()
        # Should not raise
        sm._cleanup_idle_connections()

    def test_create_session_failure_raises_cloudflare_error(self):
        """Lines 121-123: session creation failure raises CloudflareError."""
        from src.utils.exceptions import CloudflareError
        sm = SessionManager()
        with patch("cloudscraper.create_scraper", side_effect=Exception("browser init failed")):
            with pytest.raises(CloudflareError, match="Could not create session"):
                sm._create_session()

    def test_close_cleans_up_session(self):
        """Lines 476-503: close() releases resources."""
        sm = SessionManager()
        sm.get_session()
        assert sm.session is not None
        sm.close()
        assert sm.session is None
        assert sm.request_count == 0

    def test_close_idempotent(self):
        """close() on already-closed session is safe."""
        sm = SessionManager()
        sm.close()  # no session to close
        sm.close()  # still safe

    def test_context_manager(self):
        """Lines 505-509: __enter__/__exit__."""
        with SessionManager() as sm:
            assert sm is not None
        assert sm.session is None  # closed by __exit__
