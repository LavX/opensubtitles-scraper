# OpenSubtitles.org Scraper

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.116.1-green)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-orange)](LICENSE)

A self-hosted service that scrapes OpenSubtitles.org and exposes Bazarr-compatible API endpoints. Drop-in replacement for the removed .org provider.

## Contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
  - [Just the scraper stack (recommended)](#just-the-scraper-stack-recommended)
  - [Full Bazarr+ stack](#full-bazarr-stack-bazarr--scraper--flaresolverr--ai-translator)
  - [Full media stack (arrstack)](#full-media-stack-arrstack)
  - [From source / development](#from-source--development)
- [API](#api)
- [Use with Bazarr](#use-with-bazarr)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)


## Why this exists

OpenSubtitles.org has been the go-to subtitle database for almost 20 years. Millions of subtitles, contributed by volunteers, freely accessible. That's changing.

The operator is phasing out .org in favour of .com. The problem is that **.com is not ready**. Their own developers have acknowledged missing subtitles, miscategorised episodes, broken sync, and unreliable search. For many languages and older titles, .org is still the only source with real coverage.

Meanwhile, every official way to access .org has been killed. Bazarr dropped its .org provider. The XML-RPC API was shut down. No migration path, no transition tooling. Users who depend on .org content that doesn't exist on .com yet were simply cut off. An upstream PR ([morpheus65535/bazarr#3012](https://github.com/morpheus65535/bazarr/pull/3012)) that re-added .org via a web scraper toggle was declined; the maintainer cited an agreement with the operator. Hence this service plus the [Bazarr+ fork](https://github.com/LavX/bazarr).

This project fills that gap. It gives you back access to .org until .com actually catches up. It ships with strict rate limits out of the box (1 req/s, 60 req/min) because the goal is access, not abuse.

If .org gets shut down before .com reaches parity, that's the operator's call. But it means cutting a community off from the archive it built, not protecting it.

> Rate limits are enforced by default and fully configurable. See [Configuration](#configuration).


## Features

- Cloudflare handling via [cloudscraper](https://github.com/VeNoMouS/cloudscraper) + [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) fallback
- Native [Anubis](https://github.com/TecharoHQ/anubis) proof-of-work solver in pure Python (no extra dependencies, 50 to 500 ms solve time)
- Automatic FlareSolverr integration when Cloudflare "Under Attack" mode is active
- Cookie caching: FlareSolverr/Anubis are only invoked once per challenge cycle, subsequent requests go direct
- Full language support with ISO 639-1/639-2 mapping
- FastAPI service with auto-generated docs at `/docs`
- Bazarr-compatible search and download endpoints
- HTML parsing for search results, subtitle listings, and downloads
- Built-in rate limiting, retry with backoff, and request queuing
- Thread-safe with double-solve prevention and rate limiter locking
- Pre-built Docker image at `ghcr.io/lavx/opensubtitles-scraper:latest`


## Architecture

```mermaid
graph LR
    A[Bazarr] --> B[FastAPI Service]
    B --> C[OpenSubtitles.org]
    subgraph "Scraper Service"
        B --> D[Session Manager]
        B --> E[Scraper]
        D -->|"CF detected"| J[FlareSolverr]
        D -->|"Anubis detected"| K[Anubis PoW Solver]
        J -->|"cookies"| D
        K -->|"cookies"| D
        E --> F[Search Parser]
        E --> G[Subtitle Parser]
        E --> H[Download Parser]
        E --> I[Bazarr Provider]
    end
```

### Challenge Resolution Flow

1. Pre-flight HEAD request detects Cloudflare or Anubis
2. **Cloudflare**: FlareSolverr solves the challenge via headless browser
3. **Anubis**: native Python solver brute-forces the SHA-256 PoW nonce (no browser needed)
4. Cookies + User-Agent are extracted and injected into the session
5. All subsequent requests use the cookies directly until they expire

FlareSolverr is optional. If not configured, the scraper falls back to cloudscraper alone. The Anubis solver is built in and always available.


## Quick Start

### Just the scraper stack (recommended)

Drop this into a `docker-compose.yml` and run `docker compose up -d`. No clone required, pulls the published image:

```yaml
services:
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    restart: unless-stopped
    environment:
      - LOG_LEVEL=info
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8191/health"]
      interval: 30s
      timeout: 10s
      start_period: 60s
      retries: 5

  opensubtitles-scraper:
    image: ghcr.io/lavx/opensubtitles-scraper:latest
    ports:
      - "8000:8000"
    restart: unless-stopped
    depends_on:
      flaresolverr:
        condition: service_healthy
    environment:
      - FLARESOLVERR_URL=http://flaresolverr:8191/v1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      start_period: 30s
      retries: 5
```

Service starts on `http://localhost:8000`. FlareSolverr comes up as a sidecar and the scraper waits for it to be healthy. Health check: `curl http://localhost:8000/health` returns `{"status": "healthy", ...}`.

To tune rate limits, retries, or pool sizes, drop a `.env` file next to the compose file (see [Configuration](#configuration)) and add `env_file: [.env]` to the scraper service.

To disable FlareSolverr, remove its block and unset `FLARESOLVERR_URL`. The Anubis solver is built in and still works.

### Full Bazarr+ stack (Bazarr + scraper + FlareSolverr + AI translator)

If you want the whole thing wired up, including a Bazarr fork that already knows how to talk to this scraper, the [Bazarr+ installer](https://lavx.github.io/bazarr/) does it in one shot:

```bash
curl -fsSL https://lavx.github.io/bazarr/install.sh | bash
```

The script ([source](https://github.com/LavX/bazarr/blob/gh-pages/install.sh)) generates a `docker-compose.yml`, a sane `.env`, and a starter `config/config.yaml`.

### Full media stack ([arrstack](https://lavx.github.io/arrstack/))

For an end-to-end homelab (qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr+, Jellyfin, Jellyseerr, FlareSolverr, Caddy, Trailarr, Recyclarr, optional Gluetun VPN, twelve services in total), use [arrstack](https://lavx.github.io/arrstack/). One command, ~90 seconds, TRaSH-Guides-aligned defaults, services pre-wired together:

```bash
curl -fsSL https://lavx.github.io/arrstack/install.sh | bash
```

Bazarr+ is included and already knows how to use this scraper.

### From source / development

```bash
git clone https://github.com/LavX/opensubtitles-scraper.git
cd opensubtitles-scraper
pip install vendor/cloudscraper-3.0.0.zip   # vendored fork, see notes
pip install -r requirements.txt
cp .env.example .env   # optional
python main.py
```

The repo's [docker-compose.yml](docker-compose.yml) builds from the local Dockerfile and is intended for development.


## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Quick health check (also at `/api/v1/health`) |
| `POST` | `/api/v1/search/movies` | Search movies |
| `POST` | `/api/v1/search/tv` | Search TV shows |
| `POST` | `/api/v1/subtitles` | List subtitles for a title |
| `POST` | `/api/v1/download/subtitle` | Download a subtitle file |
| `POST` | `/search`, `/api/v1/search` | Bazarr-compatible search shim |
| `POST` | `/download`, `/api/v1/download` | Bazarr-compatible download shim |
| `GET` | `/docs` | Swagger UI |

### Examples

```bash
# Health
curl http://localhost:8000/health

# Search
curl -X POST http://localhost:8000/api/v1/search/movies \
     -H "Content-Type: application/json" \
     -d '{"query": "Avatar", "year": 2009}'

# List subtitles
curl -X POST http://localhost:8000/api/v1/subtitles \
     -H "Content-Type: application/json" \
     -d '{"movie_url": "https://www.opensubtitles.org/en/movies/idmovies-123456", "languages": ["en", "es"]}'

# Download
curl -X POST http://localhost:8000/api/v1/download/subtitle \
     -H "Content-Type: application/json" \
     -d '{"subtitle_id": "123456", "download_url": "https://www.opensubtitles.org/download/..."}'
```


## Use with Bazarr

Three integration paths, in order of how most people will use this:

### 1. Bazarr+ fork (zero-config)

The [Bazarr+ fork](https://github.com/LavX/bazarr) ships with `opensubtitles` (the .org provider) wired up to talk to this service. If you used the [installer above](#full-bazarr-stack-bazarr--scraper--flaresolverr--ai-translator), nothing else to do. Log into Bazarr at `http://localhost:6767`, pick `OpenSubtitles.org` under **Settings → Providers**, and you're done.

The Bazarr+ container reaches the scraper via the internal Docker network using `OPENSUBTITLES_SCRAPER_URL=http://opensubtitles-scraper:8000`.

### 2. Stock (upstream) Bazarr

Stock Bazarr's `opensubtitles` provider was rewired to talk to the .com REST API after the .org [XML-RPC shutdown](https://forum.opensubtitles.org/viewtopic.php?t=19471). It does not scrape .org anymore.

#### Can I add this as a custom provider from the Bazarr UI?

No. Bazarr has no UI plugin slot, no provider upload, no "add by URL" form. The **Settings → Providers** screen is populated from a hardcoded list ([`frontend/src/pages/Settings/Providers/list.ts`](https://github.com/morpheus65535/bazarr/blob/master/frontend/src/pages/Settings/Providers/list.ts)) that ships baked into the image. The "+" button only enables providers that are already on that list and registered in the in-process `provider_registry`. The Whisper provider takes a URL, but it's a hardcoded entry pointing at a hardcoded API contract (transcription), not a generic custom-provider mechanism.

If you want this scraper exposed as a Bazarr provider, you have to ship a Bazarr image that knows about it. Use Bazarr+ (already done) or fork upstream and patch the files listed below.

#### How Bazarr loads providers

Bazarr providers are Python files in `custom_libs/subliminal_patch/providers/`. The directory's [`__init__.py`](https://github.com/morpheus65535/bazarr/blob/master/custom_libs/subliminal_patch/providers/__init__.py) iterates every `.py` file at startup and auto-registers any class ending in `Provider` into the global `provider_registry`. The actual code path is [here](https://github.com/morpheus65535/bazarr/blob/master/custom_libs/subliminal_patch/providers/__init__.py#L65-L114). So Python registration is automatic, but registration alone is not enough to make a provider usable, because Bazarr also needs:

- `bazarr/app/config.py`: validators for any provider-specific settings keys (`Validator('opensubtitles.scraper_service_url', ...)` and similar).
- `bazarr/app/get_providers.py`: a section in `get_providers_auth()` that passes those settings into the provider's `__init__`.
- `frontend/src/pages/Settings/Providers/list.ts`: the entry that makes the provider show up in **Settings → Providers**, with the right input fields.

These three live inside the Bazarr image, so you cannot add them by mounting a volume. You have to rebuild Bazarr.

#### Practical paths

1. **Run Bazarr+ instead** (recommended). It is stock Bazarr with exactly these patches applied, plus AI translation. Same SQLite schema and config layout, so swapping the image keeps your existing setup. Image: `ghcr.io/lavx/bazarr:latest`. See the [installer](#full-bazarr-stack-bazarr--scraper--flaresolverr--ai-translator).

2. **Build your own patched Bazarr image.** If you want to stay close to upstream, fork [morpheus65535/bazarr](https://github.com/morpheus65535/bazarr) and apply the four files Bazarr+ changed. The reference diffs are:
   - [`custom_libs/subliminal_patch/providers/opensubtitles_scraper.py`](https://github.com/LavX/bazarr/blob/development/custom_libs/subliminal_patch/providers/opensubtitles_scraper.py): new file, the `OpenSubtitlesScraperMixin` that talks to this service's `/api/v1` endpoints.
   - [`custom_libs/subliminal_patch/providers/opensubtitles.py`](https://github.com/LavX/bazarr/blob/development/custom_libs/subliminal_patch/providers/opensubtitles.py): `OpenSubtitlesProvider` inherits the mixin and routes `list_subtitles`/`download_subtitle` through the scraper when `scraper_service_url` is configured.
   - [`bazarr/app/config.py`](https://github.com/LavX/bazarr/blob/development/bazarr/app/config.py): adds `opensubtitles.use_web_scraper` and `opensubtitles.scraper_service_url` validators.
   - [`bazarr/app/get_providers.py`](https://github.com/LavX/bazarr/blob/development/bazarr/app/get_providers.py): passes those settings into the provider auth dict.
   - [`frontend/src/pages/Settings/Providers/list.ts`](https://github.com/LavX/bazarr/blob/development/frontend/src/pages/Settings/Providers/list.ts): replaces the OpenSubtitles.org provider's username/password fields with a single `Scraper Service URL` text input.

   Rebuild the image, set the URL in **Settings → Providers → OpenSubtitles.org**, point it at this scraper (e.g. `http://opensubtitles-scraper:8000`), and you're good.

3. **Skip Bazarr entirely** and use this scraper as a pure REST service from your own client (see [Direct HTTP](#4-direct-http-from-any-client) below).

### 3. Direct Python embedding

If you'd rather not run Bazarr at all, the scraper exposes a Python provider class you can use from your own code:

```python
from src.providers.opensubtitles_scraper_provider import OpenSubtitlesScraperProvider
from src.providers.base_provider import Language, Movie

provider = OpenSubtitlesScraperProvider()
provider.initialize()

video = Movie("Avatar.2009.1080p.BluRay.x264-GROUP", "Avatar", 2009)
subtitles = provider.list_subtitles(video, {Language('en'), Language('es')})

if subtitles:
    provider.download_subtitle(subtitles[0])
    print(subtitles[0].content.decode('utf-8'))

provider.terminate()
```

### 4. Direct HTTP from any client

Because the service is just a FastAPI app, anything that can POST JSON works. Hit the endpoints in the [API](#api) section. Swagger UI is at `/docs`.


## Configuration

All settings are configurable via environment variables or a `.env` file. Docker Compose loads `.env` automatically.

Copy the example and adjust:
```bash
cp .env.example .env
```

### Rate Limits

The service enforces rate limits in two places:

```
Bazarr ──► [ Inbound gate ] ──► Scraper ──► [ Outbound gate ] ──► OpenSubtitles.org
```

**Inbound (Bazarr → Scraper API):** protects the scraper from being overwhelmed by concurrent callers. When the limit is hit, the API responds `429 Too Many Requests` with a `Retry-After` header.

| Variable | Default | What it does |
|---|---|---|
| `SCRAPER_MAX_INFLIGHT_REQUESTS` | `2` | Max concurrent API requests being processed. Extra requests are queued or rejected. |
| `SCRAPER_QUEUE_TIMEOUT` | `30` | Seconds to wait in queue before returning 429. Gives in-flight FlareSolverr requests time to finish. |
| `SCRAPER_RETRY_AFTER_SECONDS` | `15` | Value of the `Retry-After` header sent with 429 responses. |

**Outbound (Scraper → OpenSubtitles.org):** prevents hammering .org. These are the core rate limits.

| Variable | Default | What it does |
|---|---|---|
| `SCRAPER_MIN_REQUEST_INTERVAL` | `1.0` | Min seconds between any two requests to .org (per-second throttle). |
| `SCRAPER_RATE_LIMIT_PER_MINUTE` | `60` | Max requests to .org in any 60-second sliding window. |
| `SCRAPER_MAX_CONCURRENT_REQUESTS` | `2` | Max simultaneous outbound connections to .org. |

Defaults enforce **1 req/s and 60 req/min to .org**, with at most **2 concurrent API requests** accepted from Bazarr.

### Other Settings

| Variable | Default | What it does |
|---|---|---|
| `SCRAPER_MAX_RETRIES` | `2` | Retries on 429/5xx from .org |
| `SCRAPER_RETRY_BACKOFF_FACTOR` | `2` | Backoff multiplier between retries |
| `SCRAPER_REQUEST_TIMEOUT` | `30` | HTTP timeout per request in seconds |
| `SCRAPER_MAX_POOL_CONNECTIONS` | `5` | Connection pools to cache |
| `SCRAPER_MAX_POOL_SIZE` | `3` | Connections per pool |
| `SCRAPER_MAX_REQUESTS_BEFORE_CLEANUP` | `20` | Requests between pool cleanup |
| `FLARESOLVERR_URL` | _(unset)_ | FlareSolverr endpoint (e.g. `http://flaresolverr:8191/v1`). Leave unset to disable. |
| `FLARESOLVERR_TIMEOUT` | `60` | Timeout in seconds for FlareSolverr challenge resolution |

> **FlareSolverr note:** When running via `docker-compose.yml`, `FLARESOLVERR_URL` is pre-configured to use the sidecar service. To disable FlareSolverr, comment out both the `flaresolverr` service and the `FLARESOLVERR_URL` environment line in the compose file.


## Project Structure

```
opensubtitles-scraper/
├── src/
│   ├── api/              # FastAPI routes and models
│   ├── core/             # Scraper engine and session management
│   ├── parsers/          # HTML parsing (search, subtitles, downloads)
│   ├── providers/        # Bazarr-compatible provider interface
│   └── utils/            # Exceptions and helpers
├── vendor/               # Vendored cloudscraper
├── main.py
├── requirements.txt
├── .env.example
├── Dockerfile
└── docker-compose.yml
```


## Troubleshooting

**Cloudflare blocks.** cloudscraper handles most challenges automatically. If it persists, FlareSolverr takes over (when configured) and the session recreates itself.

**Anubis loops.** If you see repeated `/.within.website/` redirects, the PoW solver is engaging. Check logs for `solve time` lines. Difficulty is set by the upstream challenge, but solves typically finish under a second.

**`429 Too Many Requests` from the scraper.** Your client is hitting the inbound queue limit. Increase `SCRAPER_MAX_INFLIGHT_REQUESTS` or back off; the response includes a `Retry-After` header.

**Parsing breaks.** .org layout changes can break parsers. Open an issue or PR with the failing URL.

**Connection errors.** Check your network. The service has built-in retry with exponential backoff.

**Debug logging:**
```bash
LOG_LEVEL=DEBUG python main.py
```

Or in Docker:
```bash
docker compose run -e LOG_LEVEL=DEBUG opensubtitles-scraper
```


## Contributing

1. Follow existing code patterns
2. Keep files under 500 lines
3. Include error handling and logging
4. Test against live .org data

### Contributors

- **[@salwinh](https://github.com/salwinh)**: language-specific URL filtering, TV series detection, subtitle metadata extraction
- **[@Zmegolaz](https://github.com/Zmegolaz)**: episode link matching fix for non-`all` language pages

---

## About

Maintained by **[LavX](https://lavx.hu)**.

Other projects:
- [arrstack](https://lavx.github.io/arrstack/): one-command installer for a 12-service self-hosted media stack (Sonarr, Radarr, Jellyfin, Bazarr+, this scraper, and more)
- [Bazarr (LavX Fork)](https://github.com/LavX/bazarr): automated subtitle management with .org scraper and AI translation
- [AI Subtitle Translator](https://github.com/LavX/ai-subtitle-translator): LLM-powered subtitle translation via OpenRouter
- [LMS Tools](https://tools.lavx.hu): 140+ free, privacy-focused dev tools

---

## License

MIT. See [LICENSE](LICENSE).

Use responsibly. Keep the default rate limits enabled.
