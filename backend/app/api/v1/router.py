from fastapi import APIRouter

from app.api.v1 import health, research

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(research.router)
