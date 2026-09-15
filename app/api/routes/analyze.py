from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_analysis_service
from app.core.logging import correlation_id_var
from app.schemas.analyze import (
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    LLMInfo,
    LogStatsOut,
    RuleBasedOpinion,
)
from app.services.analysis_service import AnalysisService

router = APIRouter(prefix="/api/v1", tags=["analysis"])


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
    responses={
        502: {"model": ErrorResponse, "description": "The LLM returned an unusable answer"},
        503: {"model": ErrorResponse, "description": "The LLM provider is unavailable"},
    },
    summary="Analyse a failed CI/CD log",
)
async def analyze(
    body: AnalyzeRequest,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> AnalyzeResponse:
    outcome = await service.analyze(body.log)
    stats = outcome.log_stats
    return AnalyzeResponse(
        request_id=correlation_id_var.get() or "",
        analysis=outcome.result,
        rule_based=RuleBasedOpinion(
            category=outcome.rule_based_category,
            matched_rules=outcome.matched_rules,
            agrees_with_model=outcome.category_agreement,
        ),
        log_stats=LogStatsOut(
            total_lines=stats.total_lines,
            normalized_lines=stats.normalized_lines,
            excerpt_lines=stats.excerpt_lines,
            truncated=stats.truncated,
            secrets_redacted=stats.secrets_redacted,
        ),
        llm=LLMInfo(
            provider=outcome.llm_provider,
            model=outcome.llm_model,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            prompt_version=outcome.prompt_version,
        ),
        source=body.source,
        metadata=body.metadata,
    )
