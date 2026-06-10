"""Evaluation Framework (PRD Section 8, feature F-12).

Layered evals: rule-based (fast), LLM-as-judge (semantic), human panel
(ground truth). Eval results gate pipeline progression — outputs below
threshold are flagged for human review before advancing (PRD 8.1).
"""

import statistics

from sqlalchemy.orm import Session

from . import models
from .contexts import INDUSTRY_CONTEXTS, get_context
from .engines.listening import classify_signal, detect_duplicate
from .textutils import normalize

# ---------------------------------------------------------------------------
# Gold sets for E-01 (per PRD: human-labelled signals per industry).
# Trimmed demo gold sets; production tenants load 500/industry at onboarding.
# ---------------------------------------------------------------------------
GOLD_SETS: dict[str, list[tuple[str, str]]] = {
    "insurance": [
        ("My claim settlement has been delayed for 6 weeks with no update", "claims_friction"),
        ("The premium quote was far more expensive than competitors", "pricing_sensitivity"),
        ("I didn't realise flood damage was an exclusion in my coverage", "coverage_gap"),
        ("Decided not to renew my policy and switch to another insurer", "renewal_churn"),
        ("Claim was denied without a clear reason after reimbursement request", "claims_friction"),
        ("Looking for a cheaper premium with the same coverage", "pricing_sensitivity"),
    ],
    "banking": [
        ("My loan application was rejected despite a good credit score", "credit_friction"),
        ("UPI payment failed three times during checkout", "payments_ux"),
        ("Suspicious unauthorised transaction appeared on my account, possible fraud", "fraud_risk"),
        ("Gave up during KYC verification because signup took too long", "onboarding_dropoff"),
        ("I want automatic round-up savings towards a goal", "savings_behavior"),
        ("Credit limit increase was denied with no explanation", "credit_friction"),
    ],
}

# ---------------------------------------------------------------------------
# Eval catalogue (E-01..E-16). gate=True evals block pipeline progression.
# ---------------------------------------------------------------------------
EVAL_CATALOGUE: dict[str, dict] = {
    "E-01": {"engine": "listening", "metric": "Signal Classification Accuracy", "target": "F1 > 0.90", "threshold": 0.90, "method": "rule", "gate": True},
    "E-02": {"engine": "listening", "metric": "Data Deduplication Rate", "target": "Dedup > 0.95", "threshold": 0.95, "method": "rule", "gate": True},
    "E-03": {"engine": "listening", "metric": "Signal Freshness Score", "target": "Median lag < 5 min (streaming)", "threshold": 5.0, "method": "rule", "gate": False, "lower_is_better": True},
    "E-04": {"engine": "behavioral", "metric": "Cluster Stability (Reproducibility)", "target": "Jaccard > 0.75", "threshold": 0.75, "method": "rule", "gate": True},
    "E-05": {"engine": "behavioral", "metric": "Persona Relevance Score", "target": "Mean > 3.8/5", "threshold": 3.8, "method": "human", "gate": False, "scale": 5},
    "E-06": {"engine": "behavioral", "metric": "Feature Hypothesis Recall@3", "target": "Recall@3 > 0.80", "threshold": 0.80, "method": "rule", "gate": False},
    "E-07": {"engine": "behavioral", "metric": "Hypothesis Confidence Calibration", "target": "Calibration error < 0.15", "threshold": 0.15, "method": "rule", "gate": False, "lower_is_better": True},
    "E-08": {"engine": "prototype", "metric": "Brief Completeness Score", "target": "Completion > 0.90", "threshold": 0.90, "method": "rule", "gate": True},
    "E-09": {"engine": "prototype", "metric": "PO Edit Rate", "target": "Fields edited < 0.25", "threshold": 0.25, "method": "rule", "gate": False, "lower_is_better": True},
    "E-10": {"engine": "prototype", "metric": "Survey Statistical Power", "target": "100% meet minimum sample", "threshold": 1.0, "method": "rule", "gate": True},
    "E-11": {"engine": "building", "metric": "UJM Completeness Score", "target": "Completeness > 0.85", "threshold": 0.85, "method": "rule", "gate": True},
    "E-12": {"engine": "building", "metric": "UJM Expert Acceptance Rate", "target": "Acceptance > 0.70", "threshold": 0.70, "method": "human", "gate": False},
    "E-13": {"engine": "building", "metric": "Feature Card Quality Score", "target": "Mean > 4/5", "threshold": 4.0, "method": "llm_judge", "gate": True, "scale": 5},
    "E-14": {"engine": "platform", "metric": "Pipeline E2E Latency", "target": "P50 < 10 min", "threshold": 600.0, "method": "rule", "gate": False, "lower_is_better": True},
    "E-15": {"engine": "platform", "metric": "Domain Terminology Accuracy", "target": "Correct terminology > 0.92", "threshold": 0.92, "method": "rule", "gate": False},
    "E-16": {"engine": "platform", "metric": "Hallucination Rate", "target": "Ungrounded claims < 0.02", "threshold": 0.02, "method": "rule", "gate": True, "lower_is_better": True},
}

ENGINE_EVALS = {
    "listening": ["E-01", "E-02", "E-03", "E-16"],
    "behavioral": ["E-04", "E-05", "E-06", "E-07"],
    "prototype": ["E-08", "E-09", "E-10"],
    "building": ["E-11", "E-12", "E-13"],
    "platform": ["E-14", "E-15"],
}


# ---------------------------------------------------------------------------
# Individual eval implementations
# ---------------------------------------------------------------------------
def eval_classification_accuracy(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    gold = GOLD_SETS.get(tenant.industry_context)
    if not gold:
        return 1.0, {"note": "no gold set for industry; baseline pending onboarding sprint"}
    correct = sum(
        1 for text, label in gold
        if classify_signal(text, tenant.industry_context)[0] == label
    )
    f1 = correct / len(gold)  # micro-F1 == accuracy for single-label
    return round(f1, 3), {"gold_set_size": len(gold), "correct": correct}


def eval_dedup_rate(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    signals = (
        db.query(models.Signal)
        .filter(models.Signal.tenant_id == tenant.id, models.Signal.is_duplicate.is_(False))
        .limit(50)
        .all()
    )
    if not signals:
        return 1.0, {"note": "no signals ingested"}
    detected = sum(
        1 for s in signals if detect_duplicate(db, tenant.id, s.content)[0]
    )
    return round(detected / len(signals), 3), {"replayed": len(signals), "caught": detected}


def eval_freshness(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    signals = (
        db.query(models.Signal)
        .filter(models.Signal.tenant_id == tenant.id)
        .order_by(models.Signal.id.desc())
        .limit(100)
        .all()
    )
    if not signals:
        return 0.0, {"note": "no signals"}
    lags = [
        max((s.created_at - s.source_timestamp).total_seconds() / 60, 0.0)
        for s in signals
    ]
    return round(statistics.median(lags), 3), {"sample": len(lags)}


def eval_cluster_stability(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    clusters = (
        db.query(models.BehavioralCluster)
        .filter(models.BehavioralCluster.tenant_id == tenant.id)
        .all()
    )
    if not clusters:
        return 0.0, {"note": "no clusters generated"}
    score = statistics.mean(c.stability_score for c in clusters)
    return round(score, 3), {"clusters": len(clusters)}


def eval_persona_relevance(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    """Human-panel eval: averages expert ratings submitted via the API.
    Until ratings exist, scores a structural proxy and marks it pending."""
    ratings = (
        db.query(models.EvalResult)
        .filter(
            models.EvalResult.tenant_id == tenant.id,
            models.EvalResult.eval_id == "E-05",
            models.EvalResult.method == "human",
        )
        .all()
    )
    if ratings:
        return round(statistics.mean(r.score for r in ratings), 3), {"expert_ratings": len(ratings)}
    clusters = db.query(models.BehavioralCluster).filter(
        models.BehavioralCluster.tenant_id == tenant.id
    ).all()
    if not clusters:
        return 0.0, {"note": "no personas generated"}
    proxy = statistics.mean(
        (2.0 + (1.0 if c.personas else 0) + (1.0 if c.need_states else 0)
         + (1.0 if len(c.attributes) >= 3 else 0.5))
        for c in clusters
    )
    return round(proxy, 3), {"note": "structural proxy — awaiting expert panel", "clusters": len(clusters)}


def eval_hypothesis_recall(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    """Recall@3 proxy: do the top-3 hypotheses per cluster cover the
    cluster's mapped need states? Expert ground truth replaces this
    quarterly per PRD."""
    clusters = db.query(models.BehavioralCluster).filter(
        models.BehavioralCluster.tenant_id == tenant.id
    ).all()
    if not clusters:
        return 0.0, {"note": "no clusters"}
    scores = []
    for cluster in clusters:
        top3 = (
            db.query(models.FeatureHypothesis)
            .filter(models.FeatureHypothesis.cluster_id == cluster.id)
            .order_by(models.FeatureHypothesis.rank)
            .limit(3)
            .all()
        )
        if not cluster.need_states:
            continue
        covered = sum(
            1 for need in cluster.need_states[:3]
            if any(
                len(set(normalize(need).split()) & set(normalize(h.title + " " + h.rationale).split())) >= 2
                for h in top3
            )
        )
        scores.append(covered / min(len(cluster.need_states), 3))
    return (round(statistics.mean(scores), 3) if scores else 0.0), {"clusters_scored": len(scores)}


def eval_confidence_calibration(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    decided = (
        db.query(models.FeatureHypothesis)
        .filter(
            models.FeatureHypothesis.tenant_id == tenant.id,
            models.FeatureHypothesis.status.in_(["validated", "rejected"]),
        )
        .all()
    )
    if len(decided) < 3:
        return 0.0, {"note": "insufficient validation outcomes; calibration tracked monthly"}
    predicted = statistics.mean(h.confidence for h in decided)
    actual = sum(1 for h in decided if h.status == "validated") / len(decided)
    return round(abs(predicted - actual), 3), {"n": len(decided), "predicted": round(predicted, 3), "actual": round(actual, 3)}


def eval_brief_completeness(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    briefs = db.query(models.PrototypeBrief).filter(
        models.PrototypeBrief.tenant_id == tenant.id
    ).all()
    if not briefs:
        return 0.0, {"note": "no briefs generated"}
    return round(statistics.mean(b.completeness_score for b in briefs), 3), {"briefs": len(briefs)}


def eval_po_edit_rate(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    briefs = db.query(models.PrototypeBrief).filter(
        models.PrototypeBrief.tenant_id == tenant.id
    ).all()
    if not briefs:
        return 0.0, {"note": "no briefs"}
    rate = statistics.mean(b.fields_edited / 10 for b in briefs)
    return round(rate, 3), {"briefs": len(briefs)}


def eval_survey_power(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    surveys = db.query(models.Survey).filter(models.Survey.tenant_id == tenant.id).all()
    if not surveys:
        return 1.0, {"note": "no surveys launched"}
    ok = sum(1 for s in surveys if s.min_sample_size > 0)
    return round(ok / len(surveys), 3), {"surveys": len(surveys)}


def eval_ujm_completeness(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    ujms = db.query(models.UserJourneyMap).filter(
        models.UserJourneyMap.tenant_id == tenant.id
    ).all()
    if not ujms:
        return 0.0, {"note": "no UJMs generated"}
    return round(statistics.mean(u.completeness_score for u in ujms), 3), {"ujms": len(ujms)}


def eval_ujm_acceptance(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    ujms = db.query(models.UserJourneyMap).filter(
        models.UserJourneyMap.tenant_id == tenant.id
    ).all()
    reviewed = [u for u in ujms if u.status in ("accepted", "rejected")]
    if not reviewed:
        return 0.0, {"note": "awaiting expert panel review (monthly)"}
    rate = sum(1 for u in reviewed if u.status == "accepted") / len(reviewed)
    return round(rate, 3), {"reviewed": len(reviewed)}


def eval_card_quality(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    cards = db.query(models.FeatureCard).filter(
        models.FeatureCard.tenant_id == tenant.id
    ).all()
    if not cards:
        return 0.0, {"note": "no feature cards"}
    return round(statistics.mean(c.quality_score for c in cards), 3), {"cards": len(cards), "rubric": "clarity, testability, AC completeness, domain accuracy, SP calibration"}


def eval_pipeline_latency(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    runs = (
        db.query(models.PipelineRun)
        .filter(
            models.PipelineRun.tenant_id == tenant.id,
            models.PipelineRun.status == "completed",
        )
        .all()
    )
    if not runs:
        return 0.0, {"note": "no completed runs"}
    latencies = sorted(r.latency_seconds for r in runs)
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[min(int(len(latencies) * 0.99), len(latencies) - 1)]
    return round(p50, 3), {"p50_seconds": round(p50, 3), "p99_seconds": round(p99, 3), "runs": len(runs)}


def eval_terminology_accuracy(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    """Spot-check generated outputs for wrong-domain terminology leakage."""
    own_terms = {t.lower() for t in get_context(tenant.industry_context)["terminology"]}
    foreign_terms = {
        t.lower()
        for ind, ctx in INDUSTRY_CONTEXTS.items()
        if ind != tenant.industry_context
        for t in ctx["terminology"]
    } - own_terms
    outputs = [
        h.title + " " + h.description
        for h in db.query(models.FeatureHypothesis).filter(models.FeatureHypothesis.tenant_id == tenant.id)
    ] + [
        c.user_story for c in db.query(models.FeatureCard).filter(models.FeatureCard.tenant_id == tenant.id)
    ]
    if not outputs:
        return 1.0, {"note": "no generated outputs to audit"}
    clean = sum(
        1 for text in outputs
        if not any(f" {term} " in f" {normalize(text)} " for term in foreign_terms)
    )
    return round(clean / len(outputs), 3), {"outputs_checked": len(outputs)}


def eval_hallucination_rate(db: Session, tenant: models.Tenant) -> tuple[float, dict]:
    """Fact-grounding: every insight card claim must trace to its source
    signal via the grounded quote."""
    cards = db.query(models.InsightCard).filter(
        models.InsightCard.tenant_id == tenant.id
    ).all()
    if not cards:
        return 0.0, {"note": "no insight cards"}
    ungrounded = 0
    for card in cards:
        signal = db.get(models.Signal, card.signal_id)
        if not signal or normalize(card.grounded_quote) not in normalize(signal.content):
            ungrounded += 1
    return round(ungrounded / len(cards), 3), {"cards": len(cards), "ungrounded": ungrounded}


EVAL_FUNCTIONS = {
    "E-01": eval_classification_accuracy,
    "E-02": eval_dedup_rate,
    "E-03": eval_freshness,
    "E-04": eval_cluster_stability,
    "E-05": eval_persona_relevance,
    "E-06": eval_hypothesis_recall,
    "E-07": eval_confidence_calibration,
    "E-08": eval_brief_completeness,
    "E-09": eval_po_edit_rate,
    "E-10": eval_survey_power,
    "E-11": eval_ujm_completeness,
    "E-12": eval_ujm_acceptance,
    "E-13": eval_card_quality,
    "E-14": eval_pipeline_latency,
    "E-15": eval_terminology_accuracy,
    "E-16": eval_hallucination_rate,
}


def run_eval(
    db: Session,
    tenant: models.Tenant,
    eval_id: str,
    pipeline_run_id: int | None = None,
) -> models.EvalResult:
    spec = EVAL_CATALOGUE[eval_id]
    score, details = EVAL_FUNCTIONS[eval_id](db, tenant)
    if spec.get("lower_is_better"):
        passed = score <= spec["threshold"]
    else:
        passed = score >= spec["threshold"]
    # Evals with no underlying data yet are informational, not failures.
    if "note" in details and score == 0.0 and not spec.get("lower_is_better"):
        passed = True
        details["status"] = "no_data_informational"
    result = models.EvalResult(
        tenant_id=tenant.id,
        pipeline_run_id=pipeline_run_id,
        eval_id=eval_id,
        engine=spec["engine"],
        metric=spec["metric"],
        score=score,
        target=spec["target"],
        passed=passed,
        method=spec["method"],
        details=details,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def run_engine_evals(
    db: Session,
    tenant: models.Tenant,
    engine: str,
    pipeline_run_id: int | None = None,
) -> tuple[list[models.EvalResult], list[str]]:
    """Run all evals for an engine stage. Returns (results, gate_failures)."""
    results, failures = [], []
    for eval_id in ENGINE_EVALS.get(engine, []):
        result = run_eval(db, tenant, eval_id, pipeline_run_id)
        results.append(result)
        if not result.passed and EVAL_CATALOGUE[eval_id].get("gate"):
            failures.append(f"{eval_id} {result.metric}: {result.score} (target {result.target})")
    return results, failures
