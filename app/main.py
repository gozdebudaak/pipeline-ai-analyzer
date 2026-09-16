import logging

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.middleware import correlation_id_middleware, metrics_middleware
from app.api.routes import analyze, health, metrics
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.llm.factory import LLMConfigurationError, build_llm_provider
from app.services.analysis_service import AnalysisService
from app.services.failure_classifier import FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.prompt_builder import PromptBuilder
from app.services.secret_redactor import SecretRedactor

logger = logging.getLogger(__name__)


def build_analysis_service(settings: Settings) -> AnalysisService | None:
    """Wire the analysis pipeline once at startup; None when no LLM provider is configured."""
    try:
        provider = build_llm_provider(settings)
    except LLMConfigurationError as exc:
        # The process stays alive (health works) but is not ready to analyse.
        logger.error("analysis service disabled", extra={"reason": str(exc)})
        return None
    return AnalysisService(
        redactor=SecretRedactor(),
        processor=LogProcessor(),
        classifier=FailureClassifier(),
        prompt_builder=PromptBuilder(),
        provider=provider,
    )


def create_app() -> FastAPI:
    """Build and return the FastAPI application.

    Wrapping construction in a function (the "application factory" pattern)
    lets tests build a fresh app with different settings instead of sharing
    one global instance.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.state.analysis_service = build_analysis_service(settings)
    app.middleware("http")(correlation_id_middleware)
    app.middleware("http")(metrics_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(analyze.router)
    app.include_router(metrics.router)
    return app


app = create_app()
