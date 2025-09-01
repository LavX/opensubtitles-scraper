
# -*- coding: utf-8 -*-
"""
OpenSubtitles Web Scraper Provider for Bazarr
Drop-in replacement for existing OpenSubtitles providers using web scraping
"""

import logging
import requests
import os
from typing import List, Optional
from babelfish import Language, language_converters
from subliminal import Provider, Episode, Movie
from subliminal.subtitle import Subtitle, fix_line_ending
from subliminal.exceptions import ProviderError, ServiceUnavailable, AuthenticationError, ConfigurationError
from subliminal.utils import sanitize

logger = logging.getLogger(__name__)


class OpenSubtitlesScraperSubtitle(Subtitle):
    """OpenSubtitles Scraper Subtitle for Bazarr compatibility"""
    provider_name = 'opensubtitles_scraper'
    
    def __init__(self, language, hearing_impaired, page_link, subtitle_id, 
                 movie_name, movie_release_name, movie_year, movie_imdb_id,
                 series_season=None, series_episode=None, filename=None, 
                 uploader=None, download_count=0, rating=0.0, forced=False, 
                 fps=None, download_url=None, movie_kind='movie'):
        super(OpenSubtitlesScraperSubtitle, self).__init__(
            language, hearing_impaired=hearing_impaired, page_link=page_link
        )
        self.subtitle_id = subtitle_id
        self.movie_name = movie_name
        self.movie_release_name = movie_release_name
        self.movie_year = movie_year
        self.movie_imdb_id = movie_imdb_id
        self.series_season = series_season
        self.series_episode = series_episode
        self.filename = filename
        self.uploader = uploader
        self.download_count = download_count
        self.rating = rating
        self.forced = forced
        self.fps = fps
        self.download_url = download_url
        self.movie_kind = movie_kind
        self.matched_by = 'query'  # Default matching method
        
    @property
    def id(self):
        return str(self.subtitle_id)
    
    @property
    def release_info(self):
        return self.movie_release_name
    
    def get_matches(self, video):
        """Get matches for scoring - compatible with Bazarr's scoring system"""
        matches = set()
        
        # Episode matching
        if isinstance(video, Episode) and self.movie_kind == 'episode':
            # Series name matching
            if video.series and self.movie_name:
                if sanitize(video.series) == sanitize(self.movie_name):
                    matches.add('series')
            
            # Year matching
            if video.year and self.movie_year == video.year:
                matches.add('year')
                
            # Season matching
            if video.season and self.series_season == video.season:
                matches.add('season')
                
            # Episode matching
            if video.episode and self.series_episode == video.episode:
                matches.add('episode')
                
            # Title matching (episode title)
            if hasattr(video, 'title') and video.title and self.movie_release_name:
                if sanitize(video.title) in sanitize(self.movie_release_name):
                    matches.add('title')
        
        # Movie matching
        elif isinstance(video, Movie) and self.movie_kind == 'movie':
            # Title matching
            if video.title and self.movie_name:
                if sanitize(video.title) == sanitize(self.movie_name):
                    matches.add('title')
            
            # Year matching
            if video.year and self.movie_year == video.year:
                matches.add('year')
        
        # IMDB ID matching (highest priority)
        if video.imdb_id and self.movie_imdb_id == video.imdb_id:
            matches.add('imdb_id')
        
        # Release group matching
