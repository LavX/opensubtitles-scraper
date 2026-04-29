"""Parser for OpenSubtitles subtitle listings"""

import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime
from bs4 import BeautifulSoup

from ..utils.exceptions import ParseError
from ..utils.helpers import extract_subtitle_info, sanitize_filename

logger = logging.getLogger(__name__)

# ISO 639-1 language codes used on opensubtitles.org
_VALID_LANG_CODES = frozenset({
    'en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ko',
    'ar', 'nl', 'pl', 'tr', 'he', 'vi', 'th', 'sv', 'da', 'fi',
    'no', 'hr', 'bg', 'sr', 'sk', 'sl', 'uk', 'id', 'ms', 'hi',
    'bn', 'fa', 'ta', 'te', 'ur', 'et', 'lv', 'lt', 'ka', 'mk',
    'sq', 'bs', 'is', 'gl', 'eu', 'ca', 'hu', 'cs', 'ro', 'el',
})


class SubtitleInfo:
    """Represents a subtitle from OpenSubtitles"""
    
    def __init__(self, subtitle_id: str, language: str, filename: str,
                 release_name: str, uploader: str, download_count: int = 0,
                 rating: float = 0.0, hearing_impaired: bool = False,
                 forced: bool = False, fps: Optional[float] = None,
                 download_url: Optional[str] = None, upload_date: Optional[datetime] = None,
                 movie_year: Optional[int] = None):
        self.subtitle_id = subtitle_id
        self.language = language
        self.filename = filename
        self.release_name = release_name
        self.uploader = uploader
        self.download_count = download_count
        self.rating = rating
        self.hearing_impaired = hearing_impaired
        self.forced = forced
        self.fps = fps
        self.download_url = download_url
        self.upload_date = upload_date
        self.movie_year = movie_year
        
        # Extract additional info from filename
        file_info = extract_subtitle_info(filename)
        if not self.language and file_info['language']:
            self.language = file_info['language']
        if not self.hearing_impaired and file_info['hearing_impaired']:
            self.hearing_impaired = file_info['hearing_impaired']
        if not self.forced and file_info['forced']:
            self.forced = file_info['forced']
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'subtitle_id': self.subtitle_id,
            'language': self.language,
            'filename': self.filename,
            'release_name': self.release_name,
            'uploader': self.uploader,
            'download_count': self.download_count,
            'rating': self.rating,
            'hearing_impaired': self.hearing_impaired,
            'forced': self.forced,
            'fps': self.fps,
            'download_url': self.download_url,
            'upload_date': self.upload_date.isoformat() if self.upload_date else None,
            'movie_year': self.movie_year,
        }


class SubtitleParser:
    """Parser for OpenSubtitles subtitle listings"""
    
    def __init__(self, session_manager=None):
        self.base_url = "https://www.opensubtitles.org"
        self._session_manager = session_manager
    
    def parse_subtitle_page(self, html_content: str, movie_url: str) -> List[SubtitleInfo]:
        """Parse subtitle listing page"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            subtitles = []
            
            # Check if this is a TV series episode list page
            if self._is_episode_list_page(soup):
                logger.info("Detected TV series episode list page, extracting episode subtitles")
                return self._parse_episode_list_page(soup, movie_url)

            # Check if this is a single subtitle detail page (redirect when only one sub exists)
            if self._is_single_subtitle_page(soup):
                logger.info("Detected single subtitle detail page, extracting subtitle directly")
                return self._parse_single_subtitle_page(soup, movie_url)

            # Otherwise, parse as regular subtitle page
            # Look for subtitle table rows
            subtitle_rows = soup.find_all('tr', class_=re.compile(r'(subtitle|sub)', re.I))
            
            if not subtitle_rows:
                # Try alternative selectors
                subtitle_rows = soup.find_all('tr')
                # Filter out header rows and non-subtitle rows
                subtitle_rows = [row for row in subtitle_rows
                               if self._is_subtitle_row(row)]
            
            for row in subtitle_rows:
                try:
                    subtitle = self._parse_subtitle_row(row, movie_url)
                    if subtitle:
                        subtitles.append(subtitle)
                except Exception as e:
                    logger.warning(f"Failed to parse subtitle row: {e}")
                    continue
            
            logger.info(f"Parsed {len(subtitles)} subtitles from page")
            return subtitles
            
        except Exception as e:
            logger.error(f"Failed to parse subtitle page: {e}")
            raise ParseError(f"Subtitle page parsing failed: {e}")
    
    def _is_subtitle_row(self, row) -> bool:
        """Check if table row contains subtitle information"""
        try:
            # Skip header rows (first row)
            if row.find('th'):
                return False
            
            # Skip ad rows (contain iframe or colspan)
            if row.find('iframe') or any(td.get('colspan') for td in row.find_all('td')):
                return False
            
            # Look for subtitle links - based on our observation: /en/subtitles/ID/title
            has_subtitle_link = bool(row.find('a', href=re.compile(r'/en/subtitles/\d+')))
            
            # Must have main content cell with id starting with "main"
            has_main_cell = bool(row.find('td', id=re.compile(r'^main\d+')))
            
            return has_subtitle_link and has_main_cell
            
        except Exception:
            return False
    
    def _parse_subtitle_row(self, row, movie_url: str) -> Optional[SubtitleInfo]:
        """Parse individual subtitle table row"""
        try:
            # Find the main content cell (has id starting with "main")
            main_cell = row.find('td', id=re.compile(r'^main\d+'))
            if not main_cell:
                return None
            
            # Extract subtitle link - pattern: /en/subtitles/ID/title
            subtitle_link = main_cell.find('a', href=re.compile(r'/en/subtitles/\d+'))
            if not subtitle_link:
                return None
            
            subtitle_url = subtitle_link['href']
            if subtitle_url.startswith('/'):
                subtitle_url = self.base_url + subtitle_url
            
            # Extract subtitle ID from URL: /en/subtitles/13230498/avatar-en
            subtitle_id = self._extract_subtitle_id(subtitle_url)
            if not subtitle_id:
                return None
            
            # Extract title and year from link text
            title_text = subtitle_link.get_text(strip=True)

            # Extract year from title text, e.g. '"Game of Thrones" Winter Is Coming (2011)'
            movie_year = None
            year_match = re.search(r'\((\d{4})\)', title_text)
            if year_match:
                movie_year = int(year_match.group(1))

            # Extract language from URL (last part after /)
            language = self._extract_language_from_url(subtitle_url)
            if not language:
                language = "en"  # Default fallback
            
            # Extract release name from main cell - it's typically after the title link
            # The HTML structure is: <strong><a>Title (Year)</a></strong><br />release-name<br />
            release_name = title_text  # Default to title
            
            # Find the strong tag containing the title link
            strong_tag = main_cell.find('strong')
            if strong_tag:
                # Look for text after the strong tag, typically after a <br />
                # Get all siblings after strong_tag
                for sibling in strong_tag.next_siblings:
                    if isinstance(sibling, str):
                        text = sibling.strip()
                        if text and not text.startswith('Watch') and not text.startswith('Download'):
                            release_name = text
                            break
                    elif hasattr(sibling, 'name') and sibling.name == 'br':
                        continue  # Skip <br> tags
                    elif hasattr(sibling, 'get_text'):
                        text = sibling.get_text(strip=True)
                        if text and not text.startswith('Watch') and not text.startswith('Download'):
                            # Skip links with class 'p' (these are action links)
                            if hasattr(sibling, 'get') and sibling.get('class') and 'p' in sibling.get('class', []):
                                break  # Stop when we hit action links
                            release_name = text
                            break
            
            # Use release name as filename base if available
            if release_name and release_name != title_text:
                filename = f"{release_name.replace(' ', '.')}.{language}.srt"
            else:
                filename = f"{title_text.replace(' ', '.')}.{language}.srt"
            
            # Extract download count from row (pattern: "1234x")
            download_count = 0
            download_links = row.find_all('a', href=re.compile(r'/subtitleserve/sub/'))
            for dl_link in download_links:
                dl_text = dl_link.get_text(strip=True)
                count_match = re.search(r'(\d+)x', dl_text)
                if count_match:
                    download_count = int(count_match.group(1))
                    break
            
            # Extract FPS from row
            fps = None
            fps_spans = row.find_all('span', class_='p')
            for span in fps_spans:
                fps_text = span.get_text(strip=True)
                if re.match(r'\d+\.\d+', fps_text):
                    try:
                        fps = float(fps_text)
                    except ValueError:
                        pass
                    break
            
            # Extract uploader from row
            uploader = "unknown"
            uploader_link = row.find('a', href=re.compile(r'/en/profile/'))
            if uploader_link:
                uploader_text = uploader_link.get_text(strip=True)
                if uploader_text:
                    uploader = uploader_text
            
            # Extract upload date
            upload_date = self._extract_upload_date_from_row(row)
            
            # Rating from row (pattern: "8.8" or similar)
            rating = 0.0
            rating_spans = row.find_all('span', title=re.compile(r'\d+ votes'))
            for span in rating_spans:
                try:
                    rating = float(span.get_text(strip=True))
                except ValueError:
                    pass
                break
            
            # Determine hearing impaired and forced flags from filename/title
            row_text = row.get_text().lower()
            hearing_impaired = bool(re.search(r'\b(hearing[\s._-]?impaired|sdh|h\.i\.)\b', row_text, re.I) or re.search(r'\bHI\b', row_text))
            forced = bool(re.search(r'\b(forced|foreign)\b', row_text))

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
                fps=fps,
                download_url=subtitle_url,  # Use subtitle page URL for now
                upload_date=upload_date,
                movie_year=movie_year
            )
            
        except Exception as e:
            logger.warning(f"Failed to parse subtitle row: {e}")
            return None
    
    def _extract_subtitle_id(self, url: str) -> Optional[str]:
        """Extract subtitle ID from download URL"""
        try:
            # Common patterns for subtitle IDs in URLs
            patterns = [
                r'/subtitles/(\d+)',
                r'/download/(\d+)',
                r'id=(\d+)',
                r'sub_id=(\d+)',
                r'subtitle_id=(\d+)'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, url)
                if match:
                    return match.group(1)
            
            return None
            
        except Exception:
            return None
    
    def _extract_language_from_url(self, url: str) -> Optional[str]:
        """Extract language from subtitle URL"""
        try:
            # Pattern: /en/subtitles/ID/title-language
            # Extract the language part after the last dash
            url_parts = url.split('/')
            if len(url_parts) >= 4:
                last_part = url_parts[-1]  # e.g., "avatar-en"
                if '-' in last_part:
                    language = last_part.split('-')[-1]
                    if len(language) == 2 and language in _VALID_LANG_CODES:
                        return language

            # Fallback: extract from URL path
            lang_match = re.search(r'/([a-z]{2})/', url)
            if lang_match and lang_match.group(1) in _VALID_LANG_CODES:
                return lang_match.group(1)

            return None

        except Exception:
            return None
    
    def _is_episode_list_page(self, soup: BeautifulSoup) -> bool:
        """Check if this is a TV series episode list page (season overview).

        A season overview has a ``search_results`` table with season header rows
        (e.g. "Season 1") and episode rows with numbering like "1.Episode Title".
        A subtitle listing page (for a single episode) has ``td[id^=main]`` cells
        with individual subtitle links -- that is NOT an episode list page.
        """
        try:
            # If the page has subtitle detail rows (td with id=main*), it's a
            # subtitle listing page, not an episode list.
            if soup.find('td', id=re.compile(r'^main\d+')):
                return False

            # Look for season header rows in a search_results table
            table = soup.find('table', id='search_results')
            if table:
                row_texts = [row.get_text(strip=True) for row in table.find_all('tr')]
                for text in row_texts:
                    if re.match(r'^Season\s+\d+', text):
                        return True

            return False

        except Exception:
            return False
    
    def _is_single_subtitle_page(self, soup: BeautifulSoup) -> bool:
        """Return True when the page is a single subtitle detail page, not a listing.

        OpenSubtitles redirects to the detail page when a search yields exactly
        one result. The detail page has a classic /en/subtitleserve/sub/{id}
        download link but none of the td[id^=main] cells that listing pages use.
        """
        try:
            if soup.find('td', id=re.compile(r'^main\d+')):
                return False
            return bool(soup.find('a', href=re.compile(r'/en/subtitleserve/sub/\d+')))
        except Exception:
            return False

    def _parse_single_subtitle_page(self, soup: BeautifulSoup, page_url: str) -> List[SubtitleInfo]:
        """Extract a SubtitleInfo from a single subtitle detail page."""
        try:
            re_subtitleserver = re.compile(r'/en/subtitleserve/sub/(\d+)')
            serve_link = soup.find('a', href=re_subtitleserver)
            if not serve_link:
                logger.warning("No subtitleserve download link found on single subtitle page")
                return []

            subtitle_id_match = re_subtitleserver.search(serve_link['href'])
            if not subtitle_id_match:
                return []
            subtitle_id = subtitle_id_match.group(1)

            # Prefer the canonical URL for language extraction because the caller's
            # page_url may still be the original search URL (before the redirect).
            canonical_tag = soup.find('link', rel='canonical')
            canonical_url = (canonical_tag.get('href', '') if canonical_tag else '') or page_url

            language = self._extract_language_from_url(canonical_url)
            if not language:
                language = 'en'

            # Extract release / title from the page heading
            release_name = ''
            h2 = soup.find('h2')
            if h2:
                release_name = h2.get_text(strip=True)
            if not release_name:
                title_tag = soup.find('title')
                if title_tag:
                    release_name = title_tag.get_text(strip=True)

            subtitle_page_url = canonical_url or f"{self.base_url}/en/subtitles/{subtitle_id}"
            filename = f"{sanitize_filename(release_name) if release_name else subtitle_id}.{language}.srt"

            uploader = 'unknown'
            uploader_link = soup.find('a', href=re.compile(r'/en/profile/'))
            if uploader_link:
                uploader_text = uploader_link.get_text(strip=True)
                if uploader_text:
                    uploader = uploader_text

            logger.info(
                f"Parsed single subtitle: id={subtitle_id} lang={language} release='{release_name}'"
            )
            return [SubtitleInfo(
                subtitle_id=subtitle_id,
                language=language,
                filename=filename,
                release_name=release_name,
                uploader=uploader,
                download_count=0,
                rating=0.0,
                hearing_impaired=False,
                forced=False,
                fps=None,
                download_url=subtitle_page_url,
                upload_date=None,
            )]
        except Exception as e:
            logger.error(f"Failed to parse single subtitle page: {e}")
            return []

    def _parse_episode_list_page(self, soup: BeautifulSoup, series_url: str) -> List[SubtitleInfo]:
        """Parse TV series episode list page and get subtitles from episodes"""
        try:
            subtitles = []
            
            # Find all episode links
            episode_links = soup.find_all('a', href=re.compile(r'/en/search/sublanguageid-\w+/imdbid-\d+'))
            
            logger.info(f"Found {len(episode_links)} episodes to process")
            
            # For now, process first few episodes to avoid overwhelming the system
            for i, episode_link in enumerate(episode_links[:5]):  # Limit to first 5 episodes
                try:
                    episode_url = episode_link.get('href')
                    if episode_url.startswith('/'):
                        episode_url = self.base_url + episode_url
                    
                    episode_title = episode_link.get_text(strip=True)
                    logger.debug(f"Processing episode: {episode_title}")
                    
                    # Get subtitles for this episode
                    episode_subtitles = self._get_episode_subtitles(episode_url, episode_title)
                    subtitles.extend(episode_subtitles)
                    
                except Exception as e:
                    logger.warning(f"Failed to process episode {i+1}: {e}")
                    continue
            
            logger.info(f"Collected {len(subtitles)} subtitles from episodes")
            return subtitles
            
        except Exception as e:
            logger.error(f"Failed to parse episode list page: {e}")
            return []
    
    def _get_episode_subtitles(self, episode_url: str, episode_title: str) -> List[SubtitleInfo]:
        """Get subtitles for a specific episode"""
        response = None
        try:
            # Use the shared session manager instead of creating new ones
            if not self._session_manager:
                logger.warning("No session manager available, skipping episode subtitle fetch")
                return []
            
            response = self._session_manager.get(episode_url)
            # Read content and close response immediately to prevent file descriptor leak
            html_content = response.text
            response.close()
            response = None
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            subtitles = []
            
            # Look for subtitle links in this episode page
            subtitle_links = soup.find_all('a', href=re.compile(r'/en/subtitles/\d+'))
            
            for link in subtitle_links:
                try:
                    subtitle = self._parse_subtitle_link(link, episode_title, episode_url)
                    if subtitle:
                        subtitles.append(subtitle)
                except Exception as e:
                    logger.warning(f"Failed to parse subtitle link: {e}")
                    continue
            
            logger.debug(f"Found {len(subtitles)} subtitles for episode: {episode_title}")
            return subtitles
                
        except Exception as e:
            logger.warning(f"Failed to get subtitles for episode {episode_url}: {e}")
            return []
        finally:
            # Ensure response is closed to prevent file descriptor leak
            if response:
                try:
                    response.close()
                except Exception:
                    pass
    
    def _parse_subtitle_link(self, link, episode_title: str, episode_url: str) -> Optional[SubtitleInfo]:
        """Parse individual subtitle link from episode page"""
        try:
            subtitle_url = link.get('href')
            if subtitle_url.startswith('/'):
                subtitle_url = self.base_url + subtitle_url
            
            # Extract subtitle ID
            subtitle_id = self._extract_subtitle_id(subtitle_url)
            if not subtitle_id:
                return None
            
            # Extract language from URL
            language = self._extract_language_from_url(subtitle_url)
            if not language:
                language = "en"
            
            # Use link text as release name
            link_text = link.get_text(strip=True)
            release_name = link_text if link_text else episode_title
            
            # Generate filename
            filename = f"{episode_title.replace(' ', '.')}.{language}.srt"
            
            # Extract additional info from the row containing this link
            row = link.find_parent('tr')
            uploader = "unknown"
            download_count = 0
            rating = 0.0
            
            if row:
                # Try to extract uploader
                uploader_link = row.find('a', href=re.compile(r'/user/'))
                if uploader_link:
                    uploader = uploader_link.get_text(strip=True)
                
                # Try to extract download count
                row_text = row.get_text()
                count_match = re.search(r'(\d+)x', row_text)
                if count_match:
                    download_count = int(count_match.group(1))
            
            # Determine subtitle flags
            row_text = row.get_text().lower() if row else link_text.lower()
            hearing_impaired = bool(re.search(r'\b(hearing[\s._-]?impaired|sdh|h\.i\.)\b', row_text, re.I) or re.search(r'\bHI\b', row_text))
            forced = bool(re.search(r'\b(forced|foreign)\b', row_text))

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
            logger.warning(f"Failed to parse subtitle link: {e}")
            return None
    
    def _extract_upload_date_from_row(self, row) -> Optional[datetime]:
        """Extract upload date from table row"""
        try:
            # Look for date in cell
            date_cell = row.find('td', class_=re.compile(r'date|time', re.I))
            if date_cell:
                date_text = date_cell.get_text(strip=True)
                # Try to parse common date formats
                date_patterns = [
                    r'(\d{4}-\d{2}-\d{2})',
                    r'(\d{2}/\d{2}/\d{4})',
                    r'(\d{2}-\d{2}-\d{4})'
                ]
                
                for pattern in date_patterns:
                    match = re.search(pattern, date_text)
                    if match:
                        try:
                            return datetime.strptime(match.group(1), '%Y-%m-%d')
                        except ValueError:
                            try:
                                return datetime.strptime(match.group(1), '%m/%d/%Y')
                            except ValueError:
                                try:
                                    return datetime.strptime(match.group(1), '%d-%m-%Y')
                                except ValueError:
                                    continue
            
            return None
            
        except Exception:
            return None
