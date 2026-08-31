import logging
import traceback

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.lifespan import lifespan
from app.routers.chat import router as chat_router
from app.routers.debug import router as debug_router
from app.routers.home import router as home_router
from app.routers.observability import router as observability_router
from app.routers.profile_events import router as profile_events_router

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="AI Hub v2 (LangGraph)", lifespan=lifespan)

app.include_router(chat_router)
app.include_router(home_router)
app.include_router(profile_events_router)

if settings.APP_ENV.strip().casefold() in {"development", "local"} and settings.ENABLE_DEBUG_ROUTES:
    app.include_router(debug_router)
    app.include_router(observability_router)


@app.exception_handler(AppError)
async def app_error_handler(request: FastAPIRequest, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "error": {"code": exc.error_code, "message": exc.message}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: FastAPIRequest, exc: HTTPException) -> JSONResponse:
    status_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        422: "VALIDATION_ERROR",
        500: "INTERNAL_ERROR",
    }
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error": {
                "code": status_map.get(exc.status_code, "HTTP_ERROR"),
                "message": str(exc.detail),
            },
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: FastAPIRequest, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s\n%s", str(exc), traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "내부 서버 오류가 발생했습니다.",
            },
        },
    )


@app.get("/health")
async def health_check():
    settings = get_settings()
    return {"status": "ok", "env": settings.APP_ENV, "version": "v2"}
