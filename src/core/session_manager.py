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
MAX_POOL_CONNECTIONS = 5  # Maximum number of connection pools to cache (reduced from 10)
MAX_POOL_SIZE = 3  # Maximum number of connections per pool (reduced from 5)
MAX_RETRIES = 2  # Maximum retries for failed requests (reduced from 3)
MAX_CONCURRENT_REQUESTS = 2  # Maximum concurrent requests


class SessionManager:
    """Manages cloudscraper sessions for OpenSubtitles.org"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session: Optional[cloudscraper.CloudScraper] = None
        self.base_url = "https://www.opensubtitles.org"
        self.last_request_time = 0
        self.min_request_interval = 1.5  # Minimum seconds between requests (increased from 1.0)
        self.request_count = 0
        self.max_requests_before_cleanup = 20  # Cleanup connections after this many requests (reduced from 50)
        self._lock = threading.Lock()  # Thread safety for session access
        self._request_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)  # Limit concurrent requests
        
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
            
            # Configure retry strategy with backoff
            retry_strategy = Retry(
                total=MAX_RETRIES,
                backoff_factor=2,  # Exponential backoff: 2, 4, 8 seconds
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"],
                raise_on_status=False  # Don't raise immediately, let us handle it
            )
            
            # Configure HTTP adapter with strict connection pool limits
            adapter = HTTPAdapter(
                pool_connections=MAX_POOL_CONNECTIONS,
                pool_maxsize=MAX_POOL_SIZE,
                max_retries=retry_strategy,
                pool_block=True  # CRITICAL: Block and wait when pool is full instead of creating new connections
            )
            
            # Mount adapter for both HTTP and HTTPS
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            
            # Configure session for better compatibility
            session.verify = True  # Enable SSL verification
            session.timeout = self.timeout
            
            # Set common headers - use Connection: close to prevent connection reuse issues
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'close',  # Changed from keep-alive to prevent connection accumulation
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0',
            })
            
            logger.info(f"Created cloudscraper session with pool_connections={MAX_POOL_CONNECTIONS}, pool_maxsize={MAX_POOL_SIZE}, pool_block=True")
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
        # Acquire semaphore to limit concurrent requests
        acquired = self._request_semaphore.acquire(timeout=60)
        if not acquired:
            raise ScrapingError("Request timeout: too many concurrent requests")
        
        response = None
        try:
            self._wait_for_rate_limit()
            
            session = self.get_session()
            
            # Set timeout if not provided
            if 'timeout' not in kwargs:
                kwargs['timeout'] = self.timeout
            
            logger.debug(f"Making {method.upper()} request to: {url}")
            response = session.request(method, url, **kwargs)
            
            # Periodic connection cleanup
            self._maybe_cleanup_connections()
            
            # Check for Cloudflare challenge
            if 'cf-ray' in response.headers and response.status_code in [403, 503]:
                logger.warning("Cloudflare challenge detected, recreating session")
                # Close the response first
                if response:
                    response.close()
                self.close()  # Properly close old session
                session = self.get_session()
                response = session.request(method, url, **kwargs)
            
            response.raise_for_status()
            logger.debug(f"Request successful: {response.status_code}")
            return response
            
        except Timeout as e:
            logger.error(f"Request timeout: {e}")
            # Cleanup on error
            self._cleanup_on_error()
            raise ScrapingError(f"Request timeout: {e}")
            
        except ConnectionError as e:
            logger.error(f"Connection error: {e}")
            # Cleanup on error - this is critical for "too many open files"
            self._cleanup_on_error()
            raise ServiceUnavailableError(f"Connection error: {e}")
            
        except RequestException as e:
            # Close response if it exists
            if response:
                try:
                    response.close()
                except Exception:
                    pass
            
            if hasattr(e, 'response') and e.response is not None:
                try:
                    e.response.close()
                except Exception:
                    pass
                    
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
        finally:
            # Always release the semaphore
            self._request_semaphore.release()
    
    def _cleanup_on_error(self):
        """Cleanup connections when an error occurs"""
        try:
            self._cleanup_idle_connections()
        except Exception as e:
            logger.warning(f"Error during cleanup on error: {e}")
    
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
                    # First, clear all connection pools in adapters
                    for adapter in self.session.adapters.values():
                        if hasattr(adapter, 'poolmanager') and adapter.poolmanager:
                            try:
                                adapter.poolmanager.clear()
                            except Exception as e:
                                logger.debug(f"Error clearing pool manager: {e}")
                        if hasattr(adapter, 'close'):
                            try:
                                adapter.close()
                            except Exception as e:
                                logger.debug(f"Error closing adapter: {e}")
                    
                    # Then close the session itself
                    try:
                        self.session.close()
                    except Exception as e:
                        logger.debug(f"Error closing session: {e}")
                        
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