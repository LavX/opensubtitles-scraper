"""IMDB title lookup service for resolving IMDB IDs to titles"""

import logging
import re
import time
from typing import Optional, Dict
from urllib.parse import urljoin

from .exceptions import ScrapingError

logger = logging.getLogger(__name__)


class IMDBLookupService:
    """Service to lookup movie/TV show titles from IMDB IDs"""
    
    def __init__(self, session_manager):
        self.session_manager = session_manager
        self.base_url = "https://www.imdb.com"
        self.cache = {}  # Simple in-memory cache
        self.cache_ttl = 3600  # 1 hour cache TTL
        
    def lookup_title(self, imdb_id: str) -> Optional[str]:
        """
        Lookup title from IMDB ID
        
        Args:
            imdb_id: IMDB ID (e.g., 'tt22497928')
            
        Returns:
            str: Movie/TV show title or None if not found
        """
        if not imdb_id or not imdb_id.startswith('tt'):
            return None
            
        # Check cache first
        cache_key = imdb_id
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            if time.time() - cached_data['timestamp'] < self.cache_ttl:
                logger.debug(f"Using cached title for {imdb_id}: {cached_data['title']}")
                return cached_data['title']
        
        try:
            logger.info(f"Looking up title for IMDB ID: {imdb_id}")
            
            # Construct IMDB URL
            imdb_url = f"{self.base_url}/title/{imdb_id}/"
            
            # Make request to IMDB
            response = self.session_manager.get(imdb_url)
            
            # Extract title from HTML
            title = self._extract_title_from_html(response.text)
            
            if title:
                # Cache the result
                self.cache[cache_key] = {
                    'title': title,
                    'timestamp': time.time()
                }
                logger.info(f"Successfully resolved {imdb_id} to: {title}")
                return title
            else:
                logger.warning(f"Could not extract title from IMDB page for {imdb_id}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to lookup title for {imdb_id}: {e}")
            return None
    
    def _extract_title_from_html(self, html_content: str) -> Optional[str]:
        """Extract title from IMDB HTML page"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Method 1: Look for the main title in h1 with data-testid
            title_element = soup.find('h1', {'data-testid': 'hero__pageTitle'})
            if title_element:
                title = title_element.get_text(strip=True)
                # Clean up the title (remove year and extra info)
                title = re.sub(r'\s*\(\d{4}\).*$', '', title).strip()
                return title
            
            # Method 2: Look for title in span with class
            title_element = soup.find('span', class_='hero__primary-text')
            if title_element:
                title = title_element.get_text(strip=True)
                title = re.sub(r'\s*\(\d{4}\).*$', '', title).strip()
                return title
            
            # Method 3: Look for title in meta tags
            meta_title = soup.find('meta', property='og:title')
            if meta_title and meta_title.get('content'):
                title = meta_title['content']
                # Clean up title from meta tag format
                title = re.sub(r'\s*\(\d{4}\).*$', '', title).strip()
                title = re.sub(r'\s*-\s*IMDb.*$', '', title).strip()
                return title
            
            # Method 4: Look in page title
            page_title = soup.find('title')
            if page_title:
                title_text = page_title.get_text(strip=True)
                # Extract title from format like "The Exchange (TV Series 2023) - IMDb"
                title_match = re.search(r'^([^(]+)', title_text)
                if title_match:
                    title = title_match.group(1).strip()
                    # Remove common suffixes
                    title = re.sub(r'\s*-\s*IMDb.*$', '', title).strip()
                    return title
            
            logger.warning("Could not find title using any extraction method")
            return None
            
        except Exception as e:
            logger.error(f"Error extracting title from HTML: {e}")
            return None
    
    def clear_cache(self):
        """Clear the title cache"""
        self.cache.clear()
        logger.info("IMDB title cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        current_time = time.time()
        valid_entries = sum(
            1 for entry in self.cache.values() 
            if current_time - entry['timestamp'] < self.cache_ttl
        )
        
        return {
            'total_entries': len(self.cache),
            'valid_entries': valid_entries,
            'expired_entries': len(self.cache) - valid_entries
        }