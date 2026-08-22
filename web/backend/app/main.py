import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import Settings, settings


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


def create_app(app_settings: Settings) -> FastAPI:
    """Construct the product application from explicit settings."""
    if app_settings.SENTRY_DSN and app_settings.ENVIRONMENT != "local":
        sentry_sdk.init(dsn=str(app_settings.SENTRY_DSN), enable_tracing=True)

    application = FastAPI(
        title=app_settings.PROJECT_NAME,
        openapi_url=f"{app_settings.API_V1_STR}/openapi.json",
        generate_unique_id_function=custom_generate_unique_id,
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
