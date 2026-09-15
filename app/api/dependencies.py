"""FastAPI dependencies: how routes obtain application-scoped objects."""

from fastapi import HTTPException, Request, status

from app.services.analysis_service import AnalysisService


def get_analysis_service(request: Request) -> AnalysisService:
    """The service built once at startup; 503 if no LLM provider is configured."""
    service: AnalysisService | None = getattr(request.app.state, "analysis_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "llm_not_configured", "message": "no LLM provider is configured"},
        )
    return service
