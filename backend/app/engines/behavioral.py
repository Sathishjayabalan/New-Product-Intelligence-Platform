"""Engine 2: Behavioral Engine.

Inputs:  signal feed from Listening Engine, historical product data
Process: segmentation, behavioral cluster generation, need-state mapping,
         feature hypothesis ranking
Outputs: persona cards, need-state maps, hypotheses with confidence scores
(F-03 Behavioral Cluster Engine, F-04 Feature Hypothesis Generator)
"""

import random

from sqlalchemy.orm import Session

from .. import models
from ..contexts import get_context
from ..llm import generation_client
from ..textutils import jaccard, token_set, top_terms

CLUSTER_SIMILARITY_THRESHOLD = 0.18

NEED_STATE_TEMPLATES = {
    "friction": "Reduce effort and waiting in the {topic} experience",
    "trust": "Build confidence and transparency around {topic}",
    "control": "Give customers visibility and control over {topic}",
    "value": "Demonstrate clear value-for-money in {topic}",
    "guidance": "Provide proactive guidance through the {topic} journey",
}

RISK_FLAG_RULES = {
    "insurance": {
        "claims_friction": ["regulatory_review_required"],
        "underwriting": ["actuarial_signoff_required", "regulatory_review_required"],
        "pricing_sensitivity": ["actuarial_signoff_required"],
    },
    "banking": {
        "credit_friction": ["compliance_review_required"],
        "fraud_risk": ["compliance_review_required", "model_risk_review"],
        "onboarding_dropoff": ["kyc_compliance_check"],
    },
    "healthcare": {
        "patient_engagement": ["phi_privacy_review"],
        "clinical_outcome": ["clinical_validation_required", "phi_privacy_review"],
        "adherence": ["clinical_validation_required"],
    },
}


def _greedy_cluster(card_tokens: list[tuple[int, set[str], str]]) -> list[list[int]]:
    """Greedy agglomerative grouping by category match + token similarity.

    Input is canonically ordered (category, then id) so the algorithm is
    deterministic regardless of ingestion order. Returns lists of
    insight-card ids.
    """
    ordered = sorted(card_tokens, key=lambda t: (t[2], t[0]))
    clusters: list[dict] = []
    for card_id, tokens, category in ordered:
        best, best_sim = None, 0.0
        for cl in clusters:
            sim = jaccard(tokens, cl["tokens"])
            if category == cl["category"]:
                sim += 0.25
            if sim > best_sim:
                best, best_sim = cl, sim
        if best is not None and best_sim >= CLUSTER_SIMILARITY_THRESHOLD:
            best["ids"].append(card_id)
            best["tokens"] |= tokens
        else:
            clusters.append({"ids": [card_id], "tokens": set(tokens), "category": category})
    return [cl["ids"] for cl in clusters]


def _co_membership_pairs(groups: list[list[int]]) -> set[frozenset[int]]:
    pairs = set()
    for g in groups:
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                pairs.add(frozenset((a, b)))
    return pairs


def cluster_stability(card_tokens: list[tuple[int, set[str], str]], runs: int = 3) -> float:
    """E-04 (reproducibility): re-cluster under leave-one-out perturbation
    and measure co-membership Jaccard against the full run, restricted to
    the shared cards. Robust clusters keep the same groupings when any
    single signal is removed."""
    if len(card_tokens) < 3:
        return 1.0
    rng = random.Random(42)
    base_pairs = _co_membership_pairs(_greedy_cluster(card_tokens))
    sims = []
    for _ in range(runs):
        dropped = rng.randrange(len(card_tokens))
        subset = [t for i, t in enumerate(card_tokens) if i != dropped]
        dropped_id = card_tokens[dropped][0]
        sub_pairs = _co_membership_pairs(_greedy_cluster(subset))
        base_shared = {p for p in base_pairs if dropped_id not in p}
        union = base_shared | sub_pairs
        sims.append(len(base_shared & sub_pairs) / len(union) if union else 1.0)
    return round(sum(sims) / len(sims), 3)


def _need_states(category: str, terms: list[str]) -> list[str]:
    topic = category.replace("_", " ")
    keys = list(NEED_STATE_TEMPLATES)
    picked = keys[: 3]
    if any(t in {"trust", "fraud", "scam", "denied"} for t in terms):
        picked = ["trust", "control", "guidance"]
    return [NEED_STATE_TEMPLATES[k].format(topic=topic) for k in picked]


def _personas(ctx: dict, category: str, terms: list[str]) -> list[dict]:
    archetype = ctx["persona_archetypes"][hash(category) % len(ctx["persona_archetypes"])]
    return [
        {
            "name": archetype,
            "traits": terms[:4],
            "dominant_need": category.replace("_", " "),
        }
    ]


def run_clustering(db: Session, tenant: models.Tenant, pipeline_run_id: int | None = None) -> list[models.BehavioralCluster]:
    """F-03: generate named clusters with need states, attributes, personas."""
    ctx = get_context(tenant.industry_context)
    cards = (
        db.query(models.InsightCard)
        .filter(models.InsightCard.tenant_id == tenant.id)
        .all()
    )
    if not cards:
        return []

    card_tokens = [
        (c.id, token_set(c.summary + " " + c.grounded_quote), c.category) for c in cards
    ]
    groups = _greedy_cluster(card_tokens)
    stability = cluster_stability(card_tokens)
    by_id = {c.id: c for c in cards}

    # Replace previous clusters for a fresh segmentation run
    db.query(models.FeatureHypothesis).filter(
        models.FeatureHypothesis.tenant_id == tenant.id
    ).delete()
    db.query(models.BehavioralCluster).filter(
        models.BehavioralCluster.tenant_id == tenant.id
    ).delete()

    clusters = []
    for ids in groups:
        members = [by_id[i] for i in ids]
        dominant = max(
            {m.category for m in members},
            key=lambda cat: sum(1 for m in members if m.category == cat),
        )
        terms = top_terms([m.summary for m in members], n=6)
        name = f"{dominant.replace('_', ' ').title()} — {', '.join(terms[:2]) or 'core'}"
        cluster = models.BehavioralCluster(
            tenant_id=tenant.id,
            pipeline_run_id=pipeline_run_id,
            name=name,
            need_states=_need_states(dominant, terms),
            attributes=terms,
            personas=_personas(ctx, dominant, terms),
            signal_ids=[m.signal_id for m in members],
            size=len(members),
            stability_score=stability,
        )
        db.add(cluster)
        clusters.append(cluster)
    db.commit()
    for c in clusters:
        db.refresh(c)
    return clusters


def _confidence(cluster: models.BehavioralCluster, rank: int, mean_strength: float) -> float:
    size_factor = min(cluster.size / 6, 1.0)
    raw = 0.45 * size_factor + 0.35 * mean_strength + 0.2 * cluster.stability_score
    return round(max(0.05, min(0.97, raw - 0.08 * rank)), 3)


def generate_hypotheses(
    db: Session, tenant: models.Tenant, top_n: int = 3
) -> list[models.FeatureHypothesis]:
    """F-04: ranked feature hypotheses with confidence, rationale, risk flags."""
    ctx = get_context(tenant.industry_context)
    clusters = (
        db.query(models.BehavioralCluster)
        .filter(models.BehavioralCluster.tenant_id == tenant.id)
        .order_by(models.BehavioralCluster.size.desc())
        .all()
    )
    llm = generation_client()
    results = []
    for cluster in clusters:
        cards = (
            db.query(models.InsightCard)
            .filter(models.InsightCard.signal_id.in_(cluster.signal_ids or [0]))
            .all()
        )
        mean_strength = (
            sum(c.strength_score for c in cards) / len(cards) if cards else 0.4
        )
        risk_flags = RISK_FLAG_RULES.get(tenant.industry_context, {}).get(
            cards[0].category if cards else "", []
        )

        proposals = None
        if llm.available:
            proposals = llm.complete_json(
                system=(
                    f"You are the Behavioral Engine of CapabilityOS. {ctx['prompt_context']} "
                    f"Given a behavioral cluster, propose the top {top_n} product feature "
                    "hypotheses as JSON: {\"hypotheses\": [{\"title\", \"description\", \"rationale\"}]}. "
                    "Every rationale must reference the cluster's need states."
                ),
                user=(
                    f"Cluster: {cluster.name}\nNeed states: {cluster.need_states}\n"
                    f"Attributes: {cluster.attributes}\nPersonas: {cluster.personas}\n"
                    f"Evidence: {[c.grounded_quote[:120] for c in cards[:5]]}"
                ),
                max_tokens=900,
            )

        if proposals and isinstance(proposals.get("hypotheses"), list):
            items = proposals["hypotheses"][:top_n]
        else:
            items = [
                {
                    "title": f"Address '{need}' for {cluster.personas[0]['name'] if cluster.personas else 'core segment'}",
                    "description": (
                        f"Capability targeting the need state '{need}' surfaced by "
                        f"{cluster.size} signal(s) in cluster '{cluster.name}'."
                    ),
                    "rationale": (
                        f"Derived from need state '{need}'; cluster attributes: "
                        f"{', '.join(cluster.attributes[:4])}."
                    ),
                }
                for need in cluster.need_states[:top_n]
            ]

        for rank, item in enumerate(items):
            hyp = models.FeatureHypothesis(
                tenant_id=tenant.id,
                cluster_id=cluster.id,
                rank=rank + 1,
                title=item.get("title", "Untitled hypothesis")[:250],
                description=item.get("description", ""),
                confidence=_confidence(cluster, rank, mean_strength),
                rationale=item.get("rationale", ""),
                risk_flags=risk_flags,
            )
            db.add(hyp)
            results.append(hyp)
    db.commit()
    for h in results:
        db.refresh(h)
    return results
