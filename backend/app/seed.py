"""Seed demo tenants and signals for the two MVP industry contexts
(Insurance + Banking, per PRD Roadmap 'MVP / Pilot Ready').

Run:  python -m app.seed
"""

from .database import SessionLocal, init_db
from .engines.listening import ingest_signal
from .models import Tenant, User
from .pipeline import run_pipeline

INSURANCE_SIGNALS = [
    ("voc", "NPS verbatims", "My claim settlement took 7 weeks and nobody called me back. I had to chase the agent for every update on my reimbursement."),
    ("voc", "NPS verbatims", "Claim was denied because of an exclusion I was never told about when I bought the policy."),
    ("support_ticket", "Zendesk", "Customer reports the claims portal keeps rejecting uploaded hospital bills, settlement delayed again."),
    ("crm", "Salesforce export", "Policyholder called to cancel renewal — said a competitor quoted a 30% cheaper premium for identical coverage."),
    ("survey", "Renewal survey Q2", "I would renew if the premium did not jump every year without explanation. Pricing feels arbitrary."),
    ("api", "Aggregator feed", "Market data: comparison-site traffic for motor insurance quotes up 42% quarter over quarter; price sensitivity rising."),
    ("voc", "App reviews", "The app makes it impossible to know what my coverage actually includes. Found out flood damage was excluded after the claim."),
    ("support_ticket", "Zendesk", "Customer confused about rider options during purchase, abandoned the quote midway."),
    ("conference", "InsurTech Connect notes", "Panel insight: carriers winning on claims experience see 2x retention; instant settlement is the new table stakes."),
    ("stream", "Claims telemetry", "Spike detected: 18% of claims this week stuck in document-verification state for more than 5 days."),
]

BANKING_SIGNALS = [
    ("voc", "App store reviews", "My loan application was rejected even though my credit score is 780. No explanation, no appeal path."),
    ("support_ticket", "Freshdesk", "UPI payment failed three times during checkout but the amount was debited. Refund took 4 days."),
    ("crm", "CRM export", "SME customer asked for a working-capital credit line with flexible repayment tied to cash flow."),
    ("voc", "In-app feedback", "I gave up during KYC — the video verification kept failing and there was no way to resume later."),
    ("survey", "Savings study", "I want the app to round up my purchases and auto-save the difference towards a travel goal."),
    ("stream", "Fraud telemetry", "Unusual pattern: 3x increase in reported unauthorised transactions originating from phishing links this month."),
    ("api", "Open banking feed", "Aggregator data shows 28% of new users abandon onboarding at the document-upload step."),
    ("voc", "NPS verbatims", "Hidden fees on international transfers — the quoted rate is never what I actually pay."),
]


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        for slug, name, industry, signals in [
            ("demo", "Demo Tenant", "insurance", INSURANCE_SIGNALS),
            ("acme-insurance", "Acme General Insurance", "insurance", INSURANCE_SIGNALS),
            ("nova-bank", "Nova Bank", "banking", BANKING_SIGNALS),
        ]:
            tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
            if not tenant:
                tenant = Tenant(slug=slug, name=name, industry_context=industry)
                db.add(tenant)
                db.commit()
                db.refresh(tenant)
                db.add(User(tenant_id=tenant.id, email=f"coe-lead@{slug}.example",
                            name="CoE Lead", role="coe_lead"))
                db.commit()
            ingested = 0
            for source_type, source_name, content in signals:
                sig = ingest_signal(
                    db, tenant, content=content, source_type=source_type,
                    source_name=source_name,
                )
                ingested += 0 if sig.is_duplicate else 1
            print(f"[{slug}] ingested {ingested} signals")
            run = run_pipeline(db, tenant)
            print(f"[{slug}] pipeline run #{run.id}: {run.status} "
                  f"({run.latency_seconds}s){' — ' + run.gate_reason if run.gated else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
