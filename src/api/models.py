"""Pydantic models for API requests and responses"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class SearchRequest(BaseModel):
    """Request model for search endpoints"""
    query: str = Field(..., description="Search query (movie/show title)")
    year: Optional[int] = Field(None, description="Release year")
    imdb_id: Optional[str] = Field(None, description="IMDB ID")
    kind: Optional[str] = Field("movie", description="Content type: 'movie' or 'episode'")


class SearchResult(BaseModel):
    """Response model for search results"""
    title: str
    year: Optional[int] = None
    imdb_id: Optional[str] = None
    url: Optional[str] = None
    subtitle_count: int = 0
    kind: str = "movie"


class SearchResponse(BaseModel):
    """Response model for search endpoints"""
    results: List[SearchResult]
    total: int
    query: str


class SubtitleRequest(BaseModel):
    """Request model for subtitle listings"""
    movie_url: str = Field(..., description="Movie/show URL from search results")
    languages: Optional[List[str]] = Field(None, description="Language codes to filter")


class SubtitleInfo(BaseModel):
    """Response model for subtitle information"""
    subtitle_id: str
    language: str
    filename: str
    release_name: str
    uploader: str
    download_count: int = 0
    rating: float = 0.0
    hearing_impaired: bool = False
    forced: bool = False
    fps: Optional[float] = None
    download_url: Optional[str] = None
    upload_date: Optional[datetime] = None


class SubtitleResponse(BaseModel):
    """Response model for subtitle listings"""
    subtitles: List[SubtitleInfo]
    total: int
    movie_url: str


class DownloadRequest(BaseModel):
    """Request model for subtitle download"""
    subtitle_id: str = Field(..., description="Subtitle ID to download")
    download_url: str = Field(..., description="Download URL")


class DownloadResponse(BaseModel):
    """Response model for subtitle download"""
    filename: str
    content: str
    size: int
    encoding: str = "utf-8"


class HealthResponse(BaseModel):
    """Response model for health check"""
    status: str = "healthy"
    version: str
    uptime: float
    scraper_status: str


class ErrorResponse(BaseModel):
    """Response model for errors"""
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None