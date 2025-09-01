"""Session manager for cloudscraper integration"""

import logging
import time
from typing import Optional, Dict, Any
import cloudscraper
from requests.exceptions import RequestException, Timeout, ConnectionError

from ..utils.exceptions import CloudflareError, ScrapingError, ServiceUnavailableError

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages cloudscraper sessions for OpenSubtitles.org"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session: Optional[cloudscraper.CloudScraper] = None
        self.base_url = "https://www.opensubtitles.org"
        self.last_request_time = 0
        self.min_request_interval = 1.0  # Minimum seconds between requests
        
    def _create_session(self) -> cloudscraper.CloudScraper:
        """Create a new cloudscraper session"""
        try:
            # Create cloudscraper with more robust configuration
            session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                },
                delay=10,
                debug=False
            )
            
            # Configure session for better compatibility
            session.verify = True  # Enable SSL verification
            session.timeout = self.timeout
            
            # Set common headers
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0',
            })
            
            return session
            
        except Exception as e:
            logger.error(f"Failed to create cloudscraper session: {e}")
            raise CloudflareError(f"Could not create session: {e}")
    
    def get_session(self) -> cloudscraper.CloudScraper:
        """Get or create a cloudscraper session"""
        if self.session is None:
            self.session = self._create_session()
            logger.info("Created new cloudscraper session")
        
        return self.session
    
    def _wait_for_rate_limit(self):
        """Wait if necessary to respect rate limiting"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f} seconds")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def make_request(self, method: str, url: str, **kwargs) -> cloudscraper.requests.Response:
        """Make a request using cloudscraper with error handling"""
        self._wait_for_rate_limit()
        
        session = self.get_session()
        
        # Set timeout if not provided
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout
        
        try:
            logger.debug(f"Making {method.upper()} request to: {url}")
            response = session.request(method, url, **kwargs)
            
            # Check for Cloudflare challenge
            if 'cf-ray' in response.headers and response.status_code in [403, 503]:
                logger.warning("Cloudflare challenge detected, recreating session")
                self.session = None
                session = self.get_session()
                response = session.request(method, url, **kwargs)
            
            response.raise_for_status()
            logger.debug(f"Request successful: {response.status_code}")
            return response
            
        except Timeout as e:
            logger.error(f"Request timeout: {e}")
            raise ScrapingError(f"Request timeout: {e}")
            
        except ConnectionError as e:
            logger.error(f"Connection error: {e}")
            raise ServiceUnavailableError(f"Connection error: {e}")
            
        except RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                if status_code == 403:
                    logger.error("Access forbidden - possible Cloudflare block")
                    raise CloudflareError("Access forbidden - Cloudflare protection active")
                elif status_code == 503:
                    logger.error("Service unavailable")
                    raise ServiceUnavailableError("OpenSubtitles.org is temporarily unavailable")
                else:
                    logger.error(f"HTTP error {status_code}: {e}")
                    raise ScrapingError(f"HTTP error {status_code}: {e}")
            else:
                logger.error(f"Request error: {e}")
                raise ScrapingError(f"Request error: {e}")
    
    def get(self, url: str, **kwargs) -> cloudscraper.requests.Response:
        """Make a GET request"""
        return self.make_request('GET', url, **kwargs)
    
    def post(self, url: str, **kwargs) -> cloudscraper.requests.Response:
        """Make a POST request"""
        return self.make_request('POST', url, **kwargs)
    
    def close(self):
        """Close the session"""
        if self.session:
            self.session.close()
            self.session = None
            logger.info("Closed cloudscraper session")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()