"""Parser for OpenSubtitles search results"""

import logging
import re
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup

from ..utils.exceptions import ParseError
from ..utils.helpers import extract_imdb_id, extract_year, normalize_title

logger = logging.getLogger(__name__)


class SearchResult:
    """Represents a search result from OpenSubtitles"""
    
    def __init__(self, title: str, year: Optional[int] = None, imdb_id: Optional[str] = None,
                 url: Optional[str] = None, subtitle_count: int = 0, kind: str = "movie"):
        self.title = title
        self.year = year
        self.imdb_id = imdb_id
        self.url = url
        self.subtitle_count = subtitle_count
        self.kind = kind  # "movie" or "episode"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'title': self.title,
            'year': self.year,
            'imdb_id': self.imdb_id,
            'url': self.url,
            'subtitle_count': self.subtitle_count,
            'kind': self.kind
        }


class SearchParser:
    """Parser for OpenSubtitles search functionality"""
    
    def __init__(self):
        self.base_url = "https://www.opensubtitles.org"
    
    def parse_search_autocomplete(self, html_content: str) -> List[SearchResult]:
        """Parse search results from the search results table"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            results = []
            
            # Look for the main search results table (id='search_results')
            search_table = soup.find('table', id='search_results')
            if not search_table:
                logger.warning("No search results table found with id='search_results'")
                return []
            
            # Parse table rows (skip header row)
            rows = search_table.find_all('tr')[1:]  # Skip header
            logger.debug(f"Found {len(rows)} result rows in search table")
            
            for row in rows:
                try:
                    result = self._parse_search_result_row(row)
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.warning(f"Failed to parse search result row: {e}")
                    continue
            
            logger.info(f"Parsed {len(results)} search results")
            return results
            
        except Exception as e:
            logger.error(f"Failed to parse search autocomplete: {e}")
            raise ParseError(f"Search parsing failed: {e}")
    
    def _parse_autocomplete_item(self, item) -> Optional[SearchResult]:
        """Parse individual autocomplete item"""
        try:
            # Extract title and metadata
            title_text = item.get_text(strip=True)
            if not title_text:
                return None
            
            # Extract URL
            url = None
            if item.name == 'a' and item.get('href'):
                url = item['href']
                if url.startswith('/'):
                    url = self.base_url + url
            
            # Parse title and year from text
            # Format examples: "Avatar (2009)", "Avatar: The Last Airbender (2005)"
            year_match = re.search(r'\((\d{4})\)', title_text)
            year = int(year_match.group(1)) if year_match else None
            
            # Clean title (remove year and extra info)
            clean_title = re.sub(r'\s*\(\d{4}\).*$', '', title_text).strip()
            
            # Extract subtitle count if available
            subtitle_count = 0
            count_match = re.search(r'(\d+)\s*subtitles?', title_text, re.I)
            if count_match:
                subtitle_count = int(count_match.group(1))
            
            # Determine if it's a movie or TV show
            kind = "movie"
            if any(keyword in title_text.lower() for keyword in ['series', 'season', 'episode', 'tv']):
                kind = "episode"
            
            # Extract IMDB ID if available in URL or data attributes
            imdb_id = None
            if url:
                imdb_id = extract_imdb_id(url)
            
            if not imdb_id and item.get('data-imdb'):
                imdb_id = item['data-imdb']
            
            return SearchResult(
                title=clean_title,
                year=year,
                imdb_id=imdb_id,
                url=url,
                subtitle_count=subtitle_count,
                kind=kind
            )
            
        except Exception as e:
            logger.warning(f"Failed to parse autocomplete item: {e}")
            return None
    
    def parse_search_page(self, html_content: str) -> List[SearchResult]:
        """Parse full search results page"""
        # Use the same logic as autocomplete since both use the search results table
        return self.parse_search_autocomplete(html_content)
    
    def _parse_search_result_row(self, row) -> Optional[SearchResult]:
        """Parse individual search result row from the search results table"""
        try:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 4:  # Need at least 4 cells based on the structure we saw
                return None
            
            # Skip rows that are ads (they have different structure)
            if any('ads' in cell.get('class', []) for cell in cells if cell.get('class')):
                return None
            
            # Based on the HTML structure we observed:
            # Cell 0: Movie poster image
            # Cell 1: Title and metadata (main content)
            # Cell 2: IMDB rating
            # Cell 3: Subtitle count
            # Cell 4: Upload date
            
            if len(cells) < 4:
                return None
            
            # Extract from the main content cell (usually cell 1)
            main_cell = cells[1] if len(cells) > 1 else cells[0]
            
            # Find the main title link
            title_link = main_cell.find('a', class_='bnone')
            if not title_link:
                # Fallback: any link in the main cell
                title_link = main_cell.find('a', href=True)
            
            if not title_link:
                return None
            
            title_text = title_link.get_text(strip=True)
            url = title_link.get('href')
            
            if not title_text or not url:
                return None
            
            # Ensure URL is absolute
            if url.startswith('/'):
                url = self.base_url + url
            
            # Extract year from title
            year = None
            year_match = re.search(r'\((\d{4})\)', title_text)
            if year_match:
                year = int(year_match.group(1))
            
            # Extract IMDB ID from IMDB rating cell (cell 2)
            imdb_id = None
            if len(cells) > 2:
                imdb_cell = cells[2]
                imdb_link = imdb_cell.find('a', href=lambda x: x and 'imdb.com' in x)
                if imdb_link:
                    imdb_match = re.search(r'tt(\d+)', imdb_link.get('href', ''))
                    if imdb_match:
                        imdb_id = f"tt{imdb_match.group(1)}"
            
            # Extract subtitle count from cell 3
            subtitle_count = 0
            if len(cells) > 3:
                count_cell = cells[3]
                count_text = count_cell.get_text(strip=True)
                if count_text.isdigit():
                    subtitle_count = int(count_text)
            
            # Determine content kind
            kind = "movie"
            # Check for TV series indicators
            if main_cell.find('img', title='TV Series') or '[S0' in title_text or 'Episode' in title_text:
                kind = "episode"
            
            # Clean title (remove year and episode info)
            clean_title = re.sub(r'\s*\(\d{4}\).*$', '', title_text).strip()
            clean_title = re.sub(r'\s*\[S\d+E\d+\].*$', '', clean_title).strip()
            
            # Remove quotes from TV episode titles
            clean_title = re.sub(r'^"([^"]+)".*$', r'\1', clean_title).strip()
            
            return SearchResult(
                title=clean_title,
                year=year,
                imdb_id=imdb_id,
                url=url,
                subtitle_count=subtitle_count,
                kind=kind
            )
            
        except Exception as e:
            logger.warning(f"Failed to parse search result row: {e}")
            return None
    
    def extract_movie_id_from_url(self, url: str) -> Optional[str]:
        """Extract movie/show ID from OpenSubtitles URL"""
        try:
            # Pattern: /en/movies/idmovies-123456
            match = re.search(r'/idmovies-(\d+)', url)
            if match:
                return match.group(1)
            
            # Pattern: /en/ssearch/idmovies-123456
            match = re.search(r'/ssearch/idmovies-(\d+)', url)
            if match:
                return match.group(1)
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to extract movie ID from URL {url}: {e}")
            return None