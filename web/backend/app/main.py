from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import Settings, settings
from app.services import nice_import_scheduler


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    nice_import_scheduler.startup_recover()
    try:
        yield
    finally:
        nice_import_scheduler.shutdown()


def create_app(app_settings: Settings) -> FastAPI:
    """Construct the product application from explicit settings."""
    if app_settings.SENTRY_DSN and app_settings.ENVIRONMENT != "local":
        sentry_sdk.init(dsn=str(app_settings.SENTRY_DSN), enable_tracing=True)

    application = FastAPI(
        title=app_settings.PROJECT_NAME,
        openapi_url=f"{app_settings.API_V1_STR}/openapi.json",
        generate_unique_id_function=custom_generate_unique_id,
        lifespan=lifespan,
    )

    if app_settings.all_cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.all_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(api_router, prefix=app_settings.API_V1_STR)
    return application


app = create_app(settings)
