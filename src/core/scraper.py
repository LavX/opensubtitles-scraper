"""Main scraper engine for OpenSubtitles.org"""

import logging
import re
import urllib.parse
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
            
            # Try direct download URL first (bypasses CAPTCHA issues)
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
        """Get subtitles for a specific episode from TV series page"""
        episode_response = None
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Look for the specific episode in the episode list
            # Find all episode links
            episode_links = soup.find_all('a', href=re.compile(r'/en/search/sublanguageid-all/imdbid-\d+'))
            
            target_episode_url = None
            logger.debug(f"Looking for episode {episode} in {len(episode_links)} episode links")
            
            # Episode 1 is the first link, episode 2 is the second, etc.
            if 1 <= episode <= len(episode_links):
                target_link = episode_links[episode - 1]  # Convert to 0-based index
                target_episode_url = target_link.get('href')
                if target_episode_url.startswith('/'):
                    target_episode_url = self.base_url + target_episode_url
                
                link_text = target_link.get_text(strip=True)
                logger.info(f"Found target episode {episode}: {link_text} -> {target_episode_url}")
            else:
                logger.warning(f"Episode {episode} not found (only {len(episode_links)} episodes available)")
            
            if not target_episode_url:
                logger.warning(f"Could not find episode S{season:02d}E{episode:02d} in series page")
                return []
            
            # Get subtitles from the specific episode page
            episode_response = self.session_manager.get(target_episode_url)
            episode_html = episode_response.text
            episode_response.close()
            episode_response = None
            
            episode_soup = BeautifulSoup(episode_html, 'html.parser')
            
            subtitles = []
            
            # Look for subtitle links in the episode page
            subtitle_links = episode_soup.find_all('a', href=re.compile(r'/en/subtitles/\d+'))
            
            for link in subtitle_links:
                try:
                    subtitle = self._parse_episode_subtitle_link(link, season, episode)
                    if subtitle:
                        subtitles.append(subtitle)
                except Exception as e:
                    logger.warning(f"Failed to parse episode subtitle link: {e}")
                    continue
            
            logger.info(f"Found {len(subtitles)} subtitles for S{season:02d}E{episode:02d}")
            return subtitles
            
        except Exception as e:
            logger.error(f"Failed to get episode subtitles: {e}")
            return []
        finally:
            # Ensure response is closed
            if episode_response:
                try:
                    episode_response.close()
                except Exception:
                    pass
    
    def _parse_episode_subtitle_link(self, link, season: int, episode: int) -> Optional[SubtitleInfo]:
        """Parse subtitle link from episode page"""
        try:
            subtitle_url = link.get('href')
            if subtitle_url.startswith('/'):
                subtitle_url = self.base_url + subtitle_url
            
            # Extract subtitle ID
            subtitle_id_match = re.search(r'/subtitles/(\d+)', subtitle_url)
            if not subtitle_id_match:
                return None
            subtitle_id = subtitle_id_match.group(1)
            
            # Extract language from URL
            language = self._extract_language_from_url(subtitle_url)
            if not language:
                language = "en"
            
            # Get link text and parent row for more info
            link_text = link.get_text(strip=True)
            row = link.find_parent('tr')
            
            # Extract release name from link text
            release_name = link_text
            
            # Generate filename
            filename = f"The.Exchange.S{season:02d}E{episode:02d}.{language}.srt"
            
            # Extract additional metadata from row
            uploader = "unknown"
            download_count = 0
            rating = 0.0
            
            if row:
                # Look for download count (pattern: "17x")
                row_text = row.get_text()
                count_match = re.search(r'(\d+)x', row_text)
                if count_match:
                    download_count = int(count_match.group(1))
                
                # Look for uploader
                uploader_link = row.find('a', href=re.compile(r'/user/'))
                if uploader_link:
                    uploader = uploader_link.get_text(strip=True)
            
            # Determine subtitle flags
            text_to_check = (link_text + " " + (row.get_text() if row else "")).lower()
            hearing_impaired = bool(re.search(r'\b(hi|hearing.impaired|sdh)\b', text_to_check))
            forced = bool(re.search(r'\b(forced|foreign)\b', text_to_check))
            
            return SubtitleInfo(
                subtitle_id=subtitle_id,
                language=language,
                filename=filename,
                release_name=release_name,
                uploader=uploader,
                download_count=download_count,
                rating=rating,
                hearing_impaired=hearing_impaired,
                forced=forced,
                fps=None,
                download_url=subtitle_url,
                upload_date=None
            )
            
        except Exception as e:
            logger.warning(f"Failed to parse episode subtitle link: {e}")
            return None
    
    def _extract_language_from_url(self, url: str) -> Optional[str]:
        """Extract language from subtitle URL"""
        try:
            # Pattern: /en/subtitles/ID/title-language
            url_parts = url.split('/')
            if len(url_parts) >= 4:
                last_part = url_parts[-1]  # e.g., "the-exchange-bank-of-tomorrow-nl"
                if '-' in last_part:
                    language = last_part.split('-')[-1]
                    if len(language) == 2:  # Valid language code
                        return language
            
            # Fallback: extract from URL path
            lang_match = re.search(r'/([a-z]{2})/', url)
            if lang_match:
                return lang_match.group(1)
            
            return None
            
        except Exception:
            return None

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
