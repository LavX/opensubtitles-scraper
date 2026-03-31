"""Pure Python solver for Anubis proof-of-work challenges.

Anubis (github.com/TecharoHQ/anubis) protects websites with a SHA-256
proof-of-work challenge. The client must find a nonce such that
SHA256(randomData + str(nonce)) has N leading hex zeros, where N is the
difficulty parameter (typically 4-5).

At difficulty 4 the expected number of hashes is ~65,536, which takes
well under a second on modern hardware.
"""

import hashlib
import json
import logging
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


def is_anubis_challenge(url: str, status_code: int = 0) -> bool:
    """Check if a URL or status code indicates an Anubis challenge."""
    return "/.within.website/" in url or (
        status_code in (307, 401, 403)
        and ".within.website" in url
    )


def extract_challenge(html: str) -> Optional[dict]:
    """Extract the Anubis challenge parameters from the HTML page.

    Returns a dict with keys: id, randomData, difficulty, redir
    or None if no challenge is found.
    """
    match = _CHALLENGE_PATTERN.search(html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        challenge = data.get("challenge", {})
        return {
            "id": challenge["id"],
            "randomData": challenge["randomData"],
            "difficulty": challenge.get("difficulty", 4),
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse Anubis challenge JSON: %s", e)
        return None


def solve_pow(random_data: str, difficulty: int) -> Tuple[int, str]:
    """Find a nonce where SHA256(randomData + str(nonce)) has N leading hex zeros.

    Returns (nonce, hash_hex).
    """
    # Number of leading zero hex chars required
    prefix = "0" * difficulty
    nonce = 0

    while True:
        payload = (random_data + str(nonce)).encode("utf-8")
        hash_hex = hashlib.sha256(payload).hexdigest()
        if hash_hex.startswith(prefix):
            return nonce, hash_hex
        nonce += 1


def solve_anubis_challenge(
    session: requests.Session,
    challenge_url: str,
    original_url: str,
    timeout: int = 30,
) -> Optional[dict]:
    """Solve an Anubis PoW challenge and return the resulting cookies.

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

    # Build the full challenge page URL
    base = f"{parsed.scheme}://{parsed.netloc}"
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

        logger.info(
            "Anubis challenge: id=%s, difficulty=%d, randomData=%s...",
            challenge["id"],
            challenge["difficulty"],
            challenge["randomData"][:16],
        )

        # Solve the proof-of-work
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
        solve_resp = session.get(pass_url, timeout=(10, timeout), allow_redirects=True)

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
            return cookies
        else:
            logger.warning(
                "Anubis solution submitted but no auth cookies received (status=%d)",
                solve_resp.status_code,
            )
            return None

    except Exception as e:
        logger.error("Anubis solver failed: %s", e)
        return None
