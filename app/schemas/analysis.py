"""The structured analysis result.

This model is the single definition of what a valid analysis looks like. It
is used three ways: to validate whatever the LLM returns (anything that does
not fit raises ``ValidationError`` and never reaches an API consumer), as the
JSON schema handed to the LLM provider, and as the shape of the API response.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.failure_classifier import FailureCategory

Severity = Literal["low", "medium", "high", "critical"]

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=2000)]


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["failed", "unstable", "unknown"] = Field(
        description="Outcome of the pipeline run as read from the log."
    )
    category: FailureCategory = Field(
        description="Failure category; must be one of the known values."
    )
    severity: Severity = Field(description="Operational impact of the failure.")
    summary: Annotated[str, Field(min_length=1, max_length=500)] = Field(
        description="One or two sentences: what failed."
    )
    root_cause: Annotated[str, Field(min_length=1, max_length=1000)] = Field(
        description="The most probable underlying cause, stated concretely."
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        description="How sure the analysis is, from 0.0 to 1.0."
    )
    evidence: Annotated[list[NonEmptyStr], Field(max_length=10)] = Field(
        default_factory=list,
        description="Verbatim log fragments that support the root cause.",
    )
    suggested_actions: Annotated[list[NonEmptyStr], Field(min_length=1, max_length=10)] = Field(
        description="Concrete, ordered steps a DevOps engineer should take next."
    )
