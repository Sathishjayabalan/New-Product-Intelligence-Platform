from datetime import datetime

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    name: str
    industry_context: str = "insurance"
    plan_tier: str = "starter"


class ContextSwitch(BaseModel):
    industry_context: str


class SignalIn(BaseModel):
    content: str = Field(min_length=3)
    source_type: str
    source_name: str = ""
    tags: list[str] = []
    source_timestamp: datetime | None = None


class SignalBatchIn(BaseModel):
    signals: list[SignalIn]


class HypothesisDecision(BaseModel):
    accepted: bool


class BriefEdit(BaseModel):
    fields: dict
    fields_edited: int = Field(ge=0, le=10)


class SurveyResponses(BaseModel):
    count: int = Field(gt=0)


class ExpertRating(BaseModel):
    """Human expert panel input (E-05 / E-12)."""

    eval_id: str = Field(pattern=r"^E-(05|12)$")
    score: float = Field(ge=0, le=5)
    rater: str = "expert_panel"
    notes: str = ""


class UJMReview(BaseModel):
    accepted: bool


class UserCreate(BaseModel):
    email: str
    name: str
    role: str = "coe_lead"


class PipelineRunRequest(BaseModel):
    auto_validate: bool = True
    enforce_gates: bool = True
