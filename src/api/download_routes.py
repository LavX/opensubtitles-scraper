"""
Download API routes for OpenSubtitles scraper service
"""

import logging
import base64
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..core.scraper import OpenSubtitlesScraper

logger = logging.getLogger(__name__)

router = APIRouter()


class DownloadRequest(BaseModel):
    download_url: str


class DownloadResponse(BaseModel):
    content: str  # Base64-encoded subtitle content
    filename: str
    encoding: str = "utf-8"


@router.post("/download", response_model=DownloadResponse)
async def download_subtitle(request: DownloadRequest):
    """
    Download subtitle content from OpenSubtitles.org
    
    Args:
        request: Download request containing the subtitle download URL
        
    Returns:
        DownloadResponse: Base64-encoded subtitle content with metadata
    """
    try:
        logger.info(f"Downloading subtitle from: {request.download_url}")
        
        scraper = OpenSubtitlesScraper()
        
        # Download the subtitle content
        content, filename = scraper.download_subtitle(request.download_url)
        
        if not content:
            raise HTTPException(status_code=404, detail="Subtitle content not found")
        
        # Encode content as base64 for JSON transport
        encoded_content = base64.b64encode(content.encode('utf-8')).decode('ascii')
        
        logger.info(f"Successfully downloaded subtitle: {filename}")
        
        return DownloadResponse(
            content=encoded_content,
            filename=filename,
            encoding="utf-8"
        )
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")