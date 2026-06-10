"""REST API (PRD 7.1: 'API-first — every engine exposes REST interfaces').

Tenancy is resolved via the X-Tenant header (slug); defaults to the demo
tenant. Engine-mutating routes enforce NS19 role permissions via the
X-Role header (F-11).
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import models, schemas
from .config import get_settings
from .contexts import INDUSTRY_CONTEXTS, NS19_ROLES, role_can_operate
from .database import get_db
from .engines import building, listening, prototype
from .engines import behavioral as behavioral_engine
from .evals import EVAL_CATALOGUE, run_engine_evals, run_eval
from .pipeline import pipeline_summary, run_pipeline

router = APIRouter(prefix="/api/v1")


def get_tenant(
    db: Session = Depends(get_db),
    x_tenant: str | None = Header(default=None),
) -> models.Tenant:
    slug = x_tenant or get_settings().default_tenant_slug
    tenant = db.query(models.Tenant).filter(models.Tenant.slug == slug).first()
    if not tenant:
        raise HTTPException(404, f"Unknown tenant '{slug}'")
    return tenant


def require_role(engine: str):
    def checker(x_role: str | None = Header(default="coe_lead")) -> str:
        role = x_role or "coe_lead"
        if role not in NS19_ROLES:
            raise HTTPException(403, f"Unknown role '{role}'")
        if not role_can_operate(role, engine):
            raise HTTPException(
                403, f"Role '{role}' is not permitted to operate the {engine} engine"
            )
        return role

    return checker


def audit(db: Session, tenant_id: int, actor: str, action: str, detail: str = "") -> None:
    db.add(models.AuditLog(tenant_id=tenant_id, actor=actor, action=action, detail=detail))
    db.commit()


# ---------------------------------------------------------------- tenants --
@router.post("/tenants", status_code=201)
def create_tenant(body: schemas.TenantCreate, db: Session = Depends(get_db)):
    if body.industry_context not in INDUSTRY_CONTEXTS:
        raise HTTPException(422, f"Unknown industry context '{body.industry_context}'")
    if db.query(models.Tenant).filter(models.Tenant.slug == body.slug).first():
        raise HTTPException(409, f"Tenant '{body.slug}' already exists")
    tenant = models.Tenant(**body.model_dump())
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/tenants/me")
def get_current_tenant(tenant: models.Tenant = Depends(get_tenant)):
    return tenant


@router.post("/tenants/me/users", status_code=201)
def add_user(
    body: schemas.UserCreate,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    if body.role not in NS19_ROLES:
        raise HTTPException(422, f"Unknown role '{body.role}'. Valid: {list(NS19_ROLES)}")
    user = models.User(tenant_id=tenant.id, **body.model_dump())
    db.add(user)
    db.commit()
    db.refresh(user)
    audit(db, tenant.id, "admin", "user_created", f"{body.email} as {body.role}")
    return user


@router.get("/roles")
def list_roles():
    return NS19_ROLES


# --------------------------------------------------------------- contexts --
@router.get("/contexts")
def list_contexts():
    return {
        key: {"label": ctx["label"], "priority": ctx["priority"],
              "signal_taxonomy": ctx["signal_taxonomy"],
              "journey_stages": ctx["journey_stages"]}
        for key, ctx in INDUSTRY_CONTEXTS.items()
    }


@router.get("/contexts/{industry}")
def get_industry_context(industry: str):
    if industry not in INDUSTRY_CONTEXTS:
        raise HTTPException(404, f"Unknown industry '{industry}'")
    return INDUSTRY_CONTEXTS[industry]


@router.put("/tenants/me/context")
def switch_context(
    body: schemas.ContextSwitch,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """F-09: Industry Context Switcher."""
    if body.industry_context not in INDUSTRY_CONTEXTS:
        raise HTTPException(422, f"Unknown industry context '{body.industry_context}'")
    tenant.industry_context = body.industry_context
    db.commit()
    audit(db, tenant.id, "admin", "context_switched", body.industry_context)
    return {"tenant": tenant.slug, "industry_context": tenant.industry_context}


# ------------------------------------------------------ listening engine --
@router.post("/signals", status_code=201)
def ingest_signal(
    body: schemas.SignalIn,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("listening")),
):
    """F-01: single-signal ingestion (API / webhook / VoC / CRM…)."""
    try:
        signal = listening.ingest_signal(db, tenant, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return signal


@router.post("/signals/batch", status_code=201)
def ingest_batch(
    body: schemas.SignalBatchIn,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("listening")),
):
    """F-01: batch ingestion (file upload / CRM export path)."""
    results = {"ingested": 0, "duplicates": 0}
    for item in body.signals:
        try:
            signal = listening.ingest_signal(db, tenant, **item.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        results["duplicates" if signal.is_duplicate else "ingested"] += 1
    return results


@router.get("/signals")
def list_signals(
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    include_duplicates: bool = False,
    limit: int = 100,
):
    q = db.query(models.Signal).filter(models.Signal.tenant_id == tenant.id)
    if not include_duplicates:
        q = q.filter(models.Signal.is_duplicate.is_(False))
    return q.order_by(models.Signal.id.desc()).limit(limit).all()


@router.get("/insight-cards")
def list_insight_cards(
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    limit: int = 100,
):
    return (
        db.query(models.InsightCard)
        .filter(models.InsightCard.tenant_id == tenant.id)
        .order_by(models.InsightCard.id.desc())
        .limit(limit)
        .all()
    )


# ----------------------------------------------------- behavioral engine --
@router.post("/clusters/run", status_code=201)
def run_clusters(
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("behavioral")),
):
    """F-03: (re)generate behavioral clusters."""
    clusters = behavioral_engine.run_clustering(db, tenant)
    audit(db, tenant.id, role, "clusters_generated", f"{len(clusters)} clusters")
    return clusters


@router.get("/clusters")
def list_clusters(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    return (
        db.query(models.BehavioralCluster)
        .filter(models.BehavioralCluster.tenant_id == tenant.id)
        .all()
    )


@router.post("/hypotheses/generate", status_code=201)
def generate_hypotheses(
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("behavioral")),
):
    """F-04: ranked feature hypotheses for every cluster."""
    hypotheses = behavioral_engine.generate_hypotheses(db, tenant)
    audit(db, tenant.id, role, "hypotheses_generated", f"{len(hypotheses)} hypotheses")
    return hypotheses


@router.get("/hypotheses")
def list_hypotheses(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    return (
        db.query(models.FeatureHypothesis)
        .filter(models.FeatureHypothesis.tenant_id == tenant.id)
        .order_by(models.FeatureHypothesis.confidence.desc())
        .all()
    )


@router.post("/hypotheses/{hypothesis_id}/decision")
def decide_hypothesis(
    hypothesis_id: int,
    body: schemas.HypothesisDecision,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("prototype")),
):
    hyp = db.get(models.FeatureHypothesis, hypothesis_id)
    if not hyp or hyp.tenant_id != tenant.id:
        raise HTTPException(404, "Hypothesis not found")
    prototype.validate_hypothesis(db, hyp, body.accepted)
    audit(db, tenant.id, role, "hypothesis_decided", f"#{hypothesis_id} -> {hyp.status}")
    return hyp


# ------------------------------------------------------ prototype engine --
@router.post("/briefs/generate/{hypothesis_id}", status_code=201)
def generate_brief(
    hypothesis_id: int,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("prototype")),
):
    """F-05: prototype brief from hypothesis."""
    hyp = db.get(models.FeatureHypothesis, hypothesis_id)
    if not hyp or hyp.tenant_id != tenant.id:
        raise HTTPException(404, "Hypothesis not found")
    return prototype.generate_brief(db, tenant, hyp)


@router.get("/briefs")
def list_briefs(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    return (
        db.query(models.PrototypeBrief)
        .filter(models.PrototypeBrief.tenant_id == tenant.id)
        .all()
    )


@router.patch("/briefs/{brief_id}")
def edit_brief(
    brief_id: int,
    body: schemas.BriefEdit,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("prototype")),
):
    """PO edits brief fields — telemetry feeds E-09 (PO Edit Rate)."""
    brief = db.get(models.PrototypeBrief, brief_id)
    if not brief or brief.tenant_id != tenant.id:
        raise HTTPException(404, "Brief not found")
    brief.fields = body.fields
    brief.fields_edited = body.fields_edited
    db.commit()
    db.refresh(brief)
    return brief


@router.post("/surveys/generate/{brief_id}", status_code=201)
def generate_survey(
    brief_id: int,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("prototype")),
):
    """F-06: cohort + auto-generated survey with power check."""
    brief = db.get(models.PrototypeBrief, brief_id)
    if not brief or brief.tenant_id != tenant.id:
        raise HTTPException(404, "Brief not found")
    return prototype.build_survey(db, tenant, brief)


@router.get("/surveys")
def list_surveys(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    return db.query(models.Survey).filter(models.Survey.tenant_id == tenant.id).all()


@router.post("/surveys/{survey_id}/responses")
def add_responses(
    survey_id: int,
    body: schemas.SurveyResponses,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    survey = db.get(models.Survey, survey_id)
    if not survey or survey.tenant_id != tenant.id:
        raise HTTPException(404, "Survey not found")
    return prototype.record_responses(db, survey, body.count)


# ------------------------------------------------------- building engine --
@router.post("/ujms/generate/{hypothesis_id}", status_code=201)
def generate_ujm(
    hypothesis_id: int,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("building")),
):
    """F-07: UJM auto-generation from a validated hypothesis."""
    hyp = db.get(models.FeatureHypothesis, hypothesis_id)
    if not hyp or hyp.tenant_id != tenant.id:
        raise HTTPException(404, "Hypothesis not found")
    if hyp.status != "validated":
        raise HTTPException(409, "Hypothesis must be validated before building (NS19 gate)")
    return building.generate_ujm(db, tenant, hyp)


@router.get("/ujms")
def list_ujms(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    return (
        db.query(models.UserJourneyMap)
        .filter(models.UserJourneyMap.tenant_id == tenant.id)
        .all()
    )


@router.post("/ujms/{ujm_id}/review")
def review_ujm(
    ujm_id: int,
    body: schemas.UJMReview,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("building")),
):
    """Expert panel acceptance — feeds E-12."""
    ujm = db.get(models.UserJourneyMap, ujm_id)
    if not ujm or ujm.tenant_id != tenant.id:
        raise HTTPException(404, "UJM not found")
    ujm.status = "accepted" if body.accepted else "rejected"
    db.commit()
    db.refresh(ujm)
    return ujm


@router.get("/ujms/{ujm_id}/export/{fmt}")
def export_ujm(
    ujm_id: int,
    fmt: str,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    if fmt not in {"miro", "figma", "pdf", "jira"}:
        raise HTTPException(422, "Format must be one of: miro, figma, pdf, jira")
    ujm = db.get(models.UserJourneyMap, ujm_id)
    if not ujm or ujm.tenant_id != tenant.id:
        raise HTTPException(404, "UJM not found")
    return building.export_payload(ujm, fmt)


@router.post("/feature-cards/generate/{ujm_id}", status_code=201)
def generate_cards(
    ujm_id: int,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("building")),
):
    """F-08: feature cards + story map from UJM."""
    ujm = db.get(models.UserJourneyMap, ujm_id)
    if not ujm or ujm.tenant_id != tenant.id:
        raise HTTPException(404, "UJM not found")
    return building.generate_feature_cards(db, tenant, ujm)


@router.get("/feature-cards")
def list_cards(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    return (
        db.query(models.FeatureCard)
        .filter(models.FeatureCard.tenant_id == tenant.id)
        .all()
    )


@router.get("/story-map/{ujm_id}")
def get_story_map(
    ujm_id: int,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ujm = db.get(models.UserJourneyMap, ujm_id)
    if not ujm or ujm.tenant_id != tenant.id:
        raise HTTPException(404, "UJM not found")
    return building.story_map(db, tenant, ujm)


# ------------------------------------------------------------- pipeline --
@router.post("/pipeline/run", status_code=201)
def trigger_pipeline(
    body: schemas.PipelineRunRequest,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    role: str = Depends(require_role("platform")),
):
    run = run_pipeline(
        db, tenant, auto_validate=body.auto_validate, enforce_gates=body.enforce_gates
    )
    audit(db, tenant.id, role, "pipeline_run", f"run #{run.id} -> {run.status}")
    return run


@router.get("/pipeline/summary")
def get_pipeline_summary(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    """F-10: real-time dashboard payload."""
    return pipeline_summary(db, tenant)


@router.get("/pipeline/runs")
def list_runs(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    return (
        db.query(models.PipelineRun)
        .filter(models.PipelineRun.tenant_id == tenant.id)
        .order_by(models.PipelineRun.id.desc())
        .limit(20)
        .all()
    )


# ----------------------------------------------------------------- evals --
@router.get("/evals/catalogue")
def eval_catalogue():
    return EVAL_CATALOGUE


@router.post("/evals/run/{eval_id}", status_code=201)
def trigger_eval(
    eval_id: str,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    if eval_id not in EVAL_CATALOGUE:
        raise HTTPException(404, f"Unknown eval '{eval_id}'")
    return run_eval(db, tenant, eval_id)


@router.post("/evals/run-engine/{engine}", status_code=201)
def trigger_engine_evals(
    engine: str,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    results, failures = run_engine_evals(db, tenant, engine)
    return {"results": results, "gate_failures": failures}


@router.get("/evals/results")
def eval_results(
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    limit: int = 64,
):
    return (
        db.query(models.EvalResult)
        .filter(models.EvalResult.tenant_id == tenant.id)
        .order_by(models.EvalResult.id.desc())
        .limit(limit)
        .all()
    )


@router.get("/evals/dashboard")
def quality_dashboard(
    tenant: models.Tenant = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Quality Intelligence Dashboard (PRD 8.1): latest result per eval."""
    latest: dict[str, models.EvalResult] = {}
    for r in (
        db.query(models.EvalResult)
        .filter(models.EvalResult.tenant_id == tenant.id)
        .order_by(models.EvalResult.id)
        .all()
    ):
        latest[r.eval_id] = r
    return {
        eval_id: {
            "metric": spec["metric"],
            "engine": spec["engine"],
            "target": spec["target"],
            "gate": spec.get("gate", False),
            "latest_score": latest[eval_id].score if eval_id in latest else None,
            "passed": latest[eval_id].passed if eval_id in latest else None,
            "method": spec["method"],
        }
        for eval_id, spec in EVAL_CATALOGUE.items()
    }


@router.post("/evals/expert-rating", status_code=201)
def submit_expert_rating(
    body: schemas.ExpertRating,
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Human expert panel input (PRD 8.3: panels sourced from client CoE)."""
    spec = EVAL_CATALOGUE[body.eval_id]
    result = models.EvalResult(
        tenant_id=tenant.id,
        eval_id=body.eval_id,
        engine=spec["engine"],
        metric=spec["metric"],
        score=body.score,
        target=spec["target"],
        passed=body.score >= spec["threshold"],
        method="human",
        details={"rater": body.rater, "notes": body.notes},
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


# ------------------------------------------------------------------ misc --
@router.get("/audit-log")
def get_audit_log(
    tenant: models.Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    limit: int = 50,
):
    return (
        db.query(models.AuditLog)
        .filter(models.AuditLog.tenant_id == tenant.id)
        .order_by(models.AuditLog.id.desc())
        .limit(limit)
        .all()
    )
