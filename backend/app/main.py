from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, log


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("app_startup", model=settings.llm_model)
    yield
    log.info("app_shutdown")


app = FastAPI(title="ResearcherX", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "researcherx", "docs": "/docs"}
