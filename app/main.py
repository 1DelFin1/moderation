import uvicorn
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import main_router
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.scheduler.jobs import configure_scheduler
    scheduler = configure_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="moderation", lifespan=lifespan)
app.include_router(main_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=settings.CORS_METHODS,
    allow_headers=settings.CORS_HEADERS,
)


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8011, reload=True)
