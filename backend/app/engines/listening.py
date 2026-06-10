"""Engine 1: Listening Engine.

Inputs:  3rd-party data feeds, conference insights, CRM exports, VoC streams
Process: signal classification, deduplication, trend extraction, clustering
Outputs: unified signal feed, tagged insight cards, data quality scores
(F-01 Signal Ingestion Hub, F-02 AI Insight Cards)
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import models
from ..contexts import get_context
from ..llm import generation_client
from ..textutils import content_hash, jaccard, token_set, tokenize

SUPPORTED_SOURCE_TYPES = {
    "api", "file", "crm", "erp", "stream", "voc", "conference",
    "survey", "support_ticket", "social", "market_feed", "webhook",
}

NEAR_DUP_THRESHOLD = 0.82

# Curated keyword boosts on top of the taxonomy-token match, covering the
# highest-signal categories per industry context.
CATEGORY_KEYWORD_BOOSTS: dict[str, list[str]] = {
    "claims_friction": ["claim", "claims", "settlement", "delay", "reimbursement", "denied"],
    "coverage_gap": ["coverage", "uninsured", "exclusion", "rider", "gap"],
    "pricing_sensitivity": ["price", "premium", "expensive", "discount", "cost", "cheaper"],
    "renewal_churn": ["renewal", "renew", "lapsed", "cancel", "switch", "churn"],
    "credit_friction": ["credit", "loan", "rejected", "limit", "score", "approval"],
    "savings_behavior": ["savings", "save", "deposit", "goal", "round-up", "interest"],
    "payments_ux": ["payment", "transfer", "upi", "failed", "checkout", "wallet"],
    "fraud_risk": ["fraud", "scam", "unauthorised", "unauthorized", "phishing", "suspicious"],
    "onboarding_dropoff": ["onboarding", "kyc", "signup", "drop", "abandoned", "verification"],
    "cart_abandonment": ["cart", "abandoned", "checkout", "basket", "left"],
    "loyalty": ["loyalty", "points", "rewards", "tier", "member"],
    "delivery_experience": ["delivery", "shipping", "late", "courier", "package"],
    "returns_friction": ["return", "refund", "exchange", "pickup"],
    "patient_engagement": ["patient", "engagement", "app", "portal", "reminder"],
    "care_gap": ["care", "follow-up", "missed", "gap", "screening"],
    "adherence": ["medication", "adherence", "dose", "refill", "missed"],
    "appointment_friction": ["appointment", "booking", "wait", "slot", "reschedule"],
    "engagement_dropoff": ["engagement", "streak", "inactive", "drop", "churn", "stopped"],
    "learning_outcome": ["score", "outcome", "mastery", "progress", "improvement"],
    "completion": ["completion", "finish", "certificate", "drop-out"],
    "eta_accuracy": ["eta", "late", "delay", "arrival", "estimate"],
    "routing": ["route", "routing", "detour", "optimization", "traffic"],
    "carrier_performance": ["carrier", "driver", "performance", "sla", "on-time"],
    "visibility": ["tracking", "visibility", "status", "update", "where"],
}

SOURCE_WEIGHT = {
    "crm": 0.9, "voc": 1.0, "survey": 0.95, "support_ticket": 0.9,
    "api": 0.8, "stream": 0.85, "conference": 0.7, "social": 0.65,
    "market_feed": 0.75, "file": 0.7, "erp": 0.8, "webhook": 0.8,
}


def classify_signal(content: str, industry: str) -> tuple[str, float]:
    """Score signal against the industry taxonomy. Returns (category, score)."""
    ctx = get_context(industry)
    tokens = set(tokenize(content))
    best_cat, best_score = "general", 0.0
    for category in ctx["signal_taxonomy"]:
        cat_tokens = set(category.split("_"))
        boosts = set(CATEGORY_KEYWORD_BOOSTS.get(category, []))
        # Curated keywords outweigh taxonomy-name tokens; never double-count
        # a term that appears in both.
        hits = len(tokens & boosts) * 2 + len(tokens & (cat_tokens - boosts))
        score = hits / max(len(tokens) ** 0.5, 1)
        if hits > 0 and score > best_score:
            best_cat, best_score = category, score
    return best_cat, round(min(best_score, 1.0), 3)


def detect_duplicate(db: Session, tenant_id: int, content: str) -> tuple[bool, str]:
    """Hash-based exact dedup plus semantic near-dup (PRD E-02)."""
    h = content_hash(content)
    exact = (
        db.query(models.Signal)
        .filter(models.Signal.tenant_id == tenant_id, models.Signal.dedup_hash == h)
        .first()
    )
    if exact:
        return True, h
    incoming = token_set(content)
    recent = (
        db.query(models.Signal)
        .filter(models.Signal.tenant_id == tenant_id, models.Signal.is_duplicate.is_(False))
        .order_by(models.Signal.id.desc())
        .limit(500)
        .all()
    )
    for sig in recent:
        if jaccard(incoming, token_set(sig.content)) >= NEAR_DUP_THRESHOLD:
            return True, h
    return False, h


def quality_score(content: str, source_type: str, category_score: float) -> float:
    length_factor = min(len(tokenize(content)) / 25, 1.0)
    source_factor = SOURCE_WEIGHT.get(source_type, 0.6)
    return round(0.4 * length_factor + 0.35 * source_factor + 0.25 * category_score, 3)


def trend_vector(db: Session, tenant_id: int, category: str) -> str:
    """Trend extraction: compare recent vs older signal volume per category."""
    cards = (
        db.query(models.InsightCard)
        .filter(
            models.InsightCard.tenant_id == tenant_id,
            models.InsightCard.category == category,
        )
        .order_by(models.InsightCard.id.desc())
        .limit(20)
        .all()
    )
    if len(cards) >= 6:
        return "rising"
    if len(cards) >= 2:
        return "flat"
    return "emerging"


def ingest_signal(
    db: Session,
    tenant: models.Tenant,
    *,
    content: str,
    source_type: str,
    source_name: str = "",
    tags: list[str] | None = None,
    source_timestamp: datetime | None = None,
) -> models.Signal:
    """F-01: multi-source ingestion with auto-tagging and dedup."""
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(
            f"Unsupported source_type '{source_type}'. "
            f"Supported: {sorted(SUPPORTED_SOURCE_TYPES)}"
        )
    is_dup, h = detect_duplicate(db, tenant.id, content)
    category, cat_score = classify_signal(content, tenant.industry_context)
    auto_tags = sorted(set((tags or []) + [category, source_type]))
    signal = models.Signal(
        tenant_id=tenant.id,
        source_type=source_type,
        source_name=source_name,
        content=content,
        tags=auto_tags,
        dedup_hash=h,
        is_duplicate=is_dup,
        quality_score=quality_score(content, source_type, cat_score),
        status="duplicate" if is_dup else "ingested",
        source_timestamp=source_timestamp or datetime.now(timezone.utc),
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    if not is_dup:
        generate_insight_card(db, tenant, signal, category, cat_score)
    return signal


def generate_insight_card(
    db: Session,
    tenant: models.Tenant,
    signal: models.Signal,
    category: str,
    cat_score: float,
) -> models.InsightCard:
    """F-02: structured insight card — source, strength, trend, engine route.

    Every card carries a grounded quote from the source signal so downstream
    claims stay fact-traceable (hallucination control, E-16).
    """
    ctx = get_context(tenant.industry_context)
    strength = round(0.6 * signal.quality_score + 0.4 * cat_score, 3)
    quote = signal.content[:280]
    title = f"{category.replace('_', ' ').title()} signal from {signal.source_name or signal.source_type}"
    summary = (
        f"{ctx['label']} signal classified as '{category}'. "
        f"Evidence: \"{quote[:140]}\""
    )

    llm = generation_client()
    enriched = llm.complete_json(
        system=(
            f"You are the Listening Engine of CapabilityOS. {ctx['prompt_context']} "
            "Generate an insight card as JSON with keys: title, summary. "
            "The summary MUST quote the source signal verbatim at least once."
        ),
        user=f"Signal ({signal.source_type}): {signal.content}\nCategory: {category}",
        max_tokens=400,
    )
    if enriched and quote[:60].lower() in enriched.get("summary", "").lower():
        title = enriched.get("title", title)[:250]
        summary = enriched["summary"]

    card = models.InsightCard(
        tenant_id=tenant.id,
        signal_id=signal.id,
        title=title,
        category=category,
        strength_score=strength,
        trend_vector=trend_vector(db, tenant.id, category),
        engine_route="behavioral",
        summary=summary,
        grounded_quote=quote,
    )
    db.add(card)
    signal.status = "processed"
    db.commit()
    db.refresh(card)
    return card
