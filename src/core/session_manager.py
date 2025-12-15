"""Session manager for cloudscraper integration"""

import logging
import time
import threading
from typing import Optional, Dict, Any
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.exceptions import RequestException, Timeout, ConnectionError

from ..utils.exceptions import CloudflareError, ScrapingError, ServiceUnavailableError

logger = logging.getLogger(__name__)


# Connection pool limits to prevent "Too many open files" error
MAX_POOL_CONNECTIONS = 10  # Maximum number of connection pools to cache
MAX_POOL_SIZE = 5  # Maximum number of connections per pool
MAX_RETRIES = 3  # Maximum retries for failed requests


class SessionManager:
    """Manages cloudscraper sessions for OpenSubtitles.org"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session: Optional[cloudscraper.CloudScraper] = None
        self.base_url = "https://www.opensubtitles.org"
        self.last_request_time = 0
        self.min_request_interval = 1.0  # Minimum seconds between requests
        self.request_count = 0
        self.max_requests_before_cleanup = 50  # Cleanup connections after this many requests
        self._lock = threading.Lock()  # Thread safety for session access
        
    def _create_session(self) -> cloudscraper.CloudScraper:
        """Create a new cloudscraper session with connection pool limits"""
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
            
            # Configure retry strategy
            retry_strategy = Retry(
                total=MAX_RETRIES,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"]
            )
            
            # Configure HTTP adapter with connection pool limits
            adapter = HTTPAdapter(
                pool_connections=MAX_POOL_CONNECTIONS,
                pool_maxsize=MAX_POOL_SIZE,
                max_retries=retry_strategy,
                pool_block=False  # Don't block when pool is full, create new connection
            )
            
            # Mount adapter for both HTTP and HTTPS
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            
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
            
            logger.info(f"Created cloudscraper session with pool_connections={MAX_POOL_CONNECTIONS}, pool_maxsize={MAX_POOL_SIZE}")
            return session
            
        except Exception as e:
            logger.error(f"Failed to create cloudscraper session: {e}")
            raise CloudflareError(f"Could not create session: {e}")
    
    def get_session(self) -> cloudscraper.CloudScraper:
        """Get or create a cloudscraper session (thread-safe)"""
        with self._lock:
            if self.session is None:
                self.session = self._create_session()
                self.request_count = 0
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
    
    def _maybe_cleanup_connections(self):
        """Periodically cleanup idle connections to prevent resource leaks"""
        self.request_count += 1
        
        if self.request_count >= self.max_requests_before_cleanup:
            logger.info(f"Cleaning up connections after {self.request_count} requests")
            self._cleanup_idle_connections()
            self.request_count = 0
    
    def _cleanup_idle_connections(self):
        """Close idle connections in the pool"""
        with self._lock:
            if self.session:
                try:
                    # Get all adapters and close their connection pools
                    for adapter in self.session.adapters.values():
                        if hasattr(adapter, 'poolmanager') and adapter.poolmanager:
                            adapter.poolmanager.clear()
                    logger.debug("Cleared idle connection pools")
                except Exception as e:
                    logger.warning(f"Error cleaning up connections: {e}")
    
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
            
            # Periodic connection cleanup
            self._maybe_cleanup_connections()
            
            # Check for Cloudflare challenge
            if 'cf-ray' in response.headers and response.status_code in [403, 503]:
                logger.warning("Cloudflare challenge detected, recreating session")
                self.close()  # Properly close old session
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
        """Close the session and release all resources"""
        with self._lock:
            if self.session:
                try:
                    # Close all adapters to release connection pools
                    for adapter in self.session.adapters.values():
                        if hasattr(adapter, 'close'):
                            adapter.close()
                    self.session.close()
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
                finally:
                    self.session = None
                    self.request_count = 0
                    logger.info("Closed cloudscraper session and released resources")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()