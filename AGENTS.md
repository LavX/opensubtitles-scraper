# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Build/Lint/Test Commands

- `pip install -r requirements.txt` - Install dependencies (Note: cloudscraper is included as a vendored dependency in vendor/ directory as pip version is outdated)
- `python main.py` - Run the service (starts on http://localhost:8000)


## Code Style Guidelines

- Keep files under 500 lines as specified in README.md
- Use cloudscraper for all HTTP requests to OpenSubtitles.org (not requests library directly)
- cloudscraper is included as a vendored dependency in vendor/cloudscraper/ directory since pip version is outdated
- Follow existing patterns for error handling with custom exceptions
- Use the SessionManager for all HTTP requests (automatic Cloudflare bypass)
- Use the OpenSubtitlesScraper class as the main interface for scraping operations
- Follow the existing provider pattern for Bazarr compatibility
- Use the existing logging configuration (logging.basicConfig in main.py)
- Use type hints for all function parameters and return values
- Follow the existing directory structure:
  - `src/api/` - FastAPI web service
  - `src/core/` - Core scraping engine
  - `src/parsers/` - HTML parsing modules
  - `src/providers/` - Bazarr-compatible providers
  - `src/utils/` - Utility modules

## Key Components

- `src/core/session_manager.py` - CloudScraper session management (use this for all HTTP requests)
- `src/core/scraper.py` - Main scraper class (OpenSubtitlesScraper)
- `src/providers/opensubtitles_scraper_provider.py` - Bazarr-compatible provider
- `src/api/routes.py` - FastAPI endpoints
- `src/parsers/` - HTML parsing modules for search results, subtitle listings, and downloads
- `vendor/cloudscraper/` - Vendored cloudscraper library (newer than pip version)
