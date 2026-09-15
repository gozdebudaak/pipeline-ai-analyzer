"""Request and response bodies of ``POST /api/v1/analyze``."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import AnalysisResult
from app.services.failure_classifier import FailureCategory

MAX_LOG_CHARS = 2_000_000  # ~2 MB of text; larger logs must be trimmed by the caller


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log: Annotated[str, Field(min_length=1, max_length=MAX_LOG_CHARS)] = Field(
        description="Raw CI/CD log text. Secrets are redacted server-side before analysis."
    )
    source: Annotated[str, Field(max_length=100)] | None = Field(
        default=None, description='Origin of the log, e.g. "jenkins", "github-actions".'
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description='Free-form labels echoed back, e.g. {"job": "api", "build": "142"}.',
    )


class RuleBasedOpinion(BaseModel):
    category: FailureCategory
    matched_rules: list[str]
    agrees_with_model: bool | None = Field(
        description="True/False when the rules had an opinion; null when they matched nothing."
    )


class LogStatsOut(BaseModel):
    total_lines: int
    normalized_lines: int
    excerpt_lines: int
    truncated: bool
    secrets_redacted: int


class LLMInfo(BaseModel):
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    prompt_version: str


class AnalyzeResponse(BaseModel):
    request_id: str
    analysis: AnalysisResult
    rule_based: RuleBasedOpinion
    log_stats: LogStatsOut
    llm: LLMInfo
    source: str | None = None
    metadata: dict[str, str] | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None


class ErrorResponse(BaseModel):
    error: ErrorDetail
