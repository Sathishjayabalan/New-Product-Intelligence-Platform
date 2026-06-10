"""Engine 3: Prototype & Validation Engine.

Inputs:  feature hypotheses, customer cohort definitions
Process: prototype brief generation, survey generation, cohort matching,
         validation scoring
Outputs: validated feature set, user test results, prioritised backlog
(F-05 Prototype Brief Generator, F-06 Cohort Builder & Survey Engine)
"""

import math

from sqlalchemy.orm import Session

from .. import models
from ..contexts import get_context
from ..llm import generation_client

# The 10-field brief template audited by E-08.
BRIEF_FIELDS = [
    "objective", "hypothesis_statement", "target_cohort", "test_method",
    "success_metrics", "sample_size", "duration_weeks", "channels",
    "risks", "owner_role",
]

TEST_METHODS = ["A/B test", "fake-door test", "concierge prototype", "clickable prototype + survey"]


def generate_brief(
    db: Session, tenant: models.Tenant, hypothesis: models.FeatureHypothesis
) -> models.PrototypeBrief:
    """F-05: structured prototype brief from a hypothesis."""
    ctx = get_context(tenant.industry_context)
    cluster = db.get(models.BehavioralCluster, hypothesis.cluster_id)
    persona = (cluster.personas[0]["name"] if cluster and cluster.personas else "core segment")
    method = TEST_METHODS[hypothesis.rank % len(TEST_METHODS)]

    fields = {
        "objective": f"Validate demand for: {hypothesis.title}",
        "hypothesis_statement": (
            f"We believe {persona} will adopt '{hypothesis.title}' because "
            f"{hypothesis.rationale or 'the underlying need state is strongly evidenced'}."
        ),
        "target_cohort": {
            "persona": persona,
            "attributes": (cluster.attributes if cluster else [])[:4],
            "source": "CRM segment match",
        },
        "test_method": method,
        "success_metrics": [
            "Adoption intent >= 40% of tested cohort",
            "Task completion rate >= 70% in prototype",
            f"Confidence uplift over baseline {hypothesis.confidence:.0%}",
        ],
        "sample_size": minimum_sample_size(),
        "duration_weeks": 2 if method == "fake-door test" else 4,
        "channels": ["in-app", "email panel"],
        "risks": hypothesis.risk_flags or ["none identified"],
        "owner_role": ctx["role_labels"]["product_owner"],
    }

    llm = generation_client()
    enriched = llm.complete_json(
        system=(
            f"You are the Prototype Engine of CapabilityOS. {ctx['prompt_context']} "
            f"Refine this prototype brief. Return JSON with exactly these keys: {BRIEF_FIELDS}."
        ),
        user=f"Hypothesis: {hypothesis.title}\nDraft brief: {fields}",
        max_tokens=900,
    )
    if enriched and all(k in enriched for k in BRIEF_FIELDS):
        fields = enriched

    completeness = brief_completeness(fields)
    brief = models.PrototypeBrief(
        tenant_id=tenant.id,
        hypothesis_id=hypothesis.id,
        fields=fields,
        completeness_score=completeness,
        status="ready" if completeness >= 0.9 else "needs_review",
    )
    db.add(brief)
    db.commit()
    db.refresh(brief)
    return brief


def brief_completeness(fields: dict) -> float:
    """E-08: automated 10-field template completeness check."""
    filled = sum(1 for f in BRIEF_FIELDS if fields.get(f) not in (None, "", [], {}))
    return round(filled / len(BRIEF_FIELDS), 3)


def minimum_sample_size(
    baseline_rate: float = 0.4, mde: float = 0.1, alpha: float = 0.05, power: float = 0.8
) -> int:
    """E-10: minimum sample for detecting `mde` at 80% power (two-proportion
    z-test approximation)."""
    z_alpha, z_beta = 1.96, 0.84  # alpha=0.05 two-sided, power=0.8
    p_bar = baseline_rate + mde / 2
    n = ((z_alpha + z_beta) ** 2 * 2 * p_bar * (1 - p_bar)) / (mde**2)
    return math.ceil(n)


def build_survey(
    db: Session, tenant: models.Tenant, brief: models.PrototypeBrief
) -> models.Survey:
    """F-06: cohort definition + auto-generated survey with power check."""
    hypothesis = db.get(models.FeatureHypothesis, brief.hypothesis_id)
    title = hypothesis.title if hypothesis else "the proposed feature"
    cohort = brief.fields.get("target_cohort", {})

    questions = [
        {"type": "likert", "text": f"How valuable would '{title}' be to you?"},
        {"type": "likert", "text": "How well does this address your current pain point?"},
        {"type": "single", "text": "How often do you face this problem?",
         "options": ["Daily", "Weekly", "Monthly", "Rarely"]},
        {"type": "single", "text": f"Would you switch providers to get '{title}'?",
         "options": ["Definitely", "Probably", "Unsure", "No"]},
        {"type": "open", "text": "What would stop you from using this feature?"},
        {"type": "open", "text": "What is the closest alternative you use today?"},
    ]

    llm = generation_client()
    ctx = get_context(tenant.industry_context)
    enriched = llm.complete_json(
        system=(
            f"You are the Prototype Engine of CapabilityOS. {ctx['prompt_context']} "
            "Generate a 6-question validation survey as JSON: "
            "{\"questions\": [{\"type\": \"likert|single|open\", \"text\", \"options\"?}]}"
        ),
        user=f"Feature: {title}\nCohort: {cohort}",
        max_tokens=700,
    )
    if enriched and isinstance(enriched.get("questions"), list) and len(enriched["questions"]) >= 4:
        questions = enriched["questions"]

    survey = models.Survey(
        tenant_id=tenant.id,
        brief_id=brief.id,
        cohort=cohort,
        questions=questions,
        min_sample_size=minimum_sample_size(),
    )
    db.add(survey)
    db.commit()
    db.refresh(survey)
    return survey


def record_responses(db: Session, survey: models.Survey, count: int) -> models.Survey:
    """Collect responses; auto-flag statistical significance (E-10)."""
    survey.responses_collected += count
    survey.statistically_significant = (
        survey.responses_collected >= survey.min_sample_size
    )
    db.commit()
    db.refresh(survey)
    return survey


def validate_hypothesis(
    db: Session, hypothesis: models.FeatureHypothesis, accepted: bool
) -> models.FeatureHypothesis:
    """Validation gate: accepted hypotheses advance to the Building Engine."""
    hypothesis.status = "validated" if accepted else "rejected"
    db.commit()
    db.refresh(hypothesis)
    return hypothesis
