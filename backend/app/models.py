from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    industry_context: Mapped[str] = mapped_column(String(32), default="insurance")
    plan_tier: Mapped[str] = mapped_column(String(32), default="starter")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    signals = relationship("Signal", back_populates="tenant")


class User(Base):
    """CoE role-based access (F-11). Roles map to NS19 engine roles."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(48), default="coe_lead")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Signal(Base):
    """Raw ingested signal (F-01 Signal Ingestion Hub)."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(48))  # api|file|crm|stream|voc|conference
    source_name: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    dedup_hash: Mapped[str] = mapped_column(String(64), index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="ingested")
    source_timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tenant = relationship("Tenant", back_populates="signals")
    insight_card = relationship("InsightCard", back_populates="signal", uselist=False)


class InsightCard(Base):
    """AI-generated structured insight card per signal (F-02)."""

    __tablename__ = "insight_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64))
    strength_score: Mapped[float] = mapped_column(Float, default=0.0)
    trend_vector: Mapped[str] = mapped_column(String(16), default="flat")  # rising|flat|falling
    engine_route: Mapped[str] = mapped_column(String(24), default="behavioral")
    summary: Mapped[str] = mapped_column(Text, default="")
    grounded_quote: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    signal = relationship("Signal", back_populates="insight_card")


class BehavioralCluster(Base):
    """ML-generated behavioral cluster with need states and personas (F-03)."""

    __tablename__ = "behavioral_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128))
    need_states: Mapped[list] = mapped_column(JSON, default=list)
    attributes: Mapped[list] = mapped_column(JSON, default=list)
    personas: Mapped[list] = mapped_column(JSON, default=list)
    signal_ids: Mapped[list] = mapped_column(JSON, default=list)
    size: Mapped[int] = mapped_column(Integer, default=0)
    stability_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    hypotheses = relationship("FeatureHypothesis", back_populates="cluster")


class FeatureHypothesis(Base):
    """Ranked feature hypothesis with confidence + risk flags (F-04)."""

    __tablename__ = "feature_hypotheses"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("behavioral_clusters.id"))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cluster = relationship("BehavioralCluster", back_populates="hypotheses")


class PrototypeBrief(Base):
    """Structured prototype brief (F-05) — 10-field template."""

    __tablename__ = "prototype_briefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("feature_hypotheses.id"))
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0)
    fields_edited: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Survey(Base):
    """Cohort + auto-generated survey (F-06)."""

    __tablename__ = "surveys"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("prototype_briefs.id"))
    cohort: Mapped[dict] = mapped_column(JSON, default=dict)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    min_sample_size: Mapped[int] = mapped_column(Integer, default=0)
    responses_collected: Mapped[int] = mapped_column(Integer, default=0)
    statistically_significant: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserJourneyMap(Base):
    """Auto-generated UJM (F-07) — lanes / touchpoints / pain points."""

    __tablename__ = "ujms"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("feature_hypotheses.id"))
    persona: Mapped[str] = mapped_column(String(128))
    lanes: Mapped[list] = mapped_column(JSON, default=list)
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="generated")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FeatureCard(Base):
    """Agile feature card derived from UJM lanes (F-08)."""

    __tablename__ = "feature_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    ujm_id: Mapped[int] = mapped_column(ForeignKey("ujms.id"))
    title: Mapped[str] = mapped_column(String(256))
    user_story: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    story_points: Mapped[int] = mapped_column(Integer, default=3)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="backlog")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PipelineRun(Base):
    """End-to-end orchestrated pipeline run (F-10)."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    current_stage: Mapped[str] = mapped_column(String(24), default="listening")
    stage_log: Mapped[list] = mapped_column(JSON, default=list)
    gated: Mapped[bool] = mapped_column(Boolean, default=False)
    gate_reason: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latency_seconds: Mapped[float] = mapped_column(Float, default=0.0)


class EvalResult(Base):
    """Eval framework results (F-12, Section 8 catalogue E-01..E-16)."""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True
    )
    eval_id: Mapped[str] = mapped_column(String(8), index=True)
    engine: Mapped[str] = mapped_column(String(24))
    metric: Mapped[str] = mapped_column(String(128))
    score: Mapped[float] = mapped_column(Float)
    target: Mapped[str] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean)
    method: Mapped[str] = mapped_column(String(24), default="rule")  # rule|llm_judge|human
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelEvalResult(Base):
    """Model qualification benchmarks (suites S-CLS..S-JDG, model_evals.py)."""

    __tablename__ = "model_eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    model_name: Mapped[str] = mapped_column(String(64), index=True)
    suite_id: Mapped[str] = mapped_column(String(8), index=True)
    engine: Mapped[str] = mapped_column(String(24))
    score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16), default="completed")  # completed|skipped|error
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """Audit trail required by F-11 RBAC."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    action: Mapped[str] = mapped_column(String(128))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
