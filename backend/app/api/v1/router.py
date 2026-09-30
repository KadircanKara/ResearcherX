from fastapi import APIRouter, Depends

from app.api.v1 import chat, health, latex, latex_files, latex_members, projects, research, users
from app.core.features import require_feature

_latex = [Depends(require_feature("latex"))]

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(research.router, dependencies=[Depends(require_feature("research"))])
api_router.include_router(users.router)  # -> /v1/me, /v1/users
api_router.include_router(projects.router)  # -> /v1/projects...
api_router.include_router(chat.router)  # -> /v1/projects/.../conversations...
api_router.include_router(latex.router, dependencies=_latex)  # -> /v1/projects/.../latex...
api_router.include_router(latex_files.router, dependencies=_latex)  # .../latex/.../files
api_router.include_router(latex_members.router, dependencies=_latex)  # .../latex/.../members
