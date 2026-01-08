"""
API v1 routes
"""

from fastapi import APIRouter
from .memories import router as memories_router
from .search import router as search_router
from .users import router as users_router
from .agents import router as agents_router
from .system import router as system_router

# Create main v1 router
router = APIRouter(prefix="/api/v1", tags=["v1"])

# Include sub-routers
router.include_router(memories_router)
router.include_router(search_router)
router.include_router(users_router)
router.include_router(agents_router)
router.include_router(system_router)
