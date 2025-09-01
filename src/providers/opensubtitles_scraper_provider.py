"""OpenSubtitles scraper provider compatible with Bazarr"""

import logging
from typing import List, Set, Optional, Dict, Any

from .base_provider import BaseProvider, Language, Video, Episode, Movie, Subtitle
from ..core.scraper import OpenSubtitlesScraper
from ..parsers.search_parser import SearchResult
from ..parsers.subtitle_parser import SubtitleInfo
from ..utils.exceptions import SearchError, ScrapingError, DownloadError
from ..utils.helpers import normalize_title

logger = logging.getLogger(__name__)


class OpenSubtitlesScraperSubtitle(Subtitle):
    """Subtitle class for OpenSubtitles scraper provider"""
    
    def __init__(self, language: Language, hearing_impaired: bool, page_link: str,
                 subtitle_info: SubtitleInfo, search_result: SearchResult):
        super().__init__(language, hearing_impaired, page_link)
        
        self.subtitle_id = subtitle_info.subtitle_id
        self.release_info = subtitle_info.release_name
        self.uploader = subtitle_info.uploader
        self.download_count = subtitle_info.download_count
        self.rating = subtitle_info.rating
        self.fps = subtitle_info.fps
        self.forced = subtitle_info.forced
        
        # Store original objects for download
        self._subtitle_info = subtitle_info
        self._search_result = search_result
    
    def get_matches(self, video: Video) -> set:
        """Get matches between subtitle and video"""
        matches = set()
        
        # Basic matches
        if isinstance(video, Episode):
            # Series name match
            if hasattr(video, 'series') and self._search_result.title:
                if normalize_title(video.series) == normalize_title(self._search_result.title):
                    matches.add('series')
            
            # Season/episode matches would need more detailed parsing
            # For now, we'll rely on the search accuracy
            
        elif isinstance(video, Movie):
            # Title match
            if hasattr(video, 'title') and self._search_result.title:
                if normalize_title(video.title) == normalize_title(self._search_result.title):
                    matches.add('title')
        
        # Year match
        if hasattr(video, 'year') and video.year and self._search_result.year:
            if video.year == self._search_result.year:
                matches.add('year')
        
        # IMDB ID match
        if hasattr(video, 'imdb_id') and video.imdb_id and self._search_result.imdb_id:
            if video.imdb_id == self._search_result.imdb_id:
                matches.add('imdb_id')
        
        # Release info match (basic)
        if hasattr(video, 'name') and self.release_info:
            video_name_normalized = normalize_title(video.name)
            release_normalized = normalize_title(self.release_info)
            
            # Check for common words between video name and release info
            video_words = set(video_name_normalized.split())
            release_words = set(release_normalized.split())
            common_words = video_words.intersection(release_words)
            
            if len(common_words) >= 2:  # At least 2 common words
                matches.add('release_group')
        
        # Hash match (if available)
        if hasattr(video, 'hashes') and video.hashes:
            # OpenSubtitles uses specific hash, but we don't have it from scraping
            # This would need to be implemented if hash matching is required
            pass
        
        return matches


class OpenSubtitlesScraperProvider(BaseProvider):
    """OpenSubtitles scraper provider for Bazarr compatibility"""
    
    # Define supported languages (comprehensive list)
    languages = {
        Language('en', 'English'),
        Language('es', 'Spanish'),
        Language('fr', 'French'),
        Language('de', 'German'),
        Language('it', 'Italian'),
        Language('pt', 'Portuguese'),
        Language('ru', 'Russian'),
        Language('zh', 'Chinese'),
        Language('ja', 'Japanese'),
        Language('ko', 'Korean'),
        Language('ar', 'Arabic'),
        Language('nl', 'Dutch'),
        Language('pl', 'Polish'),
        Language('sv', 'Swedish'),
        Language('no', 'Norwegian'),
        Language('da', 'Danish'),
        Language('fi', 'Finnish'),
        Language('el', 'Greek'),
        Language('he', 'Hebrew'),
        Language('tr', 'Turkish'),
        Language('cs', 'Czech'),
        Language('hu', 'Hungarian'),
        Language('ro', 'Romanian'),
        Language('bg', 'Bulgarian'),
        Language('hr', 'Croatian'),
        Language('sr', 'Serbian'),
        Language('sk', 'Slovak'),
        Language('sl', 'Slovenian'),
        Language('uk', 'Ukrainian'),
        Language('lt', 'Lithuanian'),
        Language('lv', 'Latvian'),
        Language('et', 'Estonian'),
        Language('th', 'Thai'),
        Language('vi', 'Vietnamese'),
        Language('hi', 'Hindi'),
        Language('bn', 'Bengali'),
        Language('ta', 'Tamil'),
        Language('te', 'Telugu'),
        Language('mr', 'Marathi'),
        Language('gu', 'Gujarati'),
        Language('pa', 'Punjabi'),
        Language('ur', 'Urdu'),
        Language('fa', 'Persian'),
        Language('ms', 'Malay'),
        Language('id', 'Indonesian'),
        Language('tl', 'Tagalog'),
        Language('sw', 'Swahili'),
        Language('af', 'Afrikaans'),
        Language('is', 'Icelandic'),
        Language('cy', 'Welsh'),
        Language('ga', 'Irish'),
        Language('eu', 'Basque'),
        Language('ca', 'Catalan'),
        Language('gl', 'Galician'),
        Language('mt', 'Maltese'),
        Language('sq', 'Albanian'),
        Language('mk', 'Macedonian'),
        Language('bs', 'Bosnian'),
        Language('me', 'Montenegrin'),
    }
    
    video_types = (Episode, Movie)
    subtitle_class = OpenSubtitlesScraperSubtitle
    
    def __init__(self, timeout: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.timeout = timeout
        self.scraper: Optional[OpenSubtitlesScraper] = None
    
    def initialize(self):
        """Initialize the scraper"""
        try:
            self.scraper = OpenSubtitlesScraper(timeout=self.timeout)
            self.initialized = True
            logger.info("OpenSubtitles scraper provider initialized")
        except Exception as e:
            logger.error(f"Failed to initialize OpenSubtitles scraper provider: {e}")
            raise
    
    def terminate(self):
        """Terminate the scraper and cleanup resources"""
        if self.scraper:
            self.scraper.close()
            self.scraper = None
        self.initialized = False
        logger.info("OpenSubtitles scraper provider terminated")
    
    def list_subtitles(self, video: Video, languages: Set[Language]) -> List[OpenSubtitlesScraperSubtitle]:
        """List available subtitles for a video"""
        if not self.initialized or not self.scraper:
            raise ScrapingError("Provider not initialized")
        
        try:
            logger.info(f"Searching subtitles for: {video.name}")
            
            # Search for the movie/show
            search_results = []
            
            if isinstance(video, Episode):
                # Search for TV show
                search_results = self.scraper.search_tv_shows(
                    query=video.series,
                    year=getattr(video, 'year', None),
                    imdb_id=getattr(video, 'series_imdb_id', None)
                )
            elif isinstance(video, Movie):
                # Search for movie
                search_results = self.scraper.search_movies(
                    query=video.title,
                    year=getattr(video, 'year', None),
                    imdb_id=getattr(video, 'imdb_id', None)
                )
            
            if not search_results:
                logger.info("No search results found")
                return []
            
            # Use the best search result
            best_result = search_results[0]
            movie_url = self.scraper.get_movie_url(best_result)
            
            # Get subtitle listings
            language_codes = [lang.code for lang in languages]
            subtitle_infos = self.scraper.get_subtitles(movie_url, language_codes)
            
            # Convert to provider subtitles
            subtitles = []
            for subtitle_info in subtitle_infos:
                try:
                    # Map language code
                    lang_code = subtitle_info.language.lower()
                    language = self._get_language_by_code(lang_code)
                    if not language:
                        logger.warning(f"Unsupported language: {lang_code}")
                        continue
                    
                    # Check if language is requested
                    if language not in languages:
                        continue
                    
                    subtitle = OpenSubtitlesScraperSubtitle(
                        language=language,
                        hearing_impaired=subtitle_info.hearing_impaired,
                        page_link=subtitle_info.download_url or movie_url,
                        subtitle_info=subtitle_info,
                        search_result=best_result
                    )
                    
                    subtitles.append(subtitle)
                    
                except Exception as e:
                    logger.warning(f"Failed to process subtitle info: {e}")
                    continue
            
            logger.info(f"Found {len(subtitles)} subtitles")
            return subtitles
            
        except Exception as e:
            logger.error(f"Failed to list subtitles: {e}")
            raise ScrapingError(f"Subtitle listing failed: {e}")
    
    def download_subtitle(self, subtitle: OpenSubtitlesScraperSubtitle):
        """Download subtitle content"""
        if not self.initialized or not self.scraper:
            raise DownloadError("Provider not initialized")
        
        try:
            logger.info(f"Downloading subtitle: {subtitle.subtitle_id}")
            
            # Download using the scraper
            subtitle_data = self.scraper.download_subtitle(subtitle._subtitle_info)
            
            # Set content and encoding
            subtitle.content = subtitle_data['content'].encode(subtitle_data.get('encoding', 'utf-8'))
            subtitle.encoding = subtitle_data.get('encoding', 'utf-8')
            
            logger.info(f"Successfully downloaded subtitle: {subtitle_data['filename']}")
            
        except Exception as e:
            logger.error(f"Failed to download subtitle: {e}")
            raise DownloadError(f"Subtitle download failed: {e}")
    
    def _get_language_by_code(self, code: str) -> Optional[Language]:
        """Get Language object by code"""
        code = code.lower()
        for language in self.languages:
            if language.code == code:
                return language
        return None