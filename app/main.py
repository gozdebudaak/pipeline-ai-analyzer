from fastapi import FastAPI

from app.api.middleware import correlation_id_middleware
from app.api.routes import health
from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Wrapping construction in a function (the "application factory" pattern)
    lets tests build a fresh app with different settings instead of sharing
    one global instance.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.middleware("http")(correlation_id_middleware)
    app.include_router(health.router)
    return app


app = create_app()
