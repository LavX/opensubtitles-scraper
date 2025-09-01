"""Base provider interface compatible with Bazarr"""

import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class Language:
    """Language representation compatible with Bazarr"""
    
    def __init__(self, code: str, name: Optional[str] = None, forced: bool = False, hi: bool = False):
        self.code = code.lower()
        self.name = name or code
        self.forced = forced
        self.hi = hi  # hearing impaired
    
    @property
    def opensubtitles(self) -> str:
        """Get OpenSubtitles language code"""
        return self.code
    
    def __str__(self):
        return self.code
    
    def __repr__(self):
        return f"Language('{self.code}')"
    
    def __eq__(self, other):
        if isinstance(other, Language):
            return self.code == other.code
        return self.code == str(other).lower()
    
    def __hash__(self):
        return hash(self.code)


class Video:
    """Video representation compatible with Bazarr"""
    
    def __init__(self, name: str, size: Optional[int] = None, hashes: Optional[Dict[str, str]] = None):
        self.name = name
        self.size = size or 0
        self.hashes = hashes or {}
        self.imdb_id = None
        self.year = None
        self.title = None
        self.alternative_titles = []
    
    def __str__(self):
        return self.name


class Episode(Video):
    """Episode representation compatible with Bazarr"""
    
    def __init__(self, name: str, series: str, season: int, episode: int, **kwargs):
        super().__init__(name, **kwargs)
        self.series = series
        self.season = season
        self.episode = episode
        self.series_imdb_id = None
        self.alternative_series = []
        self.is_special = False
    
    @property
    def title(self):
        return self.series


class Movie(Video):
    """Movie representation compatible with Bazarr"""
    
    def __init__(self, name: str, title: str, year: Optional[int] = None, **kwargs):
        super().__init__(name, **kwargs)
        self.title = title
        self.year = year
        self.alternative_titles = []


class Subtitle:
    """Subtitle representation compatible with Bazarr"""
    
    def __init__(self, language: Language, hearing_impaired: bool = False, page_link: Optional[str] = None):
        self.language = language
        self.hearing_impaired = hearing_impaired
        self.page_link = page_link
        self.content = None
        self.encoding = 'utf-8'
        self.provider_name = 'opensubtitles_scraper'
        
        # Additional metadata
        self.subtitle_id = None
        self.release_info = None
        self.uploader = None
        self.download_count = 0
        self.rating = 0.0
        self.fps = None
    
    def get_matches(self, video: Video) -> set:
        """Get matches between subtitle and video (to be implemented by subclasses)"""
        return set()
    
    def __str__(self):
        return f"<{self.__class__.__name__} [{self.language}]>"
    
    def __repr__(self):
        return f"{self.__class__.__name__}(language={self.language!r}, hearing_impaired={self.hearing_impaired})"


class BaseProvider(ABC):
    """Base provider interface compatible with Bazarr"""
    
    languages = set()
    video_types = (Episode, Movie)
    subtitle_class = Subtitle
    
    def __init__(self, **kwargs):
        self.initialized = False
    
    @abstractmethod
    def initialize(self):
        """Initialize the provider"""
        pass
    
    @abstractmethod
    def terminate(self):
        """Terminate the provider and cleanup resources"""
        pass
    
    @abstractmethod
    def list_subtitles(self, video: Video, languages: set) -> List[Subtitle]:
        """List available subtitles for a video"""
        pass
    
    @abstractmethod
    def download_subtitle(self, subtitle: Subtitle):
        """Download subtitle content"""
        pass
    
    def __enter__(self):
        self.initialize()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()