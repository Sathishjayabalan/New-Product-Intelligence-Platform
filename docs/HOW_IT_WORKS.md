# CapabilityOS — How It Works & Parameter Reference

A short operational guide to the platform: what happens when a pipeline runs, how each engine makes its decisions, and every parameter you can tune.

---

## 1. How it works — the big picture

CapabilityOS turns raw customer signals into backlog-ready feature cards through four chained engines. Every stage is scored by the eval framework, and **gate evals stop the run** if quality drops below threshold.

```
                 ┌────────────┐   ┌────────────┐   ┌─────────────┐   ┌────────────┐
 signals ──────► │ LISTENING  │──►│ BEHAVIORAL │──►│  PROTOTYPE  │──►│  BUILDING  │──► feature cards
 (12 sources)    │ classify,  │   │ cluster,   │   │ briefs,     │   │ UJMs, story│    story maps
                 │ dedup,     │   │ personas,  │   │ cohorts,    │   │ maps, cards│    exports
                 │ insight    │   │ hypotheses │   │ surveys,    │   │            │
                 │ cards      │   │            │   │ validation  │   │            │
                 └─────┬──────┘   └─────┬──────┘   └──────┬──────┘   └─────┬──────┘
                       │                │                 │                │
                  E-01..03,16      E-04..07          E-08..10         E-11..13
                       └────────── eval gates: fail ⇒ run "gated", human review ─────┘
```

A run is triggered with `POST /api/v1/pipeline/run` (or the dashboard's **Run Pipeline** button). The orchestrator (`backend/app/pipeline.py`):

1. **Listening** — counts non-duplicate signals (they were classified at ingest time), runs listening evals. Zero signals ⇒ run fails fast.
2. **Behavioral** — re-clusters all insight cards, generates ranked hypotheses per cluster, runs behavioral evals.
3. **Prototype** — takes the top-3 hypotheses by confidence, generates a brief + survey for each; with `auto_validate` on, hypotheses at or above the validation cutoff are marked `validated`.
4. **Building** — for every validated hypothesis, generates a UJM on the industry's journey stages and converts intervention lanes into feature cards.
5. **Platform evals** (latency, terminology) run after completion.

Each stage appends to the run's `stage_log`; if any **gate eval** fails, the run stops with `status: "gated"` and a `gate_reason`, leaving prior artifacts intact for human review.

## 2. How each engine decides

**Listening** (`engines/listening.py`)
- *Classification:* signal tokens are matched against the active industry's taxonomy. Curated keyword boosts count double vs. taxonomy-name tokens; the best-scoring category wins.
- *Dedup:* exact SHA-256 hash of normalized content, plus near-duplicate detection by token Jaccard similarity against the last 500 signals.
- *Quality score:* `0.4·length + 0.35·source weight + 0.25·classification score`. Source weights favour first-party voice (VoC=1.0, survey=0.95, … social=0.65).
- *Insight cards:* always embed a verbatim quote (first 280 chars) from the source signal — this is what the hallucination eval (E-16) traces.

**Behavioral** (`engines/behavioral.py`)
- *Clustering:* greedy agglomerative grouping over insight-card tokens; same-category cards get a +0.25 similarity bonus. Input is canonically ordered, so results are deterministic.
- *Stability (E-04):* the set is re-clustered three times with one random card left out; co-membership Jaccard vs. the full run is averaged.
- *Confidence:* `0.45·cluster size + 0.35·mean signal strength + 0.2·stability − 0.08·rank`, clamped to [0.05, 0.97].
- *Risk flags:* rule table per industry (e.g., insurance `claims_friction` ⇒ `regulatory_review_required`).

**Prototype** (`engines/prototype.py`)
- *Briefs:* 10 fixed fields; completeness = filled fields / 10. ≥ 0.9 ⇒ `ready`, else `needs_review`.
- *Sample size:* two-proportion z-test approximation at α=0.05, power=0.8 — not a hardcoded number.
- *Validation gate:* only `validated` hypotheses may enter the Building engine (API returns 409 otherwise).

**Building** (`engines/building.py`)
- *UJM:* one lane per journey stage of the active industry context; the feature intervention is placed at the mid-journey stage ±1. Completeness is audited against a 12-point schema.
- *Feature cards:* one per intervention lane; story points come from a Fibonacci scale keyed to AC + touchpoint count; quality is a 5-criterion rubric (clarity, testability, AC completeness, domain accuracy, SP calibration).

**LLM enrichment** (`llm.py`): when `ANTHROPIC_API_KEY` is set, every engine asks Claude to enrich its draft (insight summaries, hypotheses, briefs, surveys, UJMs). Output is accepted **only if it passes structural checks** (correct JSON keys, grounded quote present, correct lane count); otherwise the deterministic template result is kept. With no key, the platform runs fully offline.

## 3. Parameter reference

### 3.1 Environment variables (`config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `CAPOS_DATABASE_URL` | `sqlite:///./capabilityos.db` | SQLAlchemy URL; point at PostgreSQL for production |
| `ANTHROPIC_API_KEY` | *(empty — offline mode)* | Enables LLM enrichment |
| `CAPOS_LLM_MODEL` | `claude-sonnet-4-6` | Generation model |
| `CAPOS_JUDGE_MODEL` | `claude-opus-4-8` | Eval judge model — must differ from the generator (PRD §8.3) |
| `CAPOS_DEFAULT_TENANT_SLUG` | `demo` | Tenant used when no `X-Tenant` header is sent |

### 3.2 Request parameters (API)

| Parameter | Where | Values / default | Effect |
|---|---|---|---|
| `X-Tenant` | header, all routes | tenant slug (default `demo`) | Row-level tenant isolation |
| `X-Role` | header, engine routes | NS19 role (default `coe_lead`) | RBAC — role must be permitted for that engine (`contexts.NS19_ROLES`) |
| `auto_validate` | `POST /pipeline/run` | `true` | Auto-advance hypotheses with confidence ≥ 0.45; set `false` to decide manually via `POST /hypotheses/{id}/decision` |
| `enforce_gates` | `POST /pipeline/run` | `true` | `false` lets a run proceed past failing gate evals (calibration/pilot use) |
| `industry_context` | `PUT /tenants/me/context` | `insurance`, `banking`, `retail`, `healthcare`, `edtech`, `logistics` | Switches taxonomy, journey stages, terminology, personas, prompt context |
| `source_type` | `POST /signals` | `api`, `file`, `crm`, `erp`, `stream`, `voc`, `conference`, `survey`, `support_ticket`, `social`, `market_feed`, `webhook` | Affects quality-score weighting |
| `fields_edited` | `PATCH /briefs/{id}` | 0–10 | PO edit telemetry feeding E-09 |
| `fmt` | `GET /ujms/{id}/export/{fmt}` | `miro`, `figma`, `pdf`, `jira` | Export connector payload |

### 3.3 Engine tuning constants (in code)

| Constant | Location | Default | Meaning |
|---|---|---|---|
| `NEAR_DUP_THRESHOLD` | `engines/listening.py` | 0.82 | Token-Jaccard above which a signal is a near-duplicate |
| `SOURCE_WEIGHT` | `engines/listening.py` | per-source map | Source credibility in quality score |
| `CATEGORY_KEYWORD_BOOSTS` | `engines/listening.py` | curated map | Double-weighted classifier keywords per category |
| `CLUSTER_SIMILARITY_THRESHOLD` | `engines/behavioral.py` | 0.18 | Minimum similarity to join an existing cluster (higher ⇒ more, smaller clusters) |
| `RISK_FLAG_RULES` | `engines/behavioral.py` | per-industry map | Category ⇒ compliance/risk flags on hypotheses |
| validation cutoff | `pipeline.py` (`run_pipeline`) | confidence ≥ 0.45 | Auto-validation bar in demo mode |
| top-N hypotheses | `pipeline.py` | 3 | Hypotheses advanced to Prototype per run |
| `baseline_rate`, `mde`, `alpha`, `power` | `engines/prototype.py` (`minimum_sample_size`) | 0.4 / 0.1 / 0.05 / 0.8 | Survey power calculation inputs |
| `UJM_SCHEMA_POINTS` | `engines/building.py` | 12 | UJM completeness audit schema |
| `STORY_POINT_SCALE` | `engines/building.py` | 1,2,3,5,8,13 | Fibonacci sizing for feature cards |

### 3.4 Eval thresholds (`evals.py`, `EVAL_CATALOGUE`)

Gate evals (⛔) block pipeline progression; the rest monitor. Thresholds are reviewed per tenant during onboarding (PRD §8.3).

| Eval | Metric | Threshold | Gate |
|---|---|---|---|
| E-01 | Signal classification accuracy (vs gold set) | F1 ≥ 0.90 | ⛔ |
| E-02 | Deduplication rate (replay test) | ≥ 0.95 | ⛔ |
| E-03 | Signal freshness (median lag) | ≤ 5 min | – |
| E-04 | Cluster stability (leave-one-out Jaccard) | ≥ 0.75 | ⛔ |
| E-05 | Persona relevance (expert panel) | ≥ 3.8 / 5 | – |
| E-06 | Hypothesis recall@3 | ≥ 0.80 | – |
| E-07 | Confidence calibration error | ≤ 0.15 | – |
| E-08 | Brief completeness | ≥ 0.90 | ⛔ |
| E-09 | PO edit rate | ≤ 0.25 | – |
| E-10 | Survey statistical power | 100% meet min sample | ⛔ |
| E-11 | UJM completeness | ≥ 0.85 | ⛔ |
| E-12 | UJM expert acceptance | ≥ 0.70 | – |
| E-13 | Feature card quality | ≥ 4 / 5 | ⛔ |
| E-14 | Pipeline E2E latency (P50) | ≤ 600 s | – |
| E-15 | Domain terminology accuracy | ≥ 0.92 | – |
| E-16 | Hallucination (ungrounded claims) | ≤ 0.02 | ⛔ |

Expert-panel scores for E-05 / E-12 are submitted via `POST /api/v1/evals/expert-rating`; any eval can be run on demand with `POST /api/v1/evals/run/{eval_id}`.

---

*See `README.md` for setup, the API tour, and project structure.*
