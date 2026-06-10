"""Tests for every Signal Ingestion Hub method (F-01)."""

import json

import pytest

H = {"X-Tenant": "ingest-co"}


@pytest.fixture(scope="module", autouse=True)
def tenant(client):
    """Dedicated tenant so ingestion volume doesn't disturb other suites."""
    client.post(
        "/api/v1/tenants",
        json={"slug": "ingest-co", "name": "Ingest Co", "industry_context": "insurance"},
    )


def count_signals(client, source_type=None):
    signals = client.get("/api/v1/signals?limit=500", headers=H).json()
    if source_type:
        signals = [s for s in signals if s["source_type"] == source_type]
    return len(signals)


def test_csv_upload(client):
    csv_body = (
        "content,source_type,source_name,tags\n"
        '"Claim portal rejects my hospital bill uploads every time",support_ticket,csv-import,claims;portal\n'
        '"Premium quote was double what the comparison site showed",voc,csv-import,\n'
    )
    r = client.post(
        "/api/v1/signals/upload", headers=H,
        files={"file": ("export.csv", csv_body.encode(), "text/csv")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["ingested"] == 2
    assert r.json()["errors"] == []


def test_json_and_jsonl_and_txt_upload(client):
    payload = {"signals": [
        {"content": "Renewal reminder arrived after the policy had already lapsed", "source_type": "crm"},
    ]}
    r = client.post(
        "/api/v1/signals/upload", headers=H,
        files={"file": ("export.json", json.dumps(payload).encode(), "application/json")},
    )
    assert r.json()["ingested"] == 1

    jsonl = b'{"text": "Agent could not explain the rider exclusions during the sales call"}\n'
    r = client.post(
        "/api/v1/signals/upload", headers=H,
        files={"file": ("feed.jsonl", jsonl, "application/x-ndjson")},
    )
    assert r.json()["ingested"] == 1

    txt = b"Customers keep asking for monthly premium instalments instead of annual\n"
    r = client.post(
        "/api/v1/signals/upload", headers=H,
        files={"file": ("notes.txt", txt, "text/plain")},
    )
    assert r.json()["ingested"] == 1

    # unsupported extension rejected
    r = client.post(
        "/api/v1/signals/upload", headers=H,
        files={"file": ("data.xlsx", b"binary", "application/octet-stream")},
    )
    assert r.status_code == 422


def test_webhook_receiver(client):
    # flat object with a probed content key
    r = client.post(
        "/api/v1/webhooks/typeform", headers=H,
        json={"feedback": "The claims status page never updates, I call support weekly"},
    )
    assert r.status_code == 201 and r.json()["ingested"] == 1
    # wrapped event list with nested content
    r = client.post(
        "/api/v1/webhooks/segment", headers=H,
        json={"events": [{"event": {"text": "Quote journey crashed at the payment step on mobile"}}]},
    )
    assert r.json()["ingested"] == 1
    # payload without text content rejected
    r = client.post("/api/v1/webhooks/bad", headers=H, json={"amount": 42})
    assert r.status_code == 422
    assert count_signals(client, "webhook") >= 2


def test_ndjson_stream(client):
    body = (
        '{"content": "Telemetry: claim document verification queue above SLA for 3 days"}\n'
        '{"content": "Telemetry: renewal API error rate spiked to 4 percent"}\n'
    )
    r = client.post(
        "/api/v1/signals/stream", headers={**H, "Content-Type": "application/x-ndjson"},
        content=body.encode(),
    )
    assert r.status_code == 201, r.text
    assert r.json()["ingested"] == 2
    # malformed line rejected
    r = client.post("/api/v1/signals/stream", headers=H, content=b"not json\n")
    assert r.status_code == 422


def test_connectors(client):
    assert set(client.get("/api/v1/connectors").json()) == {
        "salesforce", "hubspot", "zendesk", "intercom", "sap_erp", "kafka",
    }
    cases = {
        "salesforce": {"records": [{"Subject": "Complaint", "Description": "Underwriting asked for the same documents three times", "Type": "Case"}]},
        "hubspot": {"results": [{"properties": {"hs_note_body": "Prospect wants usage-based premium pricing for their fleet"}}]},
        "zendesk": {"tickets": [{"subject": "Refund", "description": "Cancelled policy but the premium refund never arrived"}]},
        "intercom": {"conversations": [{"source": {"body": "How do I add a flood rider to my existing coverage?"}}]},
        "sap_erp": {"items": [{"DocumentNo": "INV-991", "LongText": "Broker commission disputes delaying policy issuance batch"}]},
        "kafka": {"messages": [{"key": "k1", "value": {"text": "Stream event: surge in claim intake from northern region"}}]},
    }
    for name, payload in cases.items():
        r = client.post(f"/api/v1/connectors/{name}/sync", headers=H, json=payload)
        assert r.status_code == 201, f"{name}: {r.text}"
        assert r.json()["ingested"] == 1, f"{name}: {r.json()}"
    # unknown connector + empty payload rejected
    assert client.post("/api/v1/connectors/oracle/sync", headers=H, json={}).status_code == 422
    assert client.post("/api/v1/connectors/zendesk/sync", headers=H, json={"tickets": []}).status_code == 422
    assert count_signals(client, "crm") >= 2
    assert count_signals(client, "erp") >= 1


def test_email_ingestion(client):
    raw = (
        "From: angry.customer@example.com\n"
        "Subject: Claim 4411 still unpaid\n"
        "\n"
        "It has been nine weeks since I submitted claim 4411 and the settlement is still pending.\n"
    )
    r = client.post("/api/v1/signals/email", headers=H, json={"raw": raw})
    assert r.status_code == 201, r.text
    assert r.json()["ingested"] == 1
    signals = client.get("/api/v1/signals?limit=500", headers=H).json()
    email_sig = next(s for s in signals if s["source_type"] == "email")
    assert "Claim 4411" in email_sig["content"]
    assert email_sig["source_name"] == "angry.customer@example.com"


def test_feed_pull_validation(client):
    # invalid URL scheme rejected by schema
    r = client.post("/api/v1/signals/pull", headers=H, json={"url": "ftp://feeds.example.com/x"})
    assert r.status_code == 422
    # unreachable feed -> 502, not a crash
    r = client.post(
        "/api/v1/signals/pull", headers=H,
        json={"url": "http://127.0.0.1:9/unreachable", "format": "json"},
    )
    assert r.status_code == 502


def test_rbac_applies_to_new_methods(client):
    behav = {**H, "X-Role": "behavioral_scientist"}
    assert client.post(
        "/api/v1/signals/stream", headers=behav, content=b'{"content": "x y z"}'
    ).status_code == 403
    assert client.post(
        "/api/v1/connectors/zendesk/sync", headers=behav,
        json={"tickets": [{"subject": "a", "description": "b c d"}]},
    ).status_code == 403


def test_dedup_applies_across_methods(client):
    content = "Identical complaint arriving through two different channels for dedup"
    r1 = client.post("/api/v1/signals", headers=H, json={"content": content, "source_type": "voc"})
    assert r1.json()["is_duplicate"] is False
    r2 = client.post("/api/v1/webhooks/zapier", headers=H, json={"text": content})
    assert r2.json()["duplicates"] == 1 and r2.json()["ingested"] == 0
