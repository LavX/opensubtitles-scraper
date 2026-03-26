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
    """Raised when Cloudflare challenge cannot be resolved"""
    pass


class ServiceUnavailableError(OpenSubtitlesScraperError):
    """Raised when OpenSubtitles.org is unavailable"""
    pass