"""Worker-submitted milestone findings, independent of delivery records."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class CriterionFinding(VerificationInput):
    check: int = Field(ge=1)
    verdict: Literal["passed", "failed", "unverified"]
    evidence: str = Field(min_length=1, max_length=8000)


class VerificationReport(VerificationInput):
    verification_id: str
    findings: list[CriterionFinding] = Field(min_length=1, max_length=50)
    coverage: Literal["complete", "incomplete"]
    coverage_notes: str = Field(min_length=1, max_length=8000)
