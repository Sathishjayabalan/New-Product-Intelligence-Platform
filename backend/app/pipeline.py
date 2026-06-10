"""Pipeline Orchestrator (F-10): signal -> insight -> cluster -> hypothesis
-> brief -> survey -> UJM -> feature card, with eval gates between stages
(PRD 8.1: output below threshold is flagged for human review before
advancing)."""

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models
from .engines import behavioral, building, prototype
from .evals import run_engine_evals

STAGES = ["listening", "behavioral", "prototype", "building", "completed"]


def _log_stage(run: models.PipelineRun, stage: str, detail: dict) -> None:
    run.stage_log = (run.stage_log or []) + [
        {"stage": stage, "at": datetime.now(timezone.utc).isoformat(), **detail}
    ]


def run_pipeline(
    db: Session,
    tenant: models.Tenant,
    *,
    auto_validate: bool = True,
    enforce_gates: bool = True,
) -> models.PipelineRun:
    """Execute a full NS19 pipeline run for a tenant.

    `auto_validate=True` advances top-confidence hypotheses through the
    validation gate automatically (demo/pilot mode); in production the
    Product Owner validates via the API.
    """
    run = models.PipelineRun(tenant_id=tenant.id)
    db.add(run)
    db.commit()
    db.refresh(run)
    t0 = time.monotonic()

    def finish(status: str, gate_reason: str = "") -> models.PipelineRun:
        run.status = status
        run.gated = bool(gate_reason)
        run.gate_reason = gate_reason
        run.finished_at = datetime.now(timezone.utc)
        run.latency_seconds = round(time.monotonic() - t0, 3)
        db.commit()
        db.refresh(run)
        return run

    # --- Stage 1: Listening (signals already ingested via the hub) ----------
    run.current_stage = "listening"
    signal_count = (
        db.query(models.Signal)
        .filter(models.Signal.tenant_id == tenant.id, models.Signal.is_duplicate.is_(False))
        .count()
    )
    if signal_count == 0:
        return finish("failed", "No signals ingested — feed the Listening Engine first.")
    _, failures = run_engine_evals(db, tenant, "listening", run.id)
    _log_stage(run, "listening", {"signals": signal_count, "eval_failures": failures})
    if failures and enforce_gates:
        return finish("gated", "Listening evals below threshold: " + "; ".join(failures))

    # --- Stage 2: Behavioral -------------------------------------------------
    run.current_stage = "behavioral"
    db.commit()
    clusters = behavioral.run_clustering(db, tenant, run.id)
    hypotheses = behavioral.generate_hypotheses(db, tenant)
    _, failures = run_engine_evals(db, tenant, "behavioral", run.id)
    _log_stage(run, "behavioral", {"clusters": len(clusters), "hypotheses": len(hypotheses), "eval_failures": failures})
    if failures and enforce_gates:
        return finish("gated", "Behavioral evals below threshold: " + "; ".join(failures))

    # --- Stage 3: Prototype & Validation -------------------------------------
    run.current_stage = "prototype"
    db.commit()
    top_hypotheses = sorted(hypotheses, key=lambda h: -h.confidence)[:3]
    briefs, surveys = [], []
    for hyp in top_hypotheses:
        brief = prototype.generate_brief(db, tenant, hyp)
        briefs.append(brief)
        surveys.append(prototype.build_survey(db, tenant, brief))
        if auto_validate:
            prototype.validate_hypothesis(db, hyp, accepted=hyp.confidence >= 0.45)
    _, failures = run_engine_evals(db, tenant, "prototype", run.id)
    _log_stage(run, "prototype", {"briefs": len(briefs), "surveys": len(surveys), "eval_failures": failures})
    if failures and enforce_gates:
        return finish("gated", "Prototype evals below threshold: " + "; ".join(failures))

    # --- Stage 4: Building ----------------------------------------------------
    run.current_stage = "building"
    db.commit()
    validated = [h for h in top_hypotheses if h.status == "validated"]
    ujms, cards = [], []
    for hyp in validated:
        ujm = building.generate_ujm(db, tenant, hyp)
        ujms.append(ujm)
        cards.extend(building.generate_feature_cards(db, tenant, ujm))
    _, failures = run_engine_evals(db, tenant, "building", run.id)
    _log_stage(run, "building", {"validated_hypotheses": len(validated), "ujms": len(ujms), "feature_cards": len(cards), "eval_failures": failures})
    if failures and enforce_gates:
        return finish("gated", "Building evals below threshold: " + "; ".join(failures))

    run.current_stage = "completed"
    result = finish("completed")
    # Platform-level evals (latency, terminology) run after completion.
    run_engine_evals(db, tenant, "platform", run.id)
    return result


def pipeline_summary(db: Session, tenant: models.Tenant) -> dict:
    """Real-time dashboard payload: engine status, signal-to-feature flow,
    bottleneck detection (F-10)."""
    counts = {
        "signals": db.query(models.Signal).filter(models.Signal.tenant_id == tenant.id, models.Signal.is_duplicate.is_(False)).count(),
        "duplicates_blocked": db.query(models.Signal).filter(models.Signal.tenant_id == tenant.id, models.Signal.is_duplicate.is_(True)).count(),
        "insight_cards": db.query(models.InsightCard).filter(models.InsightCard.tenant_id == tenant.id).count(),
        "clusters": db.query(models.BehavioralCluster).filter(models.BehavioralCluster.tenant_id == tenant.id).count(),
        "hypotheses": db.query(models.FeatureHypothesis).filter(models.FeatureHypothesis.tenant_id == tenant.id).count(),
        "validated_hypotheses": db.query(models.FeatureHypothesis).filter(models.FeatureHypothesis.tenant_id == tenant.id, models.FeatureHypothesis.status == "validated").count(),
        "briefs": db.query(models.PrototypeBrief).filter(models.PrototypeBrief.tenant_id == tenant.id).count(),
        "surveys": db.query(models.Survey).filter(models.Survey.tenant_id == tenant.id).count(),
        "ujms": db.query(models.UserJourneyMap).filter(models.UserJourneyMap.tenant_id == tenant.id).count(),
        "feature_cards": db.query(models.FeatureCard).filter(models.FeatureCard.tenant_id == tenant.id).count(),
    }
    funnel = [
        ("signals", counts["signals"]),
        ("insight_cards", counts["insight_cards"]),
        ("clusters", counts["clusters"]),
        ("hypotheses", counts["hypotheses"]),
        ("validated_hypotheses", counts["validated_hypotheses"]),
        ("feature_cards", counts["feature_cards"]),
    ]
    bottleneck = None
    for (prev_name, prev), (name, cur) in zip(funnel, funnel[1:]):
        if prev > 0 and cur == 0:
            bottleneck = {"stage": name, "blocked_after": prev_name}
            break
    latest = (
        db.query(models.PipelineRun)
        .filter(models.PipelineRun.tenant_id == tenant.id)
        .order_by(models.PipelineRun.id.desc())
        .first()
    )
    return {
        "tenant": tenant.slug,
        "industry_context": tenant.industry_context,
        "counts": counts,
        "bottleneck": bottleneck,
        "latest_run": {
            "id": latest.id,
            "status": latest.status,
            "current_stage": latest.current_stage,
            "gated": latest.gated,
            "gate_reason": latest.gate_reason,
            "latency_seconds": latest.latency_seconds,
            "stage_log": latest.stage_log,
        }
        if latest
        else None,
    }
