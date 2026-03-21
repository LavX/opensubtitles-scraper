"""Session manager for cloudscraper integration"""

import logging
import os
import time
import threading
from collections import deque
from typing import Optional, Dict, Any
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.exceptions import RequestException, Timeout, ConnectionError

from urllib.parse import urlparse as _urlparse

from ..utils.exceptions import CloudflareError, ScrapingError, ServiceUnavailableError
from ..utils.url_validator import validate_target_url

logger = logging.getLogger(__name__)


# --- Configurable via environment variables (.env) ---
MAX_POOL_CONNECTIONS = int(os.environ.get("SCRAPER_MAX_POOL_CONNECTIONS", "5"))
MAX_POOL_SIZE = int(os.environ.get("SCRAPER_MAX_POOL_SIZE", "3"))
MAX_RETRIES = int(os.environ.get("SCRAPER_MAX_RETRIES", "2"))
RETRY_BACKOFF_FACTOR = float(os.environ.get("SCRAPER_RETRY_BACKOFF_FACTOR", "2"))
MAX_CONCURRENT_REQUESTS = int(os.environ.get("SCRAPER_MAX_CONCURRENT_REQUESTS", "2"))
REQUEST_TIMEOUT = int(os.environ.get("SCRAPER_REQUEST_TIMEOUT", "30"))
MIN_REQUEST_INTERVAL = float(os.environ.get("SCRAPER_MIN_REQUEST_INTERVAL", "1.0"))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("SCRAPER_RATE_LIMIT_PER_MINUTE", "60"))
MAX_REQUESTS_BEFORE_CLEANUP = int(os.environ.get("SCRAPER_MAX_REQUESTS_BEFORE_CLEANUP", "20"))


class SessionManager:
    """Manages cloudscraper sessions for OpenSubtitles.org"""

    def __init__(self, timeout: int = REQUEST_TIMEOUT):
        self.timeout = timeout
        self.session: Optional[cloudscraper.CloudScraper] = None
        self.base_url = "https://www.opensubtitles.org"
        self.last_request_time = 0
        self.min_request_interval = MIN_REQUEST_INTERVAL
        self.request_count = 0
        self.max_requests_before_cleanup = MAX_REQUESTS_BEFORE_CLEANUP
        self._lock = threading.Lock()
        self._request_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
        # FlareSolverr configuration
        self.flaresolverr_url = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191/v1")
        self.flaresolverr_timeout = int(os.environ.get("FLARESOLVERR_TIMEOUT", "60"))
        self._flaresolverr_cookies = []
        self._flaresolverr_user_agent = None
        # Sliding window for per-minute rate limiting
        self._request_timestamps: deque = deque()
        self._rate_limit_per_minute = RATE_LIMIT_PER_MINUTE
        # Dedicated locks for thread-safety (NOT reusing self._lock to avoid deadlocks)
        self._rate_limit_lock = threading.Lock()
        self._flaresolverr_solve_lock = threading.Lock()
        self._flaresolverr_solving = False
        self._flaresolverr_solve_done = threading.Event()
        self._flaresolverr_last_result = None
        # Pre-compute FlareSolverr host for URL allowlist validation
        _parsed_fs = _urlparse(self.flaresolverr_url) if self.flaresolverr_url else None
        self._flaresolverr_extra_hosts = (
            frozenset({_parsed_fs.hostname}) if _parsed_fs and _parsed_fs.hostname else frozenset()
        )

        logger.info(
            "SessionManager config: min_interval=%.1fs, rate_limit=%d/min, "
            "max_retries=%d, backoff=%.1f, max_concurrent=%d, timeout=%ds",
            self.min_request_interval, self._rate_limit_per_minute,
            MAX_RETRIES, RETRY_BACKOFF_FACTOR, MAX_CONCURRENT_REQUESTS, self.timeout,
        )

    def _create_session(self) -> cloudscraper.CloudScraper:
        """Create a new cloudscraper session with connection pool limits"""
        try:
            session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                },
                delay=10,
                debug=False
            )

            retry_strategy = Retry(
                total=MAX_RETRIES,
                backoff_factor=RETRY_BACKOFF_FACTOR,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"],
                raise_on_status=False
            )

            adapter = HTTPAdapter(
                pool_connections=MAX_POOL_CONNECTIONS,
                pool_maxsize=MAX_POOL_SIZE,
                max_retries=retry_strategy,
                pool_block=True
            )

            session.mount("http://", adapter)
            session.mount("https://", adapter)

            session.verify = True
            session.timeout = self.timeout

            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0',
            })

            logger.info(
                "Created cloudscraper session with pool_connections=%d, "
                "pool_maxsize=%d, pool_block=True",
                MAX_POOL_CONNECTIONS, MAX_POOL_SIZE,
            )
            return session

        except Exception as e:
            logger.error(f"Failed to create cloudscraper session: {e}")
            raise CloudflareError(f"Could not create session: {e}")

    def get_session(self) -> cloudscraper.CloudScraper:
        """Get or create a cloudscraper session (thread-safe)"""
        with self._lock:
            if self.session is None:
                self.session = self._create_session()
                self.request_count = 0
                logger.info("Created new cloudscraper session")

                # Re-inject stored FlareSolverr cookies if we have them
                if self._flaresolverr_cookies:
                    self._inject_flaresolverr_cookies(
                        self._flaresolverr_cookies,
                        self._flaresolverr_user_agent or "",
                    )

            return self.session

    def _wait_for_rate_limit(self):
        """Wait if necessary to respect both per-second and per-minute rate limits"""
        # --- Per-second throttle ---
        with self._rate_limit_lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            per_second_sleep = max(0, self.min_request_interval - time_since_last)

        if per_second_sleep > 0:
            logger.debug(f"Per-second rate limit: sleeping for {per_second_sleep:.2f}s")
            time.sleep(per_second_sleep)

        # --- Per-minute sliding window ---
        with self._rate_limit_lock:
            now = time.time()
            window_start = now - 60.0
            while self._request_timestamps and self._request_timestamps[0] < window_start:
                self._request_timestamps.popleft()

            per_minute_sleep = 0
            if len(self._request_timestamps) >= self._rate_limit_per_minute:
                oldest = self._request_timestamps[0]
                per_minute_sleep = max(0, oldest + 60.0 - now)

        if per_minute_sleep > 0:
            logger.info(
                "Per-minute rate limit (%d/%d): sleeping for %.2fs",
                len(self._request_timestamps), self._rate_limit_per_minute, per_minute_sleep,
            )
            time.sleep(per_minute_sleep)

        # --- Record this request ---
        with self._rate_limit_lock:
            # Re-evict after potential sleep
            now = time.time()
            window_start = now - 60.0
            while self._request_timestamps and self._request_timestamps[0] < window_start:
                self._request_timestamps.popleft()
            self._request_timestamps.append(now)
            self.last_request_time = now

    def _maybe_cleanup_connections(self):
        """Periodically cleanup idle connections to prevent resource leaks"""
        self.request_count += 1

        if self.request_count >= self.max_requests_before_cleanup:
            logger.info(f"Cleaning up connections after {self.request_count} requests")
            self._cleanup_idle_connections()
            self.request_count = 0

    def _cleanup_idle_connections(self):
        """Close idle connections in the pool"""
        with self._lock:
            if self.session:
                try:
                    for adapter in self.session.adapters.values():
                        if hasattr(adapter, 'poolmanager') and adapter.poolmanager:
                            adapter.poolmanager.clear()
                    logger.debug("Cleared idle connection pools")
                except Exception as e:
                    logger.warning(f"Error cleaning up connections: {e}")

    def _is_cloudflare_challenge(self, response) -> bool:
        """Detect if a response is a Cloudflare challenge page."""
        if response is None:
            return False

        # cf-ray header must be present to confirm Cloudflare is involved
        if "cf-ray" not in response.headers:
            return False

        # Status 403 or 503 with cf-ray is a challenge
        if response.status_code in (403, 503):
            return True

        # Status 200 but body contains challenge markers
        try:
            body = response.text[:2000]  # Only check beginning
        except Exception:
            return False

        challenge_markers = [
            "Just a moment",
            "challenge-platform",
            "cf-spinner",
            "cf_chl_opt",
        ]
        return any(marker in body for marker in challenge_markers)

    def _is_cloudflare_active(self, url: str) -> bool:
        """Quick pre-flight check: is Cloudflare blocking this URL?

        Uses plain requests (not cloudscraper) with a short timeout
        to avoid hanging. Only checks once — if we already have
        FlareSolverr cookies injected, skip the check.
        """
        # If we already have FlareSolverr cookies, check if they're still valid
        if self._flaresolverr_cookies:
            now = time.time()
            if all(c.get("expiry", now + 1) > now for c in self._flaresolverr_cookies):
                return False
            # Cookies expired — clear them and re-check
            logger.info("FlareSolverr cookies expired, re-running pre-flight check")
            self._flaresolverr_cookies = []
            self._flaresolverr_user_agent = None

        import requests as plain_requests
        try:
            resp = plain_requests.head(url, timeout=5, allow_redirects=True)
            return self._is_cloudflare_challenge(resp)
        except Exception:
            # If we can't even reach the site, let the normal flow handle it
            return False

    def _solve_with_flaresolverr(self, url: str) -> dict:
        """Use FlareSolverr to solve a Cloudflare challenge.

        Returns dict with keys: html, url, status, cookies, user_agent.
        """
        import requests as plain_requests

        try:
            logger.info(f"Calling FlareSolverr for: {url}")
            resp = plain_requests.post(
                self.flaresolverr_url,
                json={
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": self.flaresolverr_timeout * 1000,
                },
                timeout=self.flaresolverr_timeout + 10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise CloudflareError(
                f"FlareSolverr unreachable at {self.flaresolverr_url}: {e}. "
                "Ensure FlareSolverr is running (docker run -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest)"
            )

        if data.get("status") != "ok":
            msg = data.get("message", "Unknown error")
            raise CloudflareError(f"FlareSolverr failed: {msg}")

        solution = data.get("solution")
        if not solution:
            raise CloudflareError("FlareSolverr returned ok status but no solution data")

        logger.info("FlareSolverr solved challenge successfully")
        return {
            "html": solution.get("response", ""),
            "url": solution.get("url", url),
            "status": solution.get("status", 200),
            "cookies": solution.get("cookies", []),
            "user_agent": solution.get("userAgent", ""),
        }

    def _inject_flaresolverr_cookies(self, cookies: list, user_agent: str):
        """Inject FlareSolverr cookies into the current session and store for reuse."""
        self._flaresolverr_cookies = cookies
        self._flaresolverr_user_agent = user_agent

        session = self.session
        if session is None:
            return

        for cookie in cookies:
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain", ""),
            )

        if user_agent:
            session.headers["User-Agent"] = user_agent

        logger.info(f"Injected {len(cookies)} FlareSolverr cookies into session")

    def _create_response_from_html(self, html: str, url: str, status_code: int):
        """Create a requests.Response from FlareSolverr HTML."""
        import requests as plain_requests

        resp = plain_requests.Response()
        resp.status_code = status_code
        resp.url = url
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        resp.encoding = "utf-8"
        resp._content = html.encode("utf-8")
        return resp

    def _fallback_to_flaresolverr(self, url: str):
        """Solve a Cloudflare challenge via FlareSolverr and return the response."""
        should_solve = False
        with self._flaresolverr_solve_lock:
            if self._flaresolverr_solving:
                # Another thread is already solving — we'll wait outside the lock
                pass
            elif self._flaresolverr_cookies:
                # Cookies already available from a previous solve
                logger.info("Another thread already solved the challenge, reusing cookies")
                session = self.get_session()
                try:
                    resp = session.request("GET", url, timeout=min(self.timeout, 15))
                    if not self._is_cloudflare_challenge(resp):
                        resp.raise_for_status()
                        return resp
                except Exception:
                    pass  # Fall through to solve ourselves
                should_solve = True
                self._flaresolverr_solving = True
                self._flaresolverr_solve_done.clear()
            else:
                should_solve = True
                self._flaresolverr_solving = True
                self._flaresolverr_solve_done.clear()

        if not should_solve:
            # Wait for the other thread to finish solving
            self._flaresolverr_solve_done.wait(timeout=120)
            # Reuse the result that the solving thread stored
            logger.info("Reusing FlareSolverr result from another thread's solve")
            with self._flaresolverr_solve_lock:
                cached = self._flaresolverr_last_result
            if cached:
                resp = self._create_response_from_html(
                    cached["html"], cached.get("url", url), cached.get("status", 200)
                )
                return resp
            # Fallback: try a real request with the injected cookies
            session = self.get_session()
            resp = session.request("GET", url, timeout=min(self.timeout, 15))
            resp.raise_for_status()
            return resp

        try:
            result = self._solve_with_flaresolverr(url)
            self._inject_flaresolverr_cookies(result["cookies"], result["user_agent"])
            with self._flaresolverr_solve_lock:
                self._flaresolverr_last_result = result
            resp = self._create_response_from_html(result["html"], result["url"], result["status"])
            resp.raise_for_status()
            return resp
        finally:
            with self._flaresolverr_solve_lock:
                self._flaresolverr_solving = False
                self._flaresolverr_solve_done.set()

    def make_request(self, method: str, url: str, **kwargs) -> cloudscraper.requests.Response:
        """Make a request with Cloudflare challenge fallback via FlareSolverr."""
        acquired = self._request_semaphore.acquire(timeout=60)
        if not acquired:
            raise ScrapingError("Request timeout: too many concurrent requests")

        response = None
        try:
            validate_target_url(url, extra_allowed_hosts=self._flaresolverr_extra_hosts)

            self._wait_for_rate_limit()

            # Pre-flight: quick check with plain requests to detect Cloudflare
            # before calling cloudscraper (which hangs on challenges)
            if self._is_cloudflare_active(url):
                logger.warning(f"Cloudflare active for {url}, going straight to FlareSolverr")
                return self._fallback_to_flaresolverr(url)

            session = self.get_session()

            # Use short timeout for initial attempt to fail fast on challenges
            if "timeout" not in kwargs:
                kwargs["timeout"] = min(self.timeout, 15)

            logger.debug(f"Making {method.upper()} request to: {url}")

            try:
                response = session.request(method, url, **kwargs)
            except Timeout:
                # Timeout likely means cloudscraper is stuck on a challenge
                logger.warning(f"Request timed out for {url}, trying FlareSolverr")
                return self._fallback_to_flaresolverr(url)

            self._maybe_cleanup_connections()

            # Check for Cloudflare challenge
            if self._is_cloudflare_challenge(response):
                logger.warning("Cloudflare challenge detected, trying FlareSolverr")
                try:
                    response.close()
                except Exception:
                    pass
                return self._fallback_to_flaresolverr(url)

            # Detect stale-cookie redirects (e.g. /en/msg-dmca) that indicate
            # the session is no longer valid and needs a fresh FlareSolverr solve.
            if response.url and response.url != url:
                final_path = response.url.rsplit("/", 1)[-1] if "/" in response.url else ""
                if final_path.startswith("msg-"):
                    logger.warning(
                        "Redirected to error page %s (stale cookies?), re-solving via FlareSolverr",
                        response.url,
                    )
                    try:
                        response.close()
                    except Exception:
                        pass
                    # Invalidate cached cookies so FlareSolverr runs fresh
                    self._flaresolverr_cookies = []
                    self._flaresolverr_user_agent = None
                    return self._fallback_to_flaresolverr(url)

            response.raise_for_status()
            logger.debug(f"Request successful: {response.status_code}")
            return response

        except (CloudflareError, ScrapingError, ServiceUnavailableError):
            raise
        except ConnectionError as e:
            logger.error(f"Connection error: {e}")
            self._cleanup_on_error()
            raise ServiceUnavailableError(f"Connection error: {e}")
        except RequestException as e:
            if response:
                try:
                    response.close()
                except Exception:
                    pass
            if hasattr(e, "response") and e.response is not None:
                try:
                    e.response.close()
                except Exception:
                    pass
                status_code = e.response.status_code
                if status_code == 403:
                    raise CloudflareError("Access forbidden - Cloudflare protection active")
                elif status_code == 503:
                    raise ServiceUnavailableError("OpenSubtitles.org is temporarily unavailable")
                else:
                    raise ScrapingError(f"HTTP error {status_code}: {e}")
            else:
                raise ScrapingError(f"Request error: {e}")
        finally:
            self._request_semaphore.release()

    def _cleanup_on_error(self):
        """Cleanup connections when an error occurs"""
        try:
            self._cleanup_idle_connections()
        except Exception as e:
            logger.warning(f"Error during cleanup on error: {e}")

    def get(self, url: str, **kwargs) -> cloudscraper.requests.Response:
        """Make a GET request"""
        return self.make_request('GET', url, **kwargs)

    def post(self, url: str, **kwargs) -> cloudscraper.requests.Response:
        """Make a POST request"""
        return self.make_request('POST', url, **kwargs)

    def close(self):
        """Close the session and release all resources"""
        with self._lock:
            if self.session:
                try:
                    for adapter in self.session.adapters.values():
                        if hasattr(adapter, 'poolmanager') and adapter.poolmanager:
                            try:
                                adapter.poolmanager.clear()
                            except Exception as e:
                                logger.debug(f"Error clearing pool manager: {e}")
                        if hasattr(adapter, 'close'):
                            try:
                                adapter.close()
                            except Exception as e:
                                logger.debug(f"Error closing adapter: {e}")

                    try:
                        self.session.close()
                    except Exception as e:
                        logger.debug(f"Error closing session: {e}")

                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
                finally:
                    self.session = None
                    self.request_count = 0
                    logger.info("Closed cloudscraper session and released resources")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
