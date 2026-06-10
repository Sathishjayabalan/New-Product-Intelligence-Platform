"""End-to-end tests for the CapabilityOS NS19 pipeline, mapped to PRD
features F-01..F-12 and eval catalogue E-01..E-16."""

INSURANCE_SIGNALS = [
    ("voc", "My claim settlement took 7 weeks and nobody called me back about reimbursement."),
    ("voc", "Claim denied because of an exclusion I was never told about at purchase."),
    ("support_ticket", "Claims portal keeps rejecting uploaded hospital bills, settlement delayed."),
    ("crm", "Policyholder cancelling renewal, competitor quoted much cheaper premium."),
    ("survey", "Would renew if the premium did not jump every year without explanation."),
    ("api", "Comparison-site traffic for motor insurance quotes up 42 percent this quarter."),
]


def seed_signals(client, tenant="demo"):
    for source, content in INSURANCE_SIGNALS:
        r = client.post(
            "/api/v1/signals",
            json={"content": content, "source_type": source, "source_name": "test"},
            headers={"X-Tenant": tenant},
        )
        assert r.status_code == 201, r.text


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["app"] == "CapabilityOS"


def test_industry_contexts_f09(client):
    r = client.get("/api/v1/contexts")
    assert r.status_code == 200
    assert set(r.json()) == {"insurance", "banking", "retail", "healthcare", "edtech", "logistics"}
    # context switch
    r = client.put("/api/v1/tenants/me/context", json={"industry_context": "banking"})
    assert r.json()["industry_context"] == "banking"
    r = client.put("/api/v1/tenants/me/context", json={"industry_context": "insurance"})
    assert r.status_code == 200
    # invalid context rejected
    assert client.put("/api/v1/tenants/me/context", json={"industry_context": "mining"}).status_code == 422


def test_signal_ingestion_f01(client):
    seed_signals(client)
    r = client.get("/api/v1/signals")
    signals = r.json()
    assert len(signals) == len(INSURANCE_SIGNALS)
    assert all(s["quality_score"] > 0 for s in signals)
    # auto-tagging includes classified category + source type
    assert any("claims_friction" in s["tags"] for s in signals)
    # unsupported source rejected
    r = client.post("/api/v1/signals", json={"content": "hello world test", "source_type": "carrier_pigeon"})
    assert r.status_code == 422


def test_deduplication_e02(client):
    dup = {"content": INSURANCE_SIGNALS[0][1], "source_type": "voc"}
    r = client.post("/api/v1/signals", json=dup)
    assert r.json()["is_duplicate"] is True
    # duplicates excluded from the unified feed
    assert len(client.get("/api/v1/signals").json()) == len(INSURANCE_SIGNALS)


def test_insight_cards_f02_grounded_e16(client):
    cards = client.get("/api/v1/insight-cards").json()
    assert len(cards) == len(INSURANCE_SIGNALS)
    for card in cards:
        assert card["strength_score"] > 0
        assert card["grounded_quote"]  # fact-traceability requirement
        assert card["engine_route"] == "behavioral"


def test_full_pipeline_run_f10(client):
    r = client.post("/api/v1/pipeline/run", json={"auto_validate": True, "enforce_gates": True})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "completed", run.get("gate_reason")
    stages = [entry["stage"] for entry in run["stage_log"]]
    assert stages == ["listening", "behavioral", "prototype", "building"]

    summary = client.get("/api/v1/pipeline/summary").json()
    counts = summary["counts"]
    assert counts["clusters"] >= 1
    assert counts["hypotheses"] >= 3
    assert counts["validated_hypotheses"] >= 1
    assert counts["feature_cards"] >= 1
    assert summary["bottleneck"] is None


def test_clusters_f03(client):
    clusters = client.get("/api/v1/clusters").json()
    assert clusters
    for c in clusters:
        assert c["name"] and c["need_states"] and c["personas"]
        assert c["stability_score"] >= 0.75  # E-04 target


def test_hypotheses_f04(client):
    hyps = client.get("/api/v1/hypotheses").json()
    assert hyps
    for h in hyps:
        assert 0 < h["confidence"] <= 0.97
        assert h["rank"] >= 1 and h["rationale"]
    # insurance claims hypotheses carry regulatory risk flags
    assert any("regulatory_review_required" in h["risk_flags"] for h in hyps)


def test_briefs_f05_e08(client):
    briefs = client.get("/api/v1/briefs").json()
    assert briefs
    for b in briefs:
        assert b["completeness_score"] >= 0.9  # E-08 target
        assert set(b["fields"]) >= {"objective", "test_method", "sample_size", "owner_role"}
    # PO edit telemetry (E-09)
    brief = briefs[0]
    r = client.patch(f"/api/v1/briefs/{brief['id']}", json={"fields": brief["fields"], "fields_edited": 2})
    assert r.json()["fields_edited"] == 2


def test_surveys_f06_e10(client):
    surveys = client.get("/api/v1/surveys").json()
    assert surveys
    s = surveys[0]
    assert s["min_sample_size"] > 100  # power-derived, not arbitrary
    assert len(s["questions"]) >= 4
    r = client.post(f"/api/v1/surveys/{s['id']}/responses", json={"count": s["min_sample_size"]})
    assert r.json()["statistically_significant"] is True


def test_ujm_f07_and_gate(client):
    ujms = client.get("/api/v1/ujms").json()
    assert ujms
    for u in ujms:
        assert u["completeness_score"] >= 0.85  # E-11 target
        assert len(u["lanes"]) == 7  # insurance journey stages
    # NS19 gate: cannot build from a non-validated hypothesis
    rejected = [h for h in client.get("/api/v1/hypotheses").json() if h["status"] != "validated"]
    if rejected:
        r = client.post(f"/api/v1/ujms/generate/{rejected[0]['id']}")
        assert r.status_code == 409
    # expert review feeds E-12
    r = client.post(f"/api/v1/ujms/{ujms[0]['id']}/review", json={"accepted": True})
    assert r.json()["status"] == "accepted"


def test_feature_cards_f08(client):
    cards = client.get("/api/v1/feature-cards").json()
    assert cards
    for c in cards:
        assert "As a" in c["user_story"] and "so that" in c["user_story"]
        assert len(c["acceptance_criteria"]) >= 3
        assert c["quality_score"] >= 4.0  # E-13 target
    # story map export
    ujm_id = client.get("/api/v1/ujms").json()[0]["id"]
    sm = client.get(f"/api/v1/story-map/{ujm_id}").json()
    assert any(stage["cards"] for stage in sm["backbone"])
    # connector payloads
    assert client.get(f"/api/v1/ujms/{ujm_id}/export/jira").json()["issue_type"] == "Story"
    assert client.get(f"/api/v1/ujms/{ujm_id}/export/xlsx").status_code == 422


def test_eval_catalogue_f12(client):
    catalogue = client.get("/api/v1/evals/catalogue").json()
    assert len(catalogue) == 16
    dash = client.get("/api/v1/evals/dashboard").json()
    gated = [e for e, spec in dash.items() if spec["gate"]]
    assert {"E-01", "E-02", "E-04", "E-08", "E-10", "E-11", "E-13", "E-16"} == set(gated)
    # all evals runnable on demand
    for eval_id in ("E-01", "E-14", "E-15", "E-16"):
        r = client.post(f"/api/v1/evals/run/{eval_id}")
        assert r.status_code == 201
        assert r.json()["passed"] is True, f"{eval_id}: {r.json()}"
    # expert panel input path (E-05)
    r = client.post("/api/v1/evals/expert-rating", json={"eval_id": "E-05", "score": 4.2, "rater": "domain_expert"})
    assert r.json()["passed"] is True


def test_rbac_f11(client):
    # Behavioral Scientist may not operate the Listening Engine
    r = client.post(
        "/api/v1/signals",
        json={"content": "another customer complaint about claims", "source_type": "voc"},
        headers={"X-Role": "behavioral_scientist"},
    )
    assert r.status_code == 403
    # ...but may run clustering
    r = client.post("/api/v1/clusters/run", headers={"X-Role": "behavioral_scientist"})
    assert r.status_code == 201
    # unknown roles rejected; audit log populated
    assert client.post("/api/v1/clusters/run", headers={"X-Role": "intern"}).status_code == 403
    assert any(a["action"] == "clusters_generated" for a in client.get("/api/v1/audit-log").json())


def test_tenant_isolation(client):
    r = client.post(
        "/api/v1/tenants",
        json={"slug": "rival-corp", "name": "Rival Corp", "industry_context": "banking"},
    )
    assert r.status_code == 201
    rival = {"X-Tenant": "rival-corp"}
    assert client.get("/api/v1/signals", headers=rival).json() == []
    assert client.get("/api/v1/feature-cards", headers=rival).json() == []
    # rival's empty pipeline fails fast, demo tenant unaffected
    r = client.post("/api/v1/pipeline/run", json={}, headers=rival)
    assert r.json()["status"] == "failed"
    assert client.get("/api/v1/pipeline/summary").json()["counts"]["signals"] > 0
    # unknown tenant rejected
    assert client.get("/api/v1/signals", headers={"X-Tenant": "ghost"}).status_code == 404
