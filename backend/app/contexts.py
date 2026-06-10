"""Industry Context Switcher (F-09) — Domain Context Layer.

Each context carries the domain schema, signal taxonomy, NS19 role labels,
journey stages, terminology dictionary, and an AI prompt context that gets
injected at the prompt layer (PRD 7.1 'Domain context injection').
"""

INDUSTRY_CONTEXTS: dict[str, dict] = {
    "insurance": {
        "label": "Insurance",
        "priority": "primary",
        "signal_taxonomy": [
            "claims_friction", "coverage_gap", "pricing_sensitivity",
            "distribution_channel", "regulatory", "customer_service",
            "underwriting", "renewal_churn",
        ],
        "role_labels": {
            "data_partnership_leader": "Data Partnership Leader",
            "behavioral_scientist": "Behavioral Scientist",
            "product_owner": "Product Owner",
            "domain_expert": "Underwriting / Actuarial Expert",
        },
        "journey_stages": [
            "Awareness", "Quote & Compare", "Purchase / Bind",
            "Onboarding", "Policy Servicing", "Claim", "Renewal",
        ],
        "terminology": [
            "premium", "claim", "underwriting", "policyholder", "coverage",
            "actuarial", "deductible", "rider", "endorsement", "bind",
        ],
        "prompt_context": (
            "You operate in the insurance domain. Use correct insurance "
            "terminology (premium, underwriting, claims, riders). Product "
            "constructs must be regulatory-aware and actuarially sound."
        ),
        "persona_archetypes": [
            "Digital-first policyholder", "Claims-burned skeptic",
            "Price-sensitive switcher", "Under-insured small business owner",
        ],
    },
    "banking": {
        "label": "Banking & Fintech",
        "priority": "primary",
        "signal_taxonomy": [
            "credit_friction", "savings_behavior", "payments_ux", "fraud_risk",
            "onboarding_dropoff", "fees_transparency", "lending", "embedded_finance",
        ],
        "role_labels": {
            "data_partnership_leader": "Data Partnership Leader",
            "behavioral_scientist": "Behavioral Scientist",
            "product_owner": "Digital Product Owner",
            "domain_expert": "Risk / Compliance Expert",
        },
        "journey_stages": [
            "Discovery", "KYC & Onboarding", "First Transaction",
            "Habitual Use", "Cross-sell", "Support", "Retention",
        ],
        "terminology": [
            "KYC", "credit line", "APR", "nudge", "overdraft", "settlement",
            "chargeback", "AML", "open banking", "interchange",
        ],
        "prompt_context": (
            "You operate in the banking and fintech domain. Behavioral science "
            "is core to financial product design — frame features as nudges, "
            "credit products, and fraud-detection capabilities. Respect KYC/AML."
        ),
        "persona_archetypes": [
            "Gen-Z neobank native", "Credit-invisible first-borrower",
            "SME cash-flow juggler", "Security-anxious saver",
        ],
    },
    "retail": {
        "label": "Retail & E-commerce",
        "priority": "high",
        "signal_taxonomy": [
            "cart_abandonment", "loyalty", "seasonal_demand", "delivery_experience",
            "returns_friction", "personalisation", "pricing_promo", "discovery",
        ],
        "role_labels": {
            "data_partnership_leader": "Data Partnership Leader",
            "behavioral_scientist": "Customer Insights Scientist",
            "product_owner": "VP Product / CX Owner",
            "domain_expert": "Merchandising Expert",
        },
        "journey_stages": [
            "Discovery", "Browse & Compare", "Cart", "Checkout",
            "Fulfilment", "Post-purchase", "Loyalty",
        ],
        "terminology": [
            "SKU", "basket size", "AOV", "conversion", "abandonment",
            "loyalty tier", "omnichannel", "assortment", "markdown", "RFM",
        ],
        "prompt_context": (
            "You operate in retail and e-commerce. Translate real-time "
            "behavioral data (cart abandonment, loyalty signals, seasonal "
            "demand) into rapidly shippable product features."
        ),
        "persona_archetypes": [
            "Deal-hunting cart abandoner", "Loyalty maximiser",
            "Omnichannel browser", "Last-minute gifter",
        ],
    },
    "healthcare": {
        "label": "Healthcare & HealthTech",
        "priority": "high",
        "signal_taxonomy": [
            "patient_engagement", "care_gap", "clinical_outcome", "payer_signal",
            "adherence", "appointment_friction", "telehealth", "ehr_workflow",
        ],
        "role_labels": {
            "data_partnership_leader": "Health Data Partnership Lead",
            "behavioral_scientist": "Behavioral Health Scientist",
            "product_owner": "Digital Health Product Owner",
            "domain_expert": "Clinical Expert",
        },
        "journey_stages": [
            "Symptom Awareness", "Triage", "Appointment", "Consultation",
            "Treatment & Adherence", "Follow-up", "Ongoing Care",
        ],
        "terminology": [
            "EHR", "care pathway", "adherence", "payer", "triage",
            "outcomes", "telehealth", "care gap", "clinical protocol", "PHI",
        ],
        "prompt_context": (
            "You operate in healthcare. EHR data, patient feedback, clinical "
            "outcomes and payer signals must all feed product decisions. "
            "Respect PHI/HIPAA constraints in every output."
        ),
        "persona_archetypes": [
            "Chronic-condition self-manager", "Caregiver coordinator",
            "Engagement drop-off patient", "Telehealth-first millennial",
        ],
    },
    "edtech": {
        "label": "EdTech",
        "priority": "medium",
        "signal_taxonomy": [
            "engagement_dropoff", "learning_outcome", "content_relevance",
            "completion", "assessment", "pricing_access", "gamification", "community",
        ],
        "role_labels": {
            "data_partnership_leader": "Learning Data Lead",
            "behavioral_scientist": "Learning Scientist",
            "product_owner": "Head of Learning Experience",
            "domain_expert": "Curriculum Expert",
        },
        "journey_stages": [
            "Discovery", "Enrolment", "First Session", "Habit Formation",
            "Assessment", "Completion", "Advocacy",
        ],
        "terminology": [
            "learner", "cohort", "adaptive learning", "completion rate",
            "streak", "mastery", "curriculum", "spaced repetition", "LMS", "MOOC",
        ],
        "prompt_context": (
            "You operate in EdTech. Translate learner behavioral data into "
            "adaptive product features; differentiate on personalisation."
        ),
        "persona_archetypes": [
            "Streak-driven mobile learner", "Career-switching upskiller",
            "Disengaged enrollee", "Exam-deadline crammer",
        ],
    },
    "logistics": {
        "label": "Logistics & Supply Chain",
        "priority": "medium",
        "signal_taxonomy": [
            "eta_accuracy", "routing", "carrier_performance", "port_weather",
            "telemetry", "exception_handling", "cost_per_mile", "visibility",
        ],
        "role_labels": {
            "data_partnership_leader": "Telemetry Partnership Lead",
            "behavioral_scientist": "Ops Data Scientist",
            "product_owner": "Logistics Product Owner",
            "domain_expert": "Operations Expert",
        },
        "journey_stages": [
            "Booking", "Pickup", "Line-haul", "Customs / Port",
            "Last Mile", "Delivery", "Exception & Claims",
        ],
        "terminology": [
            "ETA", "line-haul", "last mile", "carrier", "telemetry",
            "waybill", "demurrage", "freight", "consignment", "dwell time",
        ],
        "prompt_context": (
            "You operate in logistics and supply chain. Map ops signals "
            "(telemetry, port/weather feeds) to product features for routing "
            "engines, ETA platforms, and carrier portals."
        ),
        "persona_archetypes": [
            "Visibility-hungry shipper", "Exception-firefighting ops manager",
            "Margin-squeezed carrier", "API-first integrator",
        ],
    },
}

# NS19 roles for RBAC (F-11) mapped to the engines they may operate.
NS19_ROLES: dict[str, dict] = {
    "coe_lead": {"label": "CoE Lead", "engines": ["listening", "behavioral", "prototype", "building", "platform"]},
    "data_partnership_leader": {"label": "Data Partnership Leader", "engines": ["listening"]},
    "data_engineer": {"label": "Data Engineer", "engines": ["listening"]},
    "behavioral_scientist": {"label": "Behavioral Scientist", "engines": ["behavioral"]},
    "data_scientist": {"label": "Data Scientist", "engines": ["behavioral"]},
    "product_owner": {"label": "Product Owner", "engines": ["prototype", "building"]},
    "consumer_expert": {"label": "Consumer Expert", "engines": ["prototype"]},
    "design_thinker": {"label": "Design Thinker", "engines": ["building"]},
    "domain_expert": {"label": "Domain Expert (Underwriting / Actuary / Clinical)", "engines": ["building"]},
}


def get_context(industry: str) -> dict:
    if industry not in INDUSTRY_CONTEXTS:
        raise KeyError(f"Unknown industry context: {industry}")
    return INDUSTRY_CONTEXTS[industry]


def role_can_operate(role: str, engine: str) -> bool:
    return engine in NS19_ROLES.get(role, {}).get("engines", [])
