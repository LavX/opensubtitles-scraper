"""Pure Python solver for Anubis proof-of-work challenges.

Anubis (github.com/TecharoHQ/anubis) protects websites with several
challenge types:

- "fast"/"slow": SHA-256 proof-of-work. Find a nonce where
  SHA256(randomData + str(nonce)) has N leading hex zeros.
- "preact": Single SHA-256 hash of randomData with a mandatory
  time delay (difficulty * 80ms server-side).
- "metarefresh": Simple redirect, no computation needed.

At difficulty 4 the PoW takes ~65,536 hashes, well under a second.
"""

import hashlib
import json
import logging
import os
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

# Pattern to extract the challenge JSON from the HTML page
_CHALLENGE_PATTERN = re.compile(
    r'<script\s+id="anubis_challenge"[^>]*>\s*(.*?)\s*</script>',
    re.DOTALL,
)

# Pattern to detect metarefresh redirect
_META_REFRESH_PATTERN = re.compile(
    r'<meta\s+http-equiv=["\']refresh["\']\s+content=["\'](\d+);\s*url=([^"\']+)["\']',
    re.IGNORECASE,
)

# Cookie cache file for persistence across restarts
_COOKIE_CACHE_PATH = os.environ.get(
    "ANUBIS_COOKIE_CACHE", "/tmp/anubis_cookies.json"
)


def is_anubis_challenge(url: str, status_code: int = 0) -> bool:
    """Check if a URL or status code indicates an Anubis challenge."""
    return "/.within.website/" in url or (
        status_code in (307, 401, 403)
        and ".within.website" in url
    )


def extract_challenge(html: str) -> Optional[dict]:
    """Extract the Anubis challenge parameters from the HTML page.

    Handles all three challenge types: fast/slow (PoW), preact, metarefresh.
    Returns a dict with keys: id, randomData, difficulty, method
    or None if no challenge is found.
    """
    # Check for metarefresh first (simplest challenge)
    meta_match = _META_REFRESH_PATTERN.search(html)
    if meta_match and "/.within.website/" in meta_match.group(2):
        return {
            "method": "metarefresh",
            "redirect_url": meta_match.group(2),
            "delay": int(meta_match.group(1)),
            "id": None,
            "randomData": None,
            "difficulty": 0,
        }

    # Look for the challenge JSON (PoW and preact)
    match = _CHALLENGE_PATTERN.search(html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        challenge = data.get("challenge", {})
        method = challenge.get("method", "fast")
        return {
            "id": challenge["id"],
            "randomData": challenge["randomData"],
            "difficulty": challenge.get("difficulty", 4),
            "method": method,
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse Anubis challenge JSON: %s", e)
        return None


def solve_pow(random_data: str, difficulty: int) -> Tuple[int, str]:
    """Find a nonce where SHA256(randomData + str(nonce)) has N leading hex zeros.

    Returns (nonce, hash_hex).
    """
    prefix = "0" * difficulty
    nonce = 0

    while True:
        payload = (random_data + str(nonce)).encode("utf-8")
        hash_hex = hashlib.sha256(payload).hexdigest()
        if hash_hex.startswith(prefix):
            return nonce, hash_hex
        nonce += 1


def solve_preact(random_data: str, difficulty: int) -> Tuple[str, float]:
    """Solve a preact challenge: single SHA-256 hash with mandatory delay.

    Returns (hash_hex, delay_seconds).
    """
    hash_hex = hashlib.sha256(random_data.encode("utf-8")).hexdigest()
    # Server enforces difficulty * 80ms minimum, client uses 125ms
    delay = difficulty * 0.125
    return hash_hex, delay


def _load_cached_cookies(domain: str) -> Optional[dict]:
    """Load cached Anubis cookies from disk if they exist and haven't expired."""
    try:
        if not os.path.exists(_COOKIE_CACHE_PATH):
            return None
        with open(_COOKIE_CACHE_PATH, "r") as f:
            cache = json.load(f)
        entry = cache.get(domain)
        if not entry:
            return None
        # Cookies are valid for 7 days, check expiry
        if time.time() > entry.get("expires", 0):
            logger.info("Cached Anubis cookies for %s have expired", domain)
            return None
        logger.info("Loaded cached Anubis cookies for %s", domain)
        return entry.get("cookies")
    except Exception as e:
        logger.debug("Could not load Anubis cookie cache: %s", e)
        return None


def _save_cookies_to_cache(domain: str, cookies: dict) -> None:
    """Save Anubis cookies to disk for reuse across restarts."""
    try:
        cache = {}
        if os.path.exists(_COOKIE_CACHE_PATH):
            with open(_COOKIE_CACHE_PATH, "r") as f:
                cache = json.load(f)
        cache[domain] = {
            "cookies": cookies,
            # Default cookie TTL is 7 days, we use 6 to be safe
            "expires": time.time() + 6 * 24 * 3600,
        }
        with open(_COOKIE_CACHE_PATH, "w") as f:
            json.dump(cache, f)
        logger.debug("Saved Anubis cookies for %s to cache", domain)
    except Exception as e:
        logger.debug("Could not save Anubis cookie cache: %s", e)


def solve_anubis_challenge(
    session: requests.Session,
    challenge_url: str,
    original_url: str,
    timeout: int = 30,
) -> Optional[dict]:
    """Solve an Anubis challenge and return the resulting cookies.

    Handles all challenge types: fast/slow PoW, preact, metarefresh.

    Args:
        session: requests.Session to use for HTTP calls
        challenge_url: the /.within.website/?redir=... URL
        original_url: the URL the user originally requested
        timeout: HTTP timeout in seconds

    Returns:
        dict of cookies if solved, None on failure
    """
    start = time.monotonic()

    # Extract the redirect target from the challenge URL
    parsed = urlparse(challenge_url)
    qs = parse_qs(parsed.query)
    redir = qs.get("redir", [parsed.path])[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc

    # Check cookie cache first
    cached = _load_cached_cookies(domain)
    if cached:
        return cached

    challenge_page_url = challenge_url if challenge_url.startswith("http") else base + challenge_url

    try:
        # Fetch the challenge page HTML
        logger.info("Fetching Anubis challenge page: %s", challenge_page_url)
        resp = session.get(challenge_page_url, timeout=(10, timeout), allow_redirects=True)
        html = resp.text

        # Extract challenge parameters
        challenge = extract_challenge(html)
        if not challenge:
            logger.warning("Could not extract Anubis challenge from HTML (len=%d)", len(html))
            return None

        method = challenge["method"]
        logger.info(
            "Anubis challenge: method=%s, id=%s, difficulty=%d",
            method,
            challenge.get("id", "N/A"),
            challenge["difficulty"],
        )

        if method == "metarefresh":
            # Simple redirect, just follow it
            redirect_url = challenge["redirect_url"]
            if not redirect_url.startswith("http"):
                redirect_url = base + redirect_url
            logger.info("Metarefresh challenge, following redirect to: %s", redirect_url[:100])
            time.sleep(challenge.get("delay", 1))
            session.get(redirect_url, timeout=(10, timeout), allow_redirects=True)

        elif method == "preact":
            # Single hash with mandatory delay
            hash_hex, delay = solve_preact(challenge["randomData"], challenge["difficulty"])
            logger.info("Preact challenge: hash=%s..., waiting %.1fs", hash_hex[:16], delay)
            time.sleep(delay)

            # Submit via pass-challenge with 'result' param (preact format)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            pass_url = (
                f"{base}/.within.website/x/cmd/anubis/api/pass-challenge?"
                + urlencode({
                    "id": challenge["id"],
                    "result": hash_hex,
                    "redir": redir,
                    "elapsedTime": str(elapsed_ms),
                })
            )
            logger.info("Submitting preact solution")
            solve_resp = session.get(pass_url, timeout=(10, timeout), allow_redirects=False)
            if solve_resp.cookies:
                session.cookies.update(solve_resp.cookies)

        else:
            # PoW challenge (fast/slow)
            solve_start = time.monotonic()
            nonce, hash_hex = solve_pow(challenge["randomData"], challenge["difficulty"])
            solve_time = time.monotonic() - solve_start

            logger.info(
                "Anubis PoW solved: nonce=%d, hash=%s..., took %.3fs",
                nonce, hash_hex[:16], solve_time,
            )

            # Submit the solution
            elapsed_ms = int((time.monotonic() - start) * 1000)
            pass_url = (
                f"{base}/.within.website/x/cmd/anubis/api/pass-challenge?"
                + urlencode({
                    "id": challenge["id"],
                    "response": hash_hex,
                    "nonce": str(nonce),
                    "redir": redir,
                    "elapsedTime": str(elapsed_ms),
                })
            )

            logger.info("Submitting Anubis solution to: %s", pass_url[:120])
            # Don't follow redirects: the auth cookie is set on the 302
            # response itself. Following to the origin may hit Cloudflare
            # which could interfere with cookie collection.
            solve_resp = session.get(pass_url, timeout=(10, timeout), allow_redirects=False)
            # Manually update session cookies from the Set-Cookie headers
            if solve_resp.cookies:
                session.cookies.update(solve_resp.cookies)

        # Collect cookies
        cookies = {}
        for cookie in session.cookies:
            if "anubis" in cookie.name.lower() or cookie.name == "PHPSESSID":
                cookies[cookie.name] = cookie.value

        if cookies:
            logger.info(
                "Anubis solved successfully in %.1fs, got %d cookies: %s",
                time.monotonic() - start,
                len(cookies),
                list(cookies.keys()),
            )
            _save_cookies_to_cache(domain, cookies)
            return cookies
        else:
            logger.warning(
                "Anubis solution submitted but no auth cookies received"
            )
            return None

    except Exception as e:
        logger.error("Anubis solver failed: %s", e)
        return None
