"""URL allowlist validation to prevent SSRF attacks."""

from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({
    "www.opensubtitles.org", "opensubtitles.org",
    "dl.opensubtitles.org",
    "www.imdb.com", "imdb.com",
})


def validate_target_url(url: str, extra_allowed_hosts: frozenset = frozenset()) -> None:
    """Raise ValueError if url is not in the allowlist."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    all_allowed = ALLOWED_HOSTS | extra_allowed_hosts
    if parsed.hostname not in all_allowed:
        raise ValueError(f"URL host not allowed: {parsed.hostname}")
