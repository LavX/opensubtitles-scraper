"""Main scraper engine for OpenSubtitles.org"""

import logging
import re
from typing import List, Dict, Any, Optional

from .session_manager import SessionManager
from ..parsers.search_parser import SearchParser, SearchResult
from ..parsers.subtitle_parser import SubtitleParser, SubtitleInfo
from ..parsers.download_parser import DownloadParser
from ..utils.exceptions import SearchError, ScrapingError, DownloadError
from ..utils.helpers import build_url, normalize_title
from ..utils.imdb_lookup import IMDBLookupService

logger = logging.getLogger(__name__)


class OpenSubtitlesScraper:
    """Main scraper class for OpenSubtitles.org"""
    
    def __init__(self, timeout: int = 30):
        self.session_manager = SessionManager(timeout=timeout)
        self.search_parser = SearchParser()
        # Pass session manager to subtitle parser to avoid creating new sessions
        self.subtitle_parser = SubtitleParser(session_manager=self.session_manager)
        self.download_parser = DownloadParser()
        self.imdb_lookup = IMDBLookupService(self.session_manager)
        self.base_url = "https://www.opensubtitles.org"
        
    def search_movies(self, query: str, year: Optional[int] = None,
                     imdb_id: Optional[str] = None) -> List[SearchResult]:
        """Search for movies on OpenSubtitles.org"""
        try:
            logger.info(f"Searching for movies: query='{query}', year={year}, imdb_id={imdb_id}")
            
            # Handle empty query with IMDB ID - lookup title first
            original_query = query
            if imdb_id:
                imdb_results = self._search_by_imdb_id(imdb_id, kind="movie")
                if imdb_results:
                    filtered_results = self._filter_search_results(
                        imdb_results, original_query, year, imdb_id, kind="movie"
                    )
                    if filtered_results:
                        logger.info(
                            f"Found {len(filtered_results)} movies via IMDB ID search"
                        )
                        return filtered_results

            if not query.strip() and imdb_id:
                logger.info(f"Empty query with IMDB ID {imdb_id}, looking up title...")
                resolved_title = self.imdb_lookup.lookup_title(imdb_id)
                if resolved_title:
                    query = resolved_title
                    logger.info(f"Resolved IMDB ID {imdb_id} to title: {query}")
                else:
                    logger.warning(f"Could not resolve title for IMDB ID {imdb_id}")
                    return []
            
            # First try autocomplete search for quick results
            autocomplete_results = self._search_autocomplete(query)
            
            if autocomplete_results:
                # Filter results by year and IMDB ID if provided
                filtered_results = self._filter_search_results(
                    autocomplete_results, original_query, year, imdb_id, kind="movie"
                )
                if filtered_results:
                    logger.info(f"Found {len(filtered_results)} movies via autocomplete")
                    return filtered_results
            
            # If autocomplete doesn't yield good results, try full search
            search_results = self._search_full_page(query, kind="movie")
            filtered_results = self._filter_search_results(
                search_results, original_query, year, imdb_id, kind="movie"
            )
            
            logger.info(f"Found {len(filtered_results)} movies via full search")
            return filtered_results
            
        except Exception as e:
            logger.error(f"Movie search failed: {e}")
            raise SearchError(f"Movie search failed: {e}")
    
    def search_tv_shows(self, query: str, year: Optional[int] = None,
                       imdb_id: Optional[str] = None) -> List[SearchResult]:
        """Search for TV shows on OpenSubtitles.org"""
        try:
            logger.info(f"Searching for TV shows: query='{query}', year={year}, imdb_id={imdb_id}")
            
            # Handle empty query with IMDB ID - lookup title first
            original_query = query
            if imdb_id:
                imdb_results = self._search_by_imdb_id(imdb_id, kind="episode")
                if imdb_results:
                    filtered_results = self._filter_search_results(
                        imdb_results, original_query, year, imdb_id, kind="episode"
                    )
                    if filtered_results:
                        logger.info(
                            f"Found {len(filtered_results)} TV shows via IMDB ID search"
                        )
                        return filtered_results

            if not query.strip() and imdb_id:
                logger.info(f"Empty query with IMDB ID {imdb_id}, looking up title...")
                resolved_title = self.imdb_lookup.lookup_title(imdb_id)
                if resolved_title:
                    query = resolved_title
                    logger.info(f"Resolved IMDB ID {imdb_id} to title: {query}")
                else:
                    logger.warning(f"Could not resolve title for IMDB ID {imdb_id}")
                    return []
            
            # Try autocomplete search first
            autocomplete_results = self._search_autocomplete(query)
            
            if autocomplete_results:
                filtered_results = self._filter_search_results(
                    autocomplete_results, original_query, year, imdb_id, kind="episode"
                )
                if filtered_results:
                    logger.info(f"Found {len(filtered_results)} TV shows via autocomplete")
                    return filtered_results
            
            # Try full search
            search_results = self._search_full_page(query, kind="episode")
            filtered_results = self._filter_search_results(
                search_results, original_query, year, imdb_id, kind="episode"
            )
            
            logger.info(f"Found {len(filtered_results)} TV shows via full search")
            return filtered_results
            
        except Exception as e:
            logger.error(f"TV show search failed: {e}")
            raise SearchError(f"TV show search failed: {e}")
    
    def _search_autocomplete(self, query: str) -> List[SearchResult]:
        """Perform autocomplete search"""
        response = None
        try:
            # Use the proper search form submission
            search_url = build_url(self.base_url, "/en/search2", {
                "MovieName": query,
                "action": "search"
            })
            
            logger.debug(f"Making autocomplete request to: {search_url}")
            response = self.session_manager.get(search_url)
            
            # Parse autocomplete results - read content then close
            html_content = response.text
            results = self.search_parser.parse_search_autocomplete(html_content)
            return results
            
        except Exception as e:
            logger.warning(f"Autocomplete search failed: {e}")
            return []
        finally:
            # Always close response to release connection
            if response:
                try:
                    response.close()
                except Exception:
                    pass
    
    def _search_full_page(self, query: str, kind: str = "movie") -> List[SearchResult]:
        """Perform full page search"""
        response = None
        try:
            # Use the proper search form submission
            search_params = {
                "MovieName": query,
                "action": "search"
            }
            
            # Add kind-specific parameters
            if kind == "episode":
                search_params["SearchOnlyTVSeries"] = "on"
            else:
                search_params["SearchOnlyMovies"] = "on"
            
            search_url = build_url(self.base_url, "/en/search2", search_params)
            
            logger.debug(f"Making full search request to: {search_url}")
            response = self.session_manager.get(search_url)
            
            # Parse search results page - read content then close
            html_content = response.text
            results = self.search_parser.parse_search_page(html_content)
            return results
            
        except Exception as e:
            logger.warning(f"Full page search failed: {e}")
            return []
        finally:
            # Always close response to release connection
            if response:
                try:
                    response.close()
                except Exception:
                    pass

    def _search_by_imdb_id(
        self,
        imdb_id: str,
        kind: Optional[str] = None,
    ) -> List[SearchResult]:
        """Perform search using an IMDB ID without requiring a title lookup"""
        response = None
        try:
            imdb_number = re.sub(r"\D", "", imdb_id)
            if not imdb_number:
                return []

            search_path = f"/en/search/sublanguageid-all/imdbid-{imdb_number}"
            search_url = build_url(self.base_url, search_path)

            logger.debug(f"Making IMDB ID search request to: {search_url}")
            response = self.session_manager.get(search_url)

            html_content = response.text
            results = self.search_parser.parse_search_page(html_content)

            for result in results:
                if not result.imdb_id:
                    result.imdb_id = imdb_id

            if kind:
                results = [result for result in results if result.kind == kind]

            if results:
                return results

            if "/en/subtitles/" in html_content:
                return [
                    SearchResult(
                        title=imdb_id,
                        year=None,
                        imdb_id=imdb_id,
                        url=search_url,
                        subtitle_count=0,
                        kind=kind or "movie",
                    )
                ]

            return []

        except Exception as e:
            logger.warning(f"IMDB ID search failed: {e}")
            return []
        finally:
            if response:
                try:
                    response.close()
                except Exception:
                    pass
    
    def _filter_search_results(self, results: List[SearchResult], query: str,
                             year: Optional[int] = None, imdb_id: Optional[str] = None,
                             kind: Optional[str] = None) -> List[SearchResult]:
        """Filter and rank search results"""
        if not results:
            return []
        
        filtered = []
        normalized_query = normalize_title(query)
        
        for result in results:
            # Filter by content type if specified
            if kind and result.kind != kind:
                continue
            
            # Filter by IMDB ID if provided (exact match)
            if imdb_id and result.imdb_id and result.imdb_id != imdb_id:
                continue
            
            # Filter by year if provided (allow some tolerance)
            if year and result.year:
                year_diff = abs(result.year - year)
                if year_diff > 1:  # Allow 1 year difference for release variations
                    continue
            
            # Calculate relevance score
            normalized_title = normalize_title(result.title)
            
            # Exact match gets highest score
            if normalized_title == normalized_query:
                result.relevance_score = 100
            # Partial match
            elif normalized_query in normalized_title or normalized_title in normalized_query:
                result.relevance_score = 80
            # Word overlap
            else:
                query_words = set(normalized_query.split())
                title_words = set(normalized_title.split())
                overlap = len(query_words.intersection(title_words))
                total_words = len(query_words.union(title_words))
                result.relevance_score = (overlap / total_words) * 60 if total_words > 0 else 0
            
            # Boost score for exact year match
            if year and result.year == year:
                result.relevance_score += 10
            
            # Boost score for IMDB ID match
            if imdb_id and result.imdb_id == imdb_id:
                result.relevance_score += 20
            
            # Only include results with reasonable relevance
            if result.relevance_score >= 30:
                filtered.append(result)
        
        # Sort by relevance score (descending)
        filtered.sort(key=lambda x: getattr(x, 'relevance_score', 0), reverse=True)
        
        return filtered
    
    def get_movie_url(self, search_result: SearchResult) -> str:
        """Get the full URL for a movie/show from search result"""
        if search_result.url:
            if search_result.url.startswith('http'):
                return search_result.url
            else:
                return self.base_url + search_result.url
        
        # Fallback: construct URL from title and year
        if search_result.kind == "episode":
            path = "/en/ssearch"
        else:
            path = "/en/movies"
        
        return build_url(self.base_url, path, {"q": search_result.title})
    
    def get_subtitles(self, movie_url: str, languages: Optional[List[str]] = None,
                     season: Optional[int] = None, episode: Optional[int] = None) -> List[SubtitleInfo]:
        """Get subtitle listings for a movie/show"""
        response = None
        try:
            logger.info(f"Getting subtitles from: {movie_url}")
            
            # Modify URL to search for specific language to avoid pagination issues
            if languages:
                lang_code = languages[0].lower()
                movie_url = movie_url.replace('sublanguageid-all', f'sublanguageid-{lang_code}')
                logger.debug(f"Modified movie URL for language {lang_code}: {movie_url}")
            
            # Make request to movie/show page
            response = self.session_manager.get(movie_url)
            
            # Read content before closing
            html_content = response.text
            
            # Check if this is a TV series and we need specific episode
            if season and episode:
                logger.info(f"Looking for specific episode: S{season:02d}E{episode:02d}")
                subtitles = self._get_episode_subtitles(html_content, movie_url, season, episode, languages)
            else:
                # Parse subtitle listings normally
                subtitles = self.subtitle_parser.parse_subtitle_page(html_content, movie_url)
            
            # Filter by languages if specified
            if languages:
                # Create language mapping for common variations
                lang_mapping = {
                    'eng': 'en', 'english': 'en',
                    'hun': 'hu', 'hungarian': 'hu',
                    'spa': 'es', 'spanish': 'es',
                    'fre': 'fr', 'french': 'fr',
                    'ger': 'de', 'german': 'de',
                    'ita': 'it', 'italian': 'it',
                    'por': 'pt', 'portuguese': 'pt',
                    'rus': 'ru', 'russian': 'ru',
                    'chi': 'zh', 'chinese': 'zh',
                    'jpn': 'ja', 'japanese': 'ja',
                    'kor': 'ko', 'korean': 'ko',
                    'ara': 'ar', 'arabic': 'ar',
                    'dut': 'nl', 'dutch': 'nl',
                    'pol': 'pl', 'polish': 'pl'
                }
                
                # Normalize requested languages
                normalized_langs = set()
                for lang in languages:
                    lang_lower = lang.lower()
                    # Map 3-letter codes to 2-letter codes
                    normalized_lang = lang_mapping.get(lang_lower, lang_lower)
                    normalized_langs.add(normalized_lang)
                    # Also add original for exact matches
                    normalized_langs.add(lang_lower)
                
                logger.debug(f"Language filter: {languages} -> {normalized_langs}")
                subtitles = [sub for sub in subtitles if sub.language.lower() in normalized_langs]
            
            logger.info(f"Found {len(subtitles)} subtitles")
            return subtitles
            
        except Exception as e:
            logger.error(f"Failed to get subtitles: {e}")
            raise ScrapingError(f"Subtitle listing failed: {e}")
        finally:
            # Always close response to release connection
            if response:
                try:
                    response.close()
                except Exception:
                    pass
    
    def download_subtitle(self, subtitle_info: SubtitleInfo) -> Dict[str, Any]:
        """Download a subtitle file"""
        download_response = None
        download_page_response = None
        try:
            logger.info(f"Downloading subtitle: {subtitle_info.subtitle_id}")
            
            # Try direct download URL first (avoids CAPTCHA page)
            direct_download_url = f"https://dl.opensubtitles.org/en/download/sub/{subtitle_info.subtitle_id}"
            logger.debug(f"Attempting direct download from: {direct_download_url}")
            
            try:
                # Download directly using the dl.opensubtitles.org endpoint
                download_response = self.session_manager.get(direct_download_url)
                
                # Check if we got a ZIP file
                content_type = download_response.headers.get('content-type', '')
                if content_type.startswith('application/zip'):
                    # Read content before closing
                    zip_content = download_response.content
                    logger.info(f"Successfully downloaded ZIP file ({len(zip_content)} bytes)")
                    
                    # Close response immediately after reading content
                    download_response.close()
                    download_response = None
                    
                    # Extract subtitle from ZIP
                    subtitle_data = self.download_parser.extract_subtitle_from_zip(
                        zip_content,
                        subtitle_info.filename
                    )
                    
                    # Validate subtitle content
                    if not self.download_parser.validate_subtitle_content(subtitle_data['content']):
                        logger.warning("Downloaded content may not be a valid subtitle file")
                    
                    logger.info(f"Successfully downloaded subtitle: {subtitle_data['filename']}")
                    return subtitle_data
                else:
                    logger.warning(f"Direct download didn't return ZIP, got: {content_type}")
                    # Close response before trying fallback
                    if download_response:
                        download_response.close()
                        download_response = None
                    
            except Exception as e:
                logger.warning(f"Direct download failed: {e}, trying fallback method")
                # Ensure response is closed on error
                if download_response:
                    try:
                        download_response.close()
                    except Exception:
                        pass
                    download_response = None
            
            # Fallback: Use the original method with download page parsing
            if not subtitle_info.download_url:
                raise DownloadError("No download URL available")
            
            # First, get the download page to extract actual download link
            download_page_response = self.session_manager.get(subtitle_info.download_url)
            page_content = download_page_response.text
            download_page_response.close()
            download_page_response = None
            
            download_info = self.download_parser.parse_download_page(page_content)
            
            if download_info['requires_captcha']:
                raise DownloadError("CAPTCHA required for download")
            
            if download_info['wait_time'] > 0:
                logger.info(f"Waiting {download_info['wait_time']} seconds before download")
                import time
                time.sleep(download_info['wait_time'])
            
            # Get the actual download URL
            actual_download_url = download_info['download_url']
            if not actual_download_url:
                actual_download_url = subtitle_info.download_url
            
            # Download the subtitle file (usually a ZIP)
            download_response = self.session_manager.get(actual_download_url)
            
            # Read content before processing
            response_content = download_response.content
            response_headers = dict(download_response.headers)
            download_response.close()
            download_response = None
            
            # Extract subtitle from ZIP if needed
            if response_headers.get('content-type', '').startswith('application/zip') or \
               actual_download_url.endswith('.zip'):
                subtitle_data = self.download_parser.extract_subtitle_from_zip(
                    response_content,
                    subtitle_info.filename
                )
            else:
                # Direct subtitle file
                try:
                    content_text = response_content.decode('utf-8')
                except UnicodeDecodeError:
                    content_text = response_content.decode('latin1', errors='replace')
                
                subtitle_data = {
                    'filename': subtitle_info.filename,
                    'content': content_text,
                    'size': len(response_content),
                    'encoding': 'utf-8'
                }
            
            # Validate subtitle content
            if not self.download_parser.validate_subtitle_content(subtitle_data['content']):
                logger.warning("Downloaded content may not be a valid subtitle file")
            
            logger.info(f"Successfully downloaded subtitle: {subtitle_data['filename']}")
            return subtitle_data
            
        except Exception as e:
            logger.error(f"Failed to download subtitle: {e}")
            raise DownloadError(f"Subtitle download failed: {e}")
        finally:
            # Ensure all responses are closed
            for resp in [download_response, download_page_response]:
                if resp:
                    try:
                        resp.close()
                    except Exception:
                        pass
    
    def close(self):
        """Close the scraper and cleanup resources"""
        self.session_manager.close()
    
    def __enter__(self):
        return self
    
    def _get_episode_subtitles(self, html_content: str, series_url: str,
                              season: int, episode: int, languages: Optional[List[str]] = None) -> List[SubtitleInfo]:
        """Get subtitles for a specific episode from TV series page.

        The page has a ``search_results`` table structured as:
        - Season header rows with text like "Season 1"
        - Episode rows with first TD like "1.Winter Is Coming" and an <a>
          linking to ``/en/search/sublanguageid-XXX/imdbid-NNNNNNN``

        We locate the correct season header, then iterate rows until the next
        season header to find the episode by its number prefix.
        """
        episode_response = None
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')

            table = soup.find('table', id='search_results')
            if not table:
                logger.warning("No search_results table found on series page")
                return []

            rows = table.find_all('tr')
            logger.debug(f"search_results table has {len(rows)} rows")

            # --- 1. Find the target season section ---
            in_target_season = False
            target_episode_url = None
            episode_title = None

            for row in rows:
                row_text = row.get_text(strip=True)

                # Detect season header rows (e.g. "Season 1", "Season 2")
                season_match = re.match(r'^Season\s+(\d+)', row_text)
                if season_match:
                    s_num = int(season_match.group(1))
                    if s_num == season:
                        in_target_season = True
                        logger.debug(f"Entered target season {season}")
                    elif in_target_season:
                        # We passed the target season without finding the episode
                        logger.debug(f"Left target season {season} at season {s_num}")
                        break
                    continue

                if not in_target_season:
                    continue

                # --- 2. Inside the target season, look for episode rows ---
                # Episode rows have an <a> with href like /en/search/sublanguageid-XXX/imdbid-NNNNN
                ep_link = row.find('a', href=re.compile(r'/en/search/sublanguageid-\w+/imdbid-\d+'))
                if not ep_link:
                    continue

                # The first TD text starts with "N." (episode number)
                first_td = row.find('td')
                if not first_td:
                    continue
                td_text = first_td.get_text(strip=True)
                ep_num_match = re.match(r'^(\d+)\.', td_text)
                if not ep_num_match:
                    continue

                ep_num = int(ep_num_match.group(1))
                if ep_num == episode:
                    target_episode_url = ep_link.get('href')
                    if target_episode_url.startswith('/'):
                        target_episode_url = self.base_url + target_episode_url
                    episode_title = ep_link.get_text(strip=True)
                    logger.info(f"Found target episode S{season:02d}E{episode:02d}: {episode_title} -> {target_episode_url}")
                    break

            if not target_episode_url:
                logger.warning(f"Could not find episode S{season:02d}E{episode:02d} in series page")
                return []

            # --- 3. Fetch the episode subtitle listing page ---
            episode_response = self.session_manager.get(target_episode_url)
            episode_html = episode_response.text
            episode_response.close()
            episode_response = None

            # Use the subtitle_parser which already knows how to parse subtitle listing pages
            subtitles = self.subtitle_parser.parse_subtitle_page(episode_html, target_episode_url)

            # Attach episode metadata to each subtitle
            for sub in subtitles:
                if not getattr(sub, 'movie_name', None):
                    sub.movie_name = episode_title or ''

            logger.info(f"Found {len(subtitles)} subtitles for S{season:02d}E{episode:02d}")
            return subtitles

        except Exception as e:
            logger.error(f"Failed to get episode subtitles: {e}")
            return []
        finally:
            if episode_response:
                try:
                    episode_response.close()
                except Exception:
                    pass
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
