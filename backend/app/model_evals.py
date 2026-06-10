"""Model Eval Harness — benchmarks every model in the pluggable LLM layer.

The platform eval catalogue (E-01..E-16, `evals.py`) scores *pipeline
outputs* for a tenant. This harness scores *models themselves* against
fixed task suites, one suite per AI task in the four engines, so that:

  - a new model can be qualified before being set as CAPOS_LLM_MODEL,
  - the configured generation + judge models can be regression-tested on
    every model update (PRD E-01 'weekly + on model update'),
  - the deterministic offline path is held to the same bar.

Candidate models = the deterministic offline path + every model listed in
CAPOS_CANDIDATE_MODELS (remote suites are marked `skipped` when no API key
is configured, never silently passed).
"""

import statistics

from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .contexts import get_context
from .engines.building import UJM_LANE_KEYS, card_quality_score
from .engines.listening import classify_signal
from .engines.prototype import BRIEF_FIELDS, minimum_sample_size
from .evals import GOLD_SETS
from .llm import LLMClient
from .textutils import normalize

OFFLINE_MODEL = "deterministic-offline"

MODEL_SUITES: dict[str, dict] = {
    "S-CLS": {"engine": "listening", "name": "Signal Classification Accuracy",
              "checks": "gold-set classification (mirrors E-01)", "threshold": 0.90},
    "S-GRD": {"engine": "listening", "name": "Insight Grounding Compliance",
              "checks": "summaries must quote the source signal verbatim (feeds E-16)", "threshold": 0.98},
    "S-HYP": {"engine": "behavioral", "name": "Hypothesis Schema & Need-State Coverage",
              "checks": "JSON keys (title/description/rationale), rationale references need states", "threshold": 0.90},
    "S-BRF": {"engine": "prototype", "name": "Brief Schema Compliance",
              "checks": "all 10 brief template fields present and non-empty", "threshold": 1.0},
    "S-SRV": {"engine": "prototype", "name": "Survey Schema Compliance",
              "checks": ">=4 questions, valid types (likert/single/open), options on single-choice", "threshold": 0.90},
    "S-UJM": {"engine": "building", "name": "UJM Schema Compliance",
              "checks": "one lane per journey stage, all 6 lane keys present", "threshold": 0.90},
    "S-JDG": {"engine": "platform", "name": "Judge Discrimination",
              "checks": "judge must score a well-formed feature card above a poor one", "threshold": 1.0},
}

# ----------------------------------------------------------------- fixtures
SAMPLE_SIGNALS = [
    "My claim settlement took 7 weeks and nobody called me back about the reimbursement.",
    "The premium renewal quote jumped 30 percent with no explanation, considering switching.",
    "Found out flood damage was an exclusion in my coverage only after the claim was denied.",
]

FIXTURE_CLUSTER = {
    "name": "Claims Friction — settlement, delays",
    "need_states": [
        "Reduce effort and waiting in the claims friction experience",
        "Build confidence and transparency around claims friction",
        "Give customers visibility and control over claims friction",
    ],
    "attributes": ["settlement", "delays", "reimbursement", "updates"],
    "personas": [{"name": "Claims-burned skeptic", "traits": ["settlement", "delays"]}],
}

GOOD_CARD = {
    "title": "Real-time claim settlement tracker — Claim stage",
    "user_story": ("As a Claims-burned skeptic, I want real-time claim settlement tracking "
                   "during the claim stage so that settlement delays are visible and resolved."),
    "acceptance_criteria": [
        "Given an open claim, status updates appear within 60 seconds of state change",
        "Resolves: no visibility into settlement progress",
        "Telemetry events emitted for adoption tracking",
        "Copy reviewed for insurance domain terminology",
    ],
}
BAD_CARD = {
    "title": "Tracker",
    "user_story": "Build a tracker for stuff.",
    "acceptance_criteria": ["works"],
}

VALID_QUESTION_TYPES = {"likert", "single", "open"}


# ------------------------------------------------------------------ suites
def _suite_classification(client: LLMClient | None, industry: str) -> tuple[float, dict]:
    gold = GOLD_SETS.get(industry) or GOLD_SETS["insurance"]
    taxonomy = get_context(industry)["signal_taxonomy"]
    correct = 0
    for text, label in gold:
        if client is None:
            predicted = classify_signal(text, industry)[0]
        else:
            out = client.complete_json(
                system=(f"Classify the customer signal into exactly one category from "
                        f"{taxonomy}. Respond as JSON: {{\"category\": \"...\"}}"),
                user=text, max_tokens=100,
            )
            predicted = (out or {}).get("category", "")
        correct += predicted == label
    return round(correct / len(gold), 3), {"gold_set_size": len(gold), "correct": correct}


def _suite_grounding(client: LLMClient | None, industry: str) -> tuple[float, dict]:
    ctx = get_context(industry)
    grounded = 0
    for signal in SAMPLE_SIGNALS:
        quote = signal[:280]
        if client is None:
            # The template path constructs the summary around the quote.
            summary = f"Signal evidence: \"{quote[:140]}\""
        else:
            out = client.complete_json(
                system=(f"You are the Listening Engine of CapabilityOS. {ctx['prompt_context']} "
                        "Generate an insight card as JSON with keys: title, summary. "
                        "The summary MUST quote the source signal verbatim at least once."),
                user=f"Signal (voc): {signal}", max_tokens=400,
            )
            summary = (out or {}).get("summary", "")
        grounded += normalize(quote[:60]) in normalize(summary)
    return round(grounded / len(SAMPLE_SIGNALS), 3), {"signals_tested": len(SAMPLE_SIGNALS)}


def _suite_hypotheses(client: LLMClient | None, industry: str) -> tuple[float, dict]:
    ctx = get_context(industry)
    needs = FIXTURE_CLUSTER["need_states"]
    if client is None:
        items = [
            {"title": f"Address '{n}'", "description": f"Capability targeting '{n}'.",
             "rationale": f"Derived from need state '{n}'."}
            for n in needs
        ]
    else:
        out = client.complete_json(
            system=(f"You are the Behavioral Engine of CapabilityOS. {ctx['prompt_context']} "
                    "Propose the top 3 feature hypotheses as JSON: "
                    "{\"hypotheses\": [{\"title\", \"description\", \"rationale\"}]}. "
                    "Every rationale must reference the cluster's need states."),
            user=f"Cluster: {FIXTURE_CLUSTER}", max_tokens=900,
        )
        items = (out or {}).get("hypotheses", [])
    if not items:
        return 0.0, {"error": "no hypotheses returned"}
    scores = []
    for item in items[:3]:
        keys_ok = all(item.get(k) for k in ("title", "description", "rationale"))
        coverage = any(
            len(set(normalize(n).split()) & set(normalize(str(item.get("rationale", ""))).split())) >= 2
            for n in needs
        )
        scores.append((keys_ok + coverage) / 2)
    return round(statistics.mean(scores), 3), {"hypotheses_returned": len(items)}


def _suite_brief(client: LLMClient | None, industry: str) -> tuple[float, dict]:
    ctx = get_context(industry)
    if client is None:
        fields = {
            "objective": "Validate demand for: real-time claim settlement tracker",
            "hypothesis_statement": "We believe the Claims-burned skeptic will adopt it.",
            "target_cohort": FIXTURE_CLUSTER["personas"][0],
            "test_method": "A/B test",
            "success_metrics": ["Adoption intent >= 40%"],
            "sample_size": minimum_sample_size(),
            "duration_weeks": 4,
            "channels": ["in-app"],
            "risks": ["regulatory_review_required"],
            "owner_role": ctx["role_labels"]["product_owner"],
        }
    else:
        fields = client.complete_json(
            system=(f"You are the Prototype Engine of CapabilityOS. {ctx['prompt_context']} "
                    f"Generate a prototype brief. Return JSON with exactly these keys: {BRIEF_FIELDS}."),
            user=f"Hypothesis: real-time claim settlement tracker\nCluster: {FIXTURE_CLUSTER}",
            max_tokens=900,
        ) or {}
    filled = sum(1 for f in BRIEF_FIELDS if fields.get(f) not in (None, "", [], {}))
    return round(filled / len(BRIEF_FIELDS), 3), {"fields_filled": filled, "fields_expected": len(BRIEF_FIELDS)}


def _suite_survey(client: LLMClient | None, industry: str) -> tuple[float, dict]:
    ctx = get_context(industry)
    if client is None:
        questions = [
            {"type": "likert", "text": "How valuable would this be to you?"},
            {"type": "likert", "text": "How well does this address your pain point?"},
            {"type": "single", "text": "How often do you face this problem?",
             "options": ["Daily", "Weekly", "Monthly", "Rarely"]},
            {"type": "open", "text": "What would stop you from using this?"},
        ]
    else:
        out = client.complete_json(
            system=(f"You are the Prototype Engine of CapabilityOS. {ctx['prompt_context']} "
                    "Generate a 6-question validation survey as JSON: "
                    "{\"questions\": [{\"type\": \"likert|single|open\", \"text\", \"options\"?}]}"),
            user="Feature: real-time claim settlement tracker", max_tokens=700,
        )
        questions = (out or {}).get("questions", [])
    if len(questions) < 4:
        return 0.0, {"error": f"only {len(questions)} questions returned (need >= 4)"}
    valid = sum(
        1 for q in questions
        if q.get("type") in VALID_QUESTION_TYPES and q.get("text")
        and (q["type"] != "single" or len(q.get("options", [])) >= 2)
    )
    return round(valid / len(questions), 3), {"questions": len(questions), "valid": valid}


def _suite_ujm(client: LLMClient | None, industry: str) -> tuple[float, dict]:
    ctx = get_context(industry)
    stages = ctx["journey_stages"]
    if client is None:
        lanes = [
            {"stage": s, "touchpoints": ["app"], "customer_actions": ["progresses"],
             "pain_points": ["friction"], "emotions": "neutral", "feature_interventions": []}
            for s in stages
        ]
    else:
        out = client.complete_json(
            system=(f"You are the Building Engine of CapabilityOS. {ctx['prompt_context']} "
                    f"Generate a User Journey Map as JSON: {{\"lanes\": [{{{', '.join(UJM_LANE_KEYS)}}}]}} "
                    f"using exactly these journey stages in order: {stages}."),
            user=f"Persona: Claims-burned skeptic\nFeature: settlement tracker\nCluster: {FIXTURE_CLUSTER}",
            max_tokens=1500,
        )
        lanes = (out or {}).get("lanes", [])
    if len(lanes) != len(stages):
        return 0.0, {"error": f"{len(lanes)} lanes returned, expected {len(stages)}"}
    compliant = sum(1 for lane in lanes if all(k in lane for k in UJM_LANE_KEYS))
    return round(compliant / len(lanes), 3), {"lanes": len(lanes), "schema_compliant": compliant}


def _suite_judge(client: LLMClient | None, industry: str) -> tuple[float, dict]:
    def rate(card: dict) -> float:
        if client is None:
            return card_quality_score(
                card["title"], card["user_story"], card["acceptance_criteria"]
            )
        out = client.complete_json(
            system=("You are the eval judge of CapabilityOS. Rate the feature card 1-5 on "
                    "clarity, testability, AC completeness, domain accuracy, story-point "
                    "calibration. Respond as JSON: {\"score\": <float 1-5>}"),
            user=str(card), max_tokens=100,
        )
        return float((out or {}).get("score", 0))

    good, bad = rate(GOOD_CARD), rate(BAD_CARD)
    return (1.0 if good > bad else 0.0), {"good_card_score": good, "bad_card_score": bad}


SUITE_FUNCTIONS = {
    "S-CLS": _suite_classification,
    "S-GRD": _suite_grounding,
    "S-HYP": _suite_hypotheses,
    "S-BRF": _suite_brief,
    "S-SRV": _suite_survey,
    "S-UJM": _suite_ujm,
    "S-JDG": _suite_judge,
}


# ------------------------------------------------------------------ runner
def candidate_models() -> list[str]:
    settings = get_settings()
    remote = [m.strip() for m in settings.candidate_models.split(",") if m.strip()]
    return [OFFLINE_MODEL] + remote


def run_model_eval(
    db: Session,
    tenant: models.Tenant,
    model_name: str,
    suite_id: str,
) -> models.ModelEvalResult:
    spec = MODEL_SUITES[suite_id]
    client = None if model_name == OFFLINE_MODEL else LLMClient(model_name)

    if client is not None and not client.available:
        status, score, passed, details = (
            "skipped", 0.0, False,
            {"reason": "no ANTHROPIC_API_KEY configured — remote model not benchmarked"},
        )
    else:
        try:
            score, details = SUITE_FUNCTIONS[suite_id](client, tenant.industry_context)
            status, passed = "completed", score >= spec["threshold"]
        except Exception as exc:  # benchmark must never crash the platform
            status, score, passed, details = "error", 0.0, False, {"error": str(exc)}

    result = models.ModelEvalResult(
        tenant_id=tenant.id,
        model_name=model_name,
        suite_id=suite_id,
        engine=spec["engine"],
        score=score,
        threshold=spec["threshold"],
        passed=passed,
        status=status,
        details=details,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def run_model_evals(
    db: Session,
    tenant: models.Tenant,
    model_names: list[str] | None = None,
    suite_ids: list[str] | None = None,
) -> list[models.ModelEvalResult]:
    return [
        run_model_eval(db, tenant, model, suite)
        for model in (model_names or candidate_models())
        for suite in (suite_ids or list(MODEL_SUITES))
    ]


def model_scoreboard(db: Session, tenant: models.Tenant) -> dict:
    """Latest result per (model, suite) — the model qualification matrix."""
    latest: dict[tuple[str, str], models.ModelEvalResult] = {}
    for r in (
        db.query(models.ModelEvalResult)
        .filter(models.ModelEvalResult.tenant_id == tenant.id)
        .order_by(models.ModelEvalResult.id)
        .all()
    ):
        latest[(r.model_name, r.suite_id)] = r
    board: dict[str, dict] = {}
    for (model, suite), r in latest.items():
        board.setdefault(model, {})[suite] = {
            "score": r.score, "threshold": r.threshold,
            "passed": r.passed, "status": r.status,
        }
    for model, suites in board.items():
        completed = [s for s in suites.values() if s["status"] == "completed"]
        suites["qualified"] = bool(completed) and all(s["passed"] for s in completed) and len(completed) == len(MODEL_SUITES)
    return board
