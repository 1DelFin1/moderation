import uvicorn
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import main_router
from app.core.config import settings

_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    502: "BAD_GATEWAY",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.scheduler.jobs import configure_scheduler
    scheduler = configure_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="moderation", lifespan=lifespan)
app.include_router(main_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("code") or _STATUS_CODES.get(exc.status_code, "ERROR")
        message = detail.get("message") or str(detail)
        content: dict = {"code": code, "message": message}
        if "details" in detail:
            content["details"] = detail["details"]
    else:
        code = _STATUS_CODES.get(exc.status_code, "ERROR")
        message = str(detail) if detail is not None else "An error occurred"
        content = {"code": code, "message": message}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    parts = [
        f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
        for e in errors[:3]
    ]
    return JSONResponse(
        status_code=422,
        content={"code": "VALIDATION_ERROR", "message": "; ".join(parts)},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=settings.CORS_METHODS,
    allow_headers=settings.CORS_HEADERS,
)


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8011, reload=True)
