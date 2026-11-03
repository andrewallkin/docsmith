import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, google, health, keys, tools
from app.config import get_settings
from app.core.tool_errors import ToolApiError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(ToolApiError)
    async def tool_error_handler(_: Request, exc: ToolApiError):
        payload = {"error": exc.code, "detail": exc.message, **exc.extra}
        return JSONResponse(status_code=exc.status_code, content=payload)

    api_prefix = "/api"
    app.include_router(health.router, prefix=api_prefix)
    app.include_router(auth.router, prefix=api_prefix)
    app.include_router(keys.router, prefix=api_prefix)
    app.include_router(google.router, prefix=api_prefix)
    app.include_router(tools.router, prefix=api_prefix)
    return app


app = create_app()
