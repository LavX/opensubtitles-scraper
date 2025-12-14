"""Main FastAPI application for OpenSubtitles scraper service"""

# Add vendor directory to Python path for cloudscraper
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor'))

import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router, cleanup_scraper
from src.api.download_routes import router as download_router
from src import __version__, __description__

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("Starting OpenSubtitles scraper service")
    yield
    # Shutdown
    logger.info("Shutting down OpenSubtitles scraper service")
    cleanup_scraper()


# Create FastAPI application
app = FastAPI(
    title="OpenSubtitles Scraper Service",
    description=__description__,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router)

# Add Bazarr-compatible endpoints directly to the main app (without /api/v1 prefix)
from src.api.routes import bazarr_search, bazarr_download, get_scraper

@app.post("/search")
async def search_endpoint(request: dict):
    """Bazarr-compatible search endpoint (direct access)"""
    scraper = get_scraper()
    return await bazarr_search(request, scraper)

@app.post("/download")
async def download_endpoint(request: dict):
    """Bazarr-compatible download endpoint (direct access)"""
    scraper = get_scraper()
    return await bazarr_download(request, scraper)

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "OpenSubtitles Scraper",
        "version": __version__,
        "description": __description__,
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health")
async def health():
    """Simple health check endpoint (root level for easy access)"""
    scraper = get_scraper()
    return {
        "status": "healthy",
        "version": __version__,
        "scraper_status": "healthy" if scraper else "unavailable"
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",  # Listen on all interfaces so other machines can connect
        port=8000,
        reload=True,
        log_level="info"
    )