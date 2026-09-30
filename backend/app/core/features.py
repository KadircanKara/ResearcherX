"""Feature switches: a switched-off feature's routes answer 404.

404 with FastAPI's own body, so a switched-off route is indistinguishable
from one that was never mounted. Used through FastAPI dependencies, so the
gated modules themselves are untouched.
"""

from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from app.core.config import settings


def feature_enabled(name: str) -> bool:
    return bool(getattr(settings, f"feature_{name}"))


def require_feature(name: str) -> Callable[[], Awaitable[None]]:
    getattr(settings, f"feature_{name}")  # typo guard: fail at import, not per request

    async def _gate() -> None:
        if not feature_enabled(name):
            raise HTTPException(status_code=404, detail="Not Found")

    return _gate
