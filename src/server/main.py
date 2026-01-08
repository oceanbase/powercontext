"""
Main FastAPI application for PowerMem API Server
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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

# Setup logging
setup_logging()

# Create FastAPI app
app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    description=config.api_description,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
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

# Include API routers
app.include_router(v1_router)

# Add exception handlers
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(StarletteHTTPException, error_handler)
app.add_exception_handler(Exception, error_handler)


@app.get("/", tags=["root"])
async def root():
    """Root endpoint"""
    return {
        "name": "PowerMem API Server",
        "version": config.api_version,
        "docs": "/docs",
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
        }
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
