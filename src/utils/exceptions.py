"""Custom exceptions for OpenSubtitles scraper"""


class OpenSubtitlesScraperError(Exception):
    """Base exception for OpenSubtitles scraper"""
    pass


class ScrapingError(OpenSubtitlesScraperError):
    """Raised when scraping fails"""
    pass


class ParseError(OpenSubtitlesScraperError):
    """Raised when parsing HTML fails"""
    pass


class DownloadError(OpenSubtitlesScraperError):
    """Raised when subtitle download fails"""
    pass


class SearchError(OpenSubtitlesScraperError):
    """Raised when search fails"""
    pass


class CloudflareError(OpenSubtitlesScraperError):
    """Raised when Cloudflare protection cannot be bypassed"""
    pass


class RateLimitError(OpenSubtitlesScraperError):
    """Raised when rate limit is exceeded"""
    pass


class AuthenticationError(OpenSubtitlesScraperError):
    """Raised when authentication fails"""
    pass


class ServiceUnavailableError(OpenSubtitlesScraperError):
    """Raised when OpenSubtitles.org is unavailable"""
    pass