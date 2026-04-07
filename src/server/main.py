"""
Main FastAPI application for PowerMem API Server
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, RedirectResponse
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from .config import config
from .api.v1 import router as v1_router
from .middleware.logging import setup_logging, LoggingMiddleware
from .middleware.rate_limit import rate_limit_middleware
from .middleware.error_handler import error_handler
from .middleware.auth import verify_api_key

import os
import logging

# Setup logging
setup_logging()

logger = logging.getLogger("server")

# Setup templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared service singletons at startup and clean up on shutdown."""
    from .services.memory_service import MemoryService
    from .services.search_service import SearchService
    from .services.user_service import UserService
    from .services.agent_service import AgentService

    logger.info("Initializing service singletons...")
    try:
        app.state.memory_service = MemoryService()
        app.state.search_service = SearchService()
        app.state.user_service = UserService()
        app.state.agent_service = AgentService()
        logger.info("Service singletons initialized")
    except Exception as e:
        logger.error(f"Failed to initialize service singletons: {e}", exc_info=True)
        app.state.memory_service = None
        app.state.search_service = None
        app.state.user_service = None
        app.state.agent_service = None

    yield

    logger.info("Shutting down services...")


# Create FastAPI app
app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    description=config.api_description,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Setup CORS
if config.cors_enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.get_cors_origins_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Setup logging middleware
app.add_middleware(LoggingMiddleware)

# Setup rate limiting
rate_limit_middleware(app)


# Mount Dashboard (redirect /dashboard -> /dashboard/ so index.html is served)
dashboard_dist = os.path.abspath(os.path.join(BASE_DIR, "dashboard"))
if os.path.exists(dashboard_dist):
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_redirect():
        return RedirectResponse(url="/dashboard/", status_code=302)

    app.mount(
        "/dashboard", StaticFiles(directory=dashboard_dist, html=True), name="dashboard"
    )

# Include API routers
app.include_router(v1_router)

# Add exception handlers
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(StarletteHTTPException, error_handler)
app.add_exception_handler(Exception, error_handler)


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Disallow all crawlers"""
    return Response(content="User-agent: *\nDisallow: /", media_type="text/plain")


@app.get("/", tags=["root"])
async def root():
    """Root endpoint"""
    return {
        "name": "PowerMem API Server",
        "version": config.api_version,
        "docs": "/docs",
        "dashboard": "/dashboard/",
        "health": "/api/v1/system/health",
    }


@app.get("/api", tags=["root"])
async def api_root():
    """API root endpoint"""
    return {
        "version": config.api_version,
        "endpoints": {
            "v1": "/api/v1",
            "docs": "/docs",
            "health": "/api/v1/health",
            "status": "/api/v1/status",
        },
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "server.main:app",
        host=config.host,
        port=config.port,
        reload=config.reload,
        workers=config.workers if not config.reload else 1,
    )
