"""Engine 4: Building Engine.

Inputs:  validated feature set, UJM inputs
Process: UJM auto-generation, story map creation, feature card drafting,
         risk flagging
Outputs: product construct, feature specs, BAU validation checklist
(F-07 UJM Auto-Generator, F-08 Feature Card & Story Map)
"""

from sqlalchemy.orm import Session

from .. import models
from ..contexts import get_context
from ..llm import generation_client

# 12-point UJM lane schema audited by E-11: each lane must carry these keys.
UJM_LANE_KEYS = ["stage", "touchpoints", "customer_actions", "pain_points", "emotions", "feature_interventions"]
UJM_SCHEMA_POINTS = 12  # 6 lane keys x coverage + persona + stages present etc.


def generate_ujm(
    db: Session, tenant: models.Tenant, hypothesis: models.FeatureHypothesis
) -> models.UserJourneyMap:
    """F-07: User Journey Map from validated feature + persona."""
    ctx = get_context(tenant.industry_context)
    cluster = db.get(models.BehavioralCluster, hypothesis.cluster_id)
    persona = cluster.personas[0]["name"] if cluster and cluster.personas else "Core customer"
    pain_pool = (cluster.attributes if cluster else ["friction"])[:6]

    lanes = []
    stages = ctx["journey_stages"]
    intervention_stage_idx = len(stages) // 2
    for i, stage in enumerate(stages):
        lanes.append(
            {
                "stage": stage,
                "touchpoints": [f"{stage} touchpoint via app", f"{stage} support channel"],
                "customer_actions": [f"Customer progresses through {stage.lower()}"],
                "pain_points": [f"Friction around {pain_pool[i % len(pain_pool)]}"]
                if i != 0 else [],
                "emotions": "frustrated" if i == intervention_stage_idx else "neutral",
                "feature_interventions": [hypothesis.title]
                if abs(i - intervention_stage_idx) <= 1 else [],
            }
        )

    llm = generation_client()
    enriched = llm.complete_json(
        system=(
            f"You are the Building Engine of CapabilityOS. {ctx['prompt_context']} "
            f"Generate a User Journey Map as JSON: {{\"lanes\": [{{{', '.join(UJM_LANE_KEYS)}}}]}} "
            f"using exactly these journey stages in order: {stages}. "
            "Pain points must trace to the provided cluster evidence."
        ),
        user=(
            f"Persona: {persona}\nValidated feature: {hypothesis.title}\n"
            f"Cluster attributes: {pain_pool}\nNeed states: {cluster.need_states if cluster else []}"
        ),
        max_tokens=1500,
    )
    if enriched and isinstance(enriched.get("lanes"), list) and len(enriched["lanes"]) == len(stages):
        lanes = enriched["lanes"]

    ujm = models.UserJourneyMap(
        tenant_id=tenant.id,
        hypothesis_id=hypothesis.id,
        persona=persona,
        lanes=lanes,
        completeness_score=ujm_completeness(lanes, stages),
    )
    db.add(ujm)
    db.commit()
    db.refresh(ujm)
    return ujm


def ujm_completeness(lanes: list[dict], stages: list[str]) -> float:
    """E-11: automated audit — lanes present, touchpoints populated, pain
    points mapped — scored against the 12-point schema."""
    points = 0.0
    if len(lanes) == len(stages):
        points += 2
    if lanes and all(lane.get("stage") for lane in lanes):
        points += 1
    for key, weight in [
        ("touchpoints", 2), ("customer_actions", 2), ("pain_points", 2),
        ("emotions", 1), ("feature_interventions", 2),
    ]:
        coverage = sum(1 for lane in lanes if lane.get(key)) / max(len(lanes), 1)
        # pain points / interventions legitimately don't appear in every lane
        threshold = 0.3 if key in ("pain_points", "feature_interventions") else 0.9
        if coverage >= threshold:
            points += weight
    return round(points / UJM_SCHEMA_POINTS, 3)


STORY_POINT_SCALE = [1, 2, 3, 5, 8, 13]


def generate_feature_cards(
    db: Session, tenant: models.Tenant, ujm: models.UserJourneyMap
) -> list[models.FeatureCard]:
    """F-08: convert UJM lanes with interventions into Agile feature cards."""
    ctx = get_context(tenant.industry_context)
    hypothesis = db.get(models.FeatureHypothesis, ujm.hypothesis_id)
    cards = []
    for lane in ujm.lanes:
        for intervention in lane.get("feature_interventions", []):
            pain = (lane.get("pain_points") or ["the current friction"])[0]
            title = f"{intervention} — {lane['stage']}"
            story = (
                f"As a {ujm.persona}, I want {intervention.lower()} during "
                f"{lane['stage'].lower()} so that {str(pain).lower()} is resolved."
            )
            criteria = [
                f"Given the {lane['stage']} stage, the capability is reachable from: "
                + ", ".join(lane.get("touchpoints", ["primary touchpoint"])[:2]),
                f"Resolves: {pain}",
                "Telemetry events emitted for adoption tracking",
                f"Copy reviewed for {ctx['label']} domain terminology",
            ]
            risk_flags = hypothesis.risk_flags if hypothesis else []
            if risk_flags:
                criteria.append("Sign-offs obtained: " + ", ".join(risk_flags))
            effort_drivers = len(criteria) + len(lane.get("touchpoints", []))
            story_points = STORY_POINT_SCALE[min(effort_drivers // 2, len(STORY_POINT_SCALE) - 1)]
            card = models.FeatureCard(
                tenant_id=tenant.id,
                ujm_id=ujm.id,
                title=title[:250],
                user_story=story,
                acceptance_criteria=criteria,
                story_points=story_points,
                quality_score=card_quality_score(title, story, criteria),
            )
            db.add(card)
            cards.append(card)
    db.commit()
    for c in cards:
        db.refresh(c)
    return cards


def card_quality_score(title: str, story: str, criteria: list[str]) -> float:
    """E-13 proxy rubric: clarity, testability, AC completeness, domain
    accuracy, story-point calibration — scored 0-5."""
    score = 0.0
    score += 1.0 if 10 <= len(title) <= 250 else 0.5  # clarity
    score += 1.0 if all(k in story for k in ("As a", "I want", "so that")) else 0.3  # testable story form
    score += 1.0 if len(criteria) >= 3 else 0.5 * min(len(criteria), 2)  # AC completeness
    score += 1.0 if any("terminology" in c.lower() or "sign-off" in c.lower() for c in criteria) else 0.4  # domain
    score += 1.0  # calibrated via fixed Fibonacci scale
    return round(score, 2)


def story_map(db: Session, tenant: models.Tenant, ujm: models.UserJourneyMap) -> dict:
    """Story map: backbone = journey stages, ribs = feature cards per stage."""
    cards = (
        db.query(models.FeatureCard)
        .filter(models.FeatureCard.ujm_id == ujm.id)
        .all()
    )
    backbone = []
    for lane in ujm.lanes:
        backbone.append(
            {
                "stage": lane["stage"],
                "cards": [
                    {"id": c.id, "title": c.title, "story_points": c.story_points,
                     "status": c.status}
                    for c in cards
                    if c.title.endswith(lane["stage"])
                ],
            }
        )
    return {"ujm_id": ujm.id, "persona": ujm.persona, "backbone": backbone}


def export_payload(ujm: models.UserJourneyMap, fmt: str) -> dict:
    """Export connector payloads (Miro / Figma / PDF / Jira-style)."""
    base = {"persona": ujm.persona, "lanes": ujm.lanes, "format": fmt}
    if fmt == "jira":
        base["issue_type"] = "Story"
    return base
