from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401  -- registers models on Base.metadata
from app.config import get_settings
from app.database import engine
from app.routers import health

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is owned by Alembic -- run `make migrate`, not create_all().
    yield
    engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}
