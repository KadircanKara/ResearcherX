from fastapi import APIRouter

from app.api.v1 import chat, health, latex, projects, research, users

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(research.router)
api_router.include_router(users.router)  # -> /v1/me, /v1/users
api_router.include_router(projects.router)  # -> /v1/projects...
api_router.include_router(chat.router)  # -> /v1/projects/.../conversations...
api_router.include_router(latex.router)  # -> /v1/projects/.../latex...
