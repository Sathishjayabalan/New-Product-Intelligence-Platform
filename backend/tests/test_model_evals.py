"""Tests for the model eval harness (suites S-CLS..S-JDG)."""

H = {"X-Tenant": "demo"}

ALL_SUITES = {"S-CLS", "S-GRD", "S-HYP", "S-BRF", "S-SRV", "S-UJM", "S-JDG"}


def test_catalogue(client):
    cat = client.get("/api/v1/model-evals/catalogue").json()
    assert set(cat["suites"]) == ALL_SUITES
    assert cat["candidate_models"][0] == "deterministic-offline"
    assert cat["generation_model"] != cat["judge_model"]  # PRD 8.3
    # every suite declares engine + threshold
    for spec in cat["suites"].values():
        assert spec["engine"] in {"listening", "behavioral", "prototype", "building", "platform"}
        assert 0 < spec["threshold"] <= 1.0 or spec["threshold"] == 1.0


def test_offline_model_passes_all_suites(client):
    r = client.post(
        "/api/v1/model-evals/run", headers=H,
        json={"models": ["deterministic-offline"]},
    )
    assert r.status_code == 201, r.text
    results = r.json()
    assert len(results) == len(ALL_SUITES)
    for res in results:
        assert res["status"] == "completed"
        assert res["passed"] is True, f"{res['suite_id']}: {res['score']} ({res['details']})"


def test_remote_models_skipped_without_api_key(client):
    r = client.post(
        "/api/v1/model-evals/run", headers=H,
        json={"models": ["claude-sonnet-4-6"], "suites": ["S-CLS", "S-JDG"]},
    )
    assert r.status_code == 201
    for res in r.json():
        # no key in the test environment: must be skipped, never silently passed
        assert res["status"] == "skipped"
        assert res["passed"] is False
        assert "ANTHROPIC_API_KEY" in res["details"]["reason"]


def test_scoreboard_and_qualification(client):
    board = client.get("/api/v1/model-evals/scoreboard", headers=H).json()
    assert board["deterministic-offline"]["qualified"] is True
    assert board["claude-sonnet-4-6"]["qualified"] is False  # skipped suites don't qualify
    for suite_id in ("S-CLS", "S-JDG"):
        assert board["claude-sonnet-4-6"][suite_id]["status"] == "skipped"


def test_validation_of_unknown_models_and_suites(client):
    assert client.post(
        "/api/v1/model-evals/run", headers=H, json={"models": ["gpt-99"]}
    ).status_code == 422
    assert client.post(
        "/api/v1/model-evals/run", headers=H, json={"suites": ["S-XXX"]}
    ).status_code == 422


def test_judge_discrimination_details(client):
    r = client.post(
        "/api/v1/model-evals/run", headers=H,
        json={"models": ["deterministic-offline"], "suites": ["S-JDG"]},
    )
    res = r.json()[0]
    assert res["details"]["good_card_score"] > res["details"]["bad_card_score"]


def test_results_endpoint(client):
    results = client.get("/api/v1/model-evals/results", headers=H).json()
    assert results
    assert {"model_name", "suite_id", "score", "threshold", "status"} <= set(results[0])
