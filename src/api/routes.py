"""FastAPI routes for OpenSubtitles scraper service"""

import base64
import logging
import os
import re as _re
import threading
import time
from contextlib import contextmanager
from fastapi import APIRouter, HTTPException, Depends

from .models import (
    SearchRequest, SearchResponse, SearchResult,
    SubtitleRequest, SubtitleResponse, SubtitleInfo,
    DownloadRequest, DownloadResponse,
    HealthResponse,
)
from ..core.scraper import OpenSubtitlesScraper
from ..utils.exceptions import SearchError, ScrapingError, DownloadError, CloudflareError, ServiceUnavailableError
from .. import __version__

# Map 2-letter ISO 639-1 codes to 3-letter OpenSubtitles codes (what Bazarr expects)
_LANG_2_TO_3 = {
    'en': 'eng', 'es': 'spa', 'fr': 'fre', 'de': 'ger', 'it': 'ita',
    'pt': 'por', 'ru': 'rus', 'zh': 'chi', 'ja': 'jpn', 'ko': 'kor',
    'ar': 'ara', 'nl': 'dut', 'pl': 'pol', 'hu': 'hun', 'cs': 'cze',
    'ro': 'rum', 'el': 'gre', 'tr': 'tur', 'he': 'heb', 'vi': 'vie',
    'th': 'tha', 'sv': 'swe', 'da': 'dan', 'fi': 'fin', 'no': 'nor',
    'hr': 'hrv', 'bg': 'bul', 'sr': 'scc', 'sk': 'slo', 'sl': 'slv',
    'uk': 'ukr', 'id': 'ind', 'ms': 'may', 'hi': 'hin', 'bn': 'ben',
    'fa': 'per', 'ta': 'tam', 'te': 'tel', 'ur': 'urd', 'et': 'est',
    'lv': 'lav', 'lt': 'lit', 'ka': 'geo', 'mk': 'mac', 'sq': 'alb',
    'bs': 'bos', 'is': 'ice', 'gl': 'glg', 'eu': 'baq', 'ca': 'cat',
}

_LANG_CODE_RE = _re.compile(r'^[a-z]{2,3}$')


def _validate_lang_code(code: str) -> bool:
    return bool(_LANG_CODE_RE.match(code))


logger = logging.getLogger(__name__)

# Global scraper instance
_scraper_instance = None
_start_time = time.time()
_last_error_time = 0.0

MAX_INFLIGHT_REQUESTS = max(
    int(os.environ.get("SCRAPER_MAX_INFLIGHT_REQUESTS", "2")),
    1,
)
REQUEST_QUEUE_TIMEOUT = max(
    float(os.environ.get("SCRAPER_QUEUE_TIMEOUT", "30")),
    0,
)
RETRY_AFTER_SECONDS = max(
    int(os.environ.get("SCRAPER_RETRY_AFTER_SECONDS", "15")),
    1,
)
# Hard wall-clock limit for any single request holding the semaphore.
# Prevents permanent deadlock when upstream calls hang.
MAX_REQUEST_DURATION = max(
    int(os.environ.get("SCRAPER_MAX_REQUEST_DURATION", "180")),
    30,
)

_request_semaphore = threading.BoundedSemaphore(MAX_INFLIGHT_REQUESTS)


@contextmanager
def request_limit(scope: str):
    """Limit concurrent requests and enforce a hard wall-clock deadline.

    Uses SCRAPER_QUEUE_TIMEOUT to decide how long to wait for a slot.
    Default 30s gives in-flight requests time to finish instead of
    rejecting immediately while FlareSolverr is solving a challenge.

    MAX_REQUEST_DURATION is a hard ceiling on how long a single request
    can hold the semaphore.  If exceeded, the semaphore is released and
    a 504 is returned, preventing permanent deadlock from hung upstream
    calls.
    """
    timeout = REQUEST_QUEUE_TIMEOUT if REQUEST_QUEUE_TIMEOUT > 0 else 30.0
    acquired = _request_semaphore.acquire(timeout=timeout)

    if not acquired:
        logger.warning("Rejecting %s request: scraper busy (waited %.0fs)", scope, timeout)
        raise HTTPException(
            status_code=429,
            detail=f"Scraper busy, retry after {RETRY_AFTER_SECONDS}s",
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        _request_semaphore.release()
        if elapsed > MAX_REQUEST_DURATION:
            logger.error(
                "Request %s exceeded wall-clock limit: %.1fs > %ds",
                scope, elapsed, MAX_REQUEST_DURATION,
            )


_scraper_lock = threading.Lock()


def get_scraper() -> OpenSubtitlesScraper:
    """Get or create scraper instance"""
    global _scraper_instance
    if _scraper_instance is None:
        with _scraper_lock:
            if _scraper_instance is None:
                _scraper_instance = OpenSubtitlesScraper()
    return _scraper_instance


# Create router
router = APIRouter(prefix="/api/v1", tags=["opensubtitles-scraper"])


@router.get("/health", response_model=HealthResponse)
def health_check():
    """Health check endpoint"""
    try:
        scraper = get_scraper()
        scraper_status = "healthy" if scraper else "unavailable"

        # Probe FlareSolverr
        flaresolverr_url = os.environ.get("FLARESOLVERR_URL", "")
        if not flaresolverr_url:
            flaresolverr_status = "not_configured"
        else:
            import requests as plain_requests
            try:
                base_url = flaresolverr_url.rsplit("/v1", 1)[0]
                r = plain_requests.get(f"{base_url}/health", timeout=3)
                flaresolverr_status = "available" if r.status_code == 200 else "unavailable"
            except Exception:
                flaresolverr_status = "unavailable"

        return HealthResponse(
            status="healthy",
            version=__version__,
            uptime=time.time() - _start_time,
            scraper_status=scraper_status,
            flaresolverr_status=flaresolverr_status,
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="unhealthy",
            version=__version__,
            uptime=time.time() - _start_time,
            scraper_status="error",
            flaresolverr_status="unknown",
        )


@router.post("/search/movies", response_model=SearchResponse)
def search_movies(request: SearchRequest, scraper: OpenSubtitlesScraper = Depends(get_scraper)):
    """Search for movies"""
    try:
        with request_limit("search_movies"):
            logger.info(f"Movie search request: {request.query}")
            
            results = scraper.search_movies(
                query=request.query,
                year=request.year,
                imdb_id=request.imdb_id
            )
            
            search_results = [
                SearchResult(
                    title=result.title,
                    year=result.year,
                    imdb_id=result.imdb_id,
                    url=result.url,
                    subtitle_count=result.subtitle_count,
                    kind=result.kind
                )
                for result in results
            ]
            
            return SearchResponse(
                results=search_results,
                total=len(search_results),
                query=request.query
            )
        
    except HTTPException:
        raise
    except SearchError as e:
        logger.error(f"Movie search failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except CloudflareError as e:
        logger.error(f"Cloudflare block: {e}")
        raise HTTPException(status_code=503, detail="Cloudflare protection active - retry later", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except ServiceUnavailableError as e:
        logger.error(f"Service unavailable: {e}")
        raise HTTPException(status_code=503, detail="OpenSubtitles.org temporarily unavailable", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except Exception as e:
        global _last_error_time
        _last_error_time = time.time()
        logger.exception(f"Unexpected error in movie search: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/search/tv", response_model=SearchResponse)
def search_tv_shows(request: SearchRequest, scraper: OpenSubtitlesScraper = Depends(get_scraper)):
    """Search for TV shows"""
    try:
        with request_limit("search_tv"):
            logger.info(f"TV show search request: {request.query}")
            
            results = scraper.search_tv_shows(
                query=request.query,
                year=request.year,
                imdb_id=request.imdb_id
            )
            
            search_results = [
                SearchResult(
                    title=result.title,
                    year=result.year,
                    imdb_id=result.imdb_id,
                    url=result.url,
                    subtitle_count=result.subtitle_count,
                    kind=result.kind
                )
                for result in results
            ]
            
            return SearchResponse(
                results=search_results,
                total=len(search_results),
                query=request.query
            )
        
    except HTTPException:
        raise
    except SearchError as e:
        logger.error(f"TV show search failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except CloudflareError as e:
        logger.error(f"Cloudflare block: {e}")
        raise HTTPException(status_code=503, detail="Cloudflare protection active - retry later", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except ServiceUnavailableError as e:
        logger.error(f"Service unavailable: {e}")
        raise HTTPException(status_code=503, detail="OpenSubtitles.org temporarily unavailable", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except Exception as e:
        global _last_error_time
        _last_error_time = time.time()
        logger.exception(f"Unexpected error in TV show search: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/subtitles", response_model=SubtitleResponse)
def get_subtitles(request: SubtitleRequest, scraper: OpenSubtitlesScraper = Depends(get_scraper)):
    """Get subtitle listings for a movie/show"""
    try:
        with request_limit("subtitles"):
            logger.info(f"Subtitle request for: {request.movie_url}")
            
            subtitles = scraper.get_subtitles(
                movie_url=request.movie_url,
                languages=request.languages,
                season=request.season,
                episode=request.episode,
            )
            
            subtitle_infos = [
                SubtitleInfo(
                    subtitle_id=sub.subtitle_id,
                    language=sub.language,
                    filename=sub.filename,
                    release_name=sub.release_name,
                    uploader=sub.uploader,
                    download_count=sub.download_count,
                    rating=sub.rating,
                    hearing_impaired=sub.hearing_impaired,
                    forced=sub.forced,
                    fps=sub.fps,
                    download_url=sub.download_url,
                    upload_date=sub.upload_date
                )
                for sub in subtitles
            ]
            
            return SubtitleResponse(
                subtitles=subtitle_infos,
                total=len(subtitle_infos),
                movie_url=request.movie_url
            )
        
    except HTTPException:
        raise
    except ScrapingError as e:
        logger.error(f"Subtitle listing failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except CloudflareError as e:
        logger.error(f"Cloudflare block: {e}")
        raise HTTPException(status_code=503, detail="Cloudflare protection active - retry later", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except ServiceUnavailableError as e:
        logger.error(f"Service unavailable: {e}")
        raise HTTPException(status_code=503, detail="OpenSubtitles.org temporarily unavailable", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except Exception as e:
        global _last_error_time
        _last_error_time = time.time()
        logger.exception(f"Unexpected error in subtitle listing: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/download/subtitle", response_model=DownloadResponse)
def download_subtitle(request: DownloadRequest, scraper: OpenSubtitlesScraper = Depends(get_scraper)):
    """Download a subtitle file"""
    try:
        with request_limit("download"):
            logger.info(f"Download request for subtitle: {request.subtitle_id}")
            
            # Create a minimal SubtitleInfo object for download
            from ..parsers.subtitle_parser import SubtitleInfo
            subtitle_info = SubtitleInfo(
                subtitle_id=request.subtitle_id,
                language="unknown",  # Will be determined during download
                filename=f"subtitle_{request.subtitle_id}.srt",
                release_name="",
                uploader="unknown",
                download_url=request.download_url
            )
            
            subtitle_data = scraper.download_subtitle(subtitle_info)
            encoded_content = base64.b64encode(subtitle_data['content'].encode('utf-8')).decode('utf-8')

            return DownloadResponse(
                filename=subtitle_data['filename'],
                content=encoded_content,
                size=subtitle_data['size'],
                encoding=subtitle_data.get('encoding', 'utf-8')
            )
        
    except HTTPException:
        raise
    except DownloadError as e:
        logger.error(f"Subtitle download failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except CloudflareError as e:
        logger.error(f"Cloudflare block: {e}")
        raise HTTPException(status_code=503, detail="Cloudflare protection active - retry later", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except ServiceUnavailableError as e:
        logger.error(f"Service unavailable: {e}")
        raise HTTPException(status_code=503, detail="OpenSubtitles.org temporarily unavailable", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except Exception as e:
        global _last_error_time
        _last_error_time = time.time()
        logger.exception(f"Unexpected error in subtitle download: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/search")
def bazarr_search(request: dict, scraper: OpenSubtitlesScraper = Depends(get_scraper)):
    """
    Bazarr-compatible search endpoint
    Handles the format that Bazarr's OpenSubtitles scraper implementation expects
    """
    try:
        with request_limit("bazarr_search"):
            logger.info("Bazarr search request: %d criteria", len(request.get('criteria', [])))
        
            # Extract criteria from Bazarr request format
            criteria = request.get('criteria', [])
            only_foreign = request.get('only_foreign', False)
            also_foreign = request.get('also_foreign', False)
            
            all_subtitles = []
            
            # Process each search criterion
            for criterion in criteria:
                subtitles = []
                
                # Handle IMDB ID search
                if 'imdbid' in criterion:
                    imdb_id = f"tt{criterion['imdbid']}"
                    season = criterion.get('season')
                    episode = criterion.get('episode')
                    series_title = None
                    movie_year = None

                    if season and episode:
                        # TV show search - use a generic query since we have IMDB ID
                        search_results = scraper.search_tv_shows(query="", imdb_id=imdb_id)
                    else:
                        # Movie search - use a generic query since we have IMDB ID
                        search_results = scraper.search_movies(query="", imdb_id=imdb_id)
                    
                    # Get subtitles for the first result
                    if search_results:
                        series_title = search_results[0].title if season else None
                        movie_year = getattr(search_results[0], 'year', None)
                        movie_url = search_results[0].url
                        languages = [
                            lang for lang in criterion.get('sublanguageid', '').split(',')
                            if lang and _validate_lang_code(lang)
                        ]
                        # Pass season and episode info for TV shows
                        subtitles = scraper.get_subtitles(
                            movie_url=movie_url,
                            languages=languages,
                            season=season,
                            episode=episode
                        )
                
                # Handle hash-based search (not supported by scraper, but we can try IMDB fallback)
                elif 'moviehash' in criterion:
                    logger.info("Hash-based search not supported by scraper, skipping")
                    continue
                
                # Handle tag search (not directly supported)
                elif 'tag' in criterion:
                    logger.info("Tag-based search not directly supported by scraper, skipping")
                    continue
                
                # Convert scraper subtitles to Bazarr format
                for sub in subtitles:
                    # Filter by foreign/forced preferences
                    if only_foreign and not sub.forced:
                        continue
                    elif not only_foreign and not also_foreign and sub.forced:
                        continue
                    
                    # Fix empty MovieName for TV episodes - this is critical for Bazarr matching
                    movie_name = getattr(sub, 'movie_name', '')
                    if season and episode and not movie_name and sub.release_name:
                        # Extract series name from release_name format: "Series Name" Episode Title
                        series_match = _re.match(r'^"([^"]+)"', sub.release_name)
                        if series_match:
                            # Use the full release_name as movie_name, but clean it up for Bazarr
                            movie_name = sub.release_name
                            # Clean up newlines, tabs, and extra spaces for proper regex matching in Bazarr
                            movie_name = movie_name.replace('\n', ' ').replace('\t', ' ')
                            movie_name = _re.sub(r'\s+', ' ', movie_name).strip()
                            logger.debug(f"Fixed empty MovieName for subtitle {sub.subtitle_id}: '{movie_name}'")
                        else:
                            # Fallback: try to construct from available data
                            movie_name = sub.release_name or f"Unknown Series S{season:02d}E{episode:02d}"
                            logger.debug(f"Fallback MovieName for subtitle {sub.subtitle_id}: '{movie_name}'")
                    # Ensure MovieName uses "SeriesName" EpisodeTitle format for Bazarr regex
                    if season and series_title and not movie_name.startswith('"'):
                        movie_name = f'"{series_title}" {movie_name}'
                    
                    # Convert to Bazarr-expected format
                    subtitle_data = {
                        'IDSubtitleFile': sub.subtitle_id,
                        'SubLanguageID': _LANG_2_TO_3.get(sub.language, sub.language),
                        'SubFileName': sub.filename,
                        'SubtitlesLink': f"/subtitle/{sub.subtitle_id}",
                        'MovieName': movie_name,  # ✅ Now properly populated for TV series
                        'MovieReleaseName': sub.release_name,
                        'MovieYear': str(sub.movie_year) if getattr(sub, 'movie_year', None) else (str(movie_year) if movie_year else ''),
                        'IDMovieImdb': criterion.get('imdbid', ''),
                        'SeriesIMDBParent': criterion.get('imdbid', '') if season else '',
                        'SeriesSeason': season or '',
                        'SeriesEpisode': episode or '',
                        'MovieKind': 'episode' if season else 'movie',
                        'SubHearingImpaired': '1' if sub.hearing_impaired else '0',
                        'SubForeignPartsOnly': '1' if sub.forced else '0',
                        'UserNickName': sub.uploader,
                        'SubDownloadsCnt': str(sub.download_count),
                        'SubRating': str(sub.rating),
                        'MovieFPS': str(sub.fps) if sub.fps else '',
                        'MatchedBy': 'imdbid' if 'imdbid' in criterion else 'hash',
                        'MovieHash': criterion.get('moviehash', ''),
                        'QueryParameters': criterion
                    }
                    all_subtitles.append(subtitle_data)
            
            # Return in Bazarr-expected format
            return {
                'status': '200 OK',
                'data': all_subtitles
            }
        
    except HTTPException:
        raise
    except CloudflareError as e:
        logger.error(f"Bazarr request blocked by Cloudflare: {e}")
        raise HTTPException(status_code=503, detail="Cloudflare protection active", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except ServiceUnavailableError as e:
        logger.error(f"Bazarr request - service unavailable: {e}")
        raise HTTPException(status_code=503, detail="OpenSubtitles.org temporarily unavailable", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except Exception as e:
        global _last_error_time
        _last_error_time = time.time()
        logger.exception(f"Bazarr search failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/download")
def bazarr_download(request: dict, scraper: OpenSubtitlesScraper = Depends(get_scraper)):
    """
    Bazarr-compatible download endpoint
    Handles the format that Bazarr's OpenSubtitles scraper implementation expects
    """
    try:
        with request_limit("bazarr_download"):
            logger.info("Bazarr download request: subtitle_id=%s", request.get('subtitle_id', 'N/A'))
        
            # Extract subtitle ID from request
            subtitle_id = request.get('subtitle_id')
            if not subtitle_id:
                raise HTTPException(status_code=400, detail="Missing subtitle_id")
            
            # Create a minimal SubtitleInfo object for download
            from ..parsers.subtitle_parser import SubtitleInfo
            
            # Construct the proper OpenSubtitles download URL from subtitle ID
            download_url = f"https://www.opensubtitles.org/en/subtitles/{subtitle_id}"
            
            subtitle_info = SubtitleInfo(
                subtitle_id=subtitle_id,
                language="unknown",
                filename=f"subtitle_{subtitle_id}.srt",
                release_name="",
                uploader="unknown",
                download_url=download_url  # ✅ Now properly set
            )
            
            logger.debug(f"Attempting to download subtitle from: {download_url}")
            
            subtitle_data = scraper.download_subtitle(subtitle_info)
            
            # Return in Bazarr-expected format (base64 encoded content)
            encoded_content = base64.b64encode(subtitle_data['content'].encode('utf-8')).decode('utf-8')
            
            return {
                'status': '200 OK',
                'data': encoded_content  # ✅ Return as string, not list
            }
        
    except HTTPException:
        raise
    except CloudflareError as e:
        logger.error(f"Bazarr request blocked by Cloudflare: {e}")
        raise HTTPException(status_code=503, detail="Cloudflare protection active", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except ServiceUnavailableError as e:
        logger.error(f"Bazarr request - service unavailable: {e}")
        raise HTTPException(status_code=503, detail="OpenSubtitles.org temporarily unavailable", headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    except Exception as e:
        global _last_error_time
        _last_error_time = time.time()
        logger.exception(f"Bazarr download failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Cleanup function
def cleanup_scraper():
    """Cleanup scraper resources"""
    global _scraper_instance
    if _scraper_instance:
        _scraper_instance.close()
        _scraper_instance = None
