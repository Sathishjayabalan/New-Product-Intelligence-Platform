# CapabilityOS — AI-Powered Product Intelligence Platform

Multi-tenant SaaS implementation of the **NS19 "Create End-State for Each Capability"** framework: a living, AI-driven pipeline that turns raw customer signals into validated, backlog-ready product capabilities.

```
Listening Engine ─→ Behavioral Engine ─→ Prototype & Validation ─→ Building Engine
 signals, insight     clusters, personas,    briefs, cohorts,         UJMs, story maps,
 cards, dedup         feature hypotheses     surveys, validation      feature cards
        └────────────────── Eval gates (E-01 … E-16) at every stage ──────────────────┘
```

## Quickstart

```bash
cd backend
pip install -r requirements.txt
python -m app.seed                      # seed demo tenants + run pilot pipelines
uvicorn app.main:app --port 8000
```

- **Dashboard:** http://localhost:8000/ (pipeline funnel, insight cards, clusters, briefs, UJMs, quality intelligence)
- **API docs:** http://localhost:8000/docs (OpenAPI, API-first per PRD §7.1)
- **Tests:** `python -m pytest tests/`

### Optional: live LLM generation

The platform is **LLM-agnostic** (PRD §7.1). Without credentials it runs a fully deterministic generation path. With credentials, engines enrich narratives via Claude:

```bash
export ANTHROPIC_API_KEY=sk-ant-…
export CAPOS_LLM_MODEL=claude-sonnet-4-6      # generation model
export CAPOS_JUDGE_MODEL=claude-opus-4-8      # eval judge (never the generator, PRD §8.3)
```

## The Four Engines (PRD §5)

| Engine | Features | What it does |
|---|---|---|
| **Listening** | F-01 Signal Ingestion Hub, F-02 AI Insight Cards | 12 source types, hash + semantic dedup, taxonomy classification, quality scoring, trend vectors, fact-grounded insight cards |
| **Behavioral** | F-03 Cluster Engine, F-04 Hypothesis Generator | Deterministic agglomerative clustering with leave-one-out stability scoring, need-state mapping, persona cards, ranked hypotheses with calibrated confidence + domain risk flags |
| **Prototype** | F-05 Brief Generator, F-06 Cohort & Survey Engine | 10-field prototype briefs, power-calculated minimum sample sizes, auto-generated validation surveys, PO edit telemetry |
| **Building** | F-07 UJM Auto-Generator, F-08 Feature Card & Story Map | Journey maps on industry-specific stages, 12-point completeness audit, Agile feature cards with acceptance criteria, story maps, Miro/Figma/PDF/Jira export payloads |

## Platform capabilities

- **F-09 Industry Context Switcher** — six domain contexts (insurance, banking, retail, healthcare, edtech, logistics), each with its own signal taxonomy, journey stages, terminology dictionary, persona archetypes and prompt context injected at the AI layer.
- **F-10 Pipeline Orchestration Dashboard** — signal-to-feature funnel, bottleneck detection, stage log, run latency.
- **F-11 RBAC** — NS19 engine roles enforced per route (`X-Role` header), full audit log.
- **F-12 Evaluation Framework** — the complete E-01…E-16 catalogue (PRD §8). Gate evals (E-01, E-02, E-04, E-08, E-10, E-11, E-13, E-16) **block pipeline progression** and flag the run for human review when below threshold. Human expert panel ratings (E-05, E-12) are submitted via the API; judge LLM is configured separately from the generation model.

## API tour

```bash
T='-H "X-Tenant: demo" -H "Content-Type: application/json"'

# Ingest a signal (Listening Engine)
curl -X POST localhost:8000/api/v1/signals -H "X-Tenant: demo" -H "Content-Type: application/json" \
  -d '{"content": "My claim settlement is stuck for weeks", "source_type": "voc"}'

# Run the full pipeline with eval gates
curl -X POST localhost:8000/api/v1/pipeline/run -H "X-Tenant: demo" -H "Content-Type: application/json" -d '{}'

# Quality Intelligence Dashboard
curl localhost:8000/api/v1/evals/dashboard -H "X-Tenant: demo"

# Switch industry context (F-09)
curl -X PUT localhost:8000/api/v1/tenants/me/context -H "X-Tenant: demo" \
  -H "Content-Type: application/json" -d '{"industry_context": "banking"}'
```

Tenancy is resolved via the `X-Tenant` header with strict row-level isolation; roles via `X-Role` (production deployments terminate SAML/OAuth in front and map assertions to these headers).

## Project structure

```
backend/
  app/
    main.py          FastAPI app + dashboard hosting
    api.py           REST API (all engines, evals, RBAC, audit)
    pipeline.py      orchestrator with eval gates (F-10)
    evals.py         E-01…E-16 catalogue + runner (F-12, PRD §8)
    contexts.py      industry context registry (F-09)
    llm.py           pluggable LLM layer (generation + judge models)
    models.py        multi-tenant SQLAlchemy schema
    engines/
      listening.py   F-01, F-02
      behavioral.py  F-03, F-04
      prototype.py   F-05, F-06
      building.py    F-07, F-08
    seed.py          demo tenants (insurance + banking pilots)
  tests/             end-to-end suite mapped to PRD features
frontend/static/     zero-build dashboard (vanilla JS)
```

## PRD traceability

Implements the **MVP / Pilot Ready** scope (PRD §11) plus all V1.0 engines: every P0/P1 feature F-01…F-12 and the full eval catalogue. Storage is SQLite for development (`CAPOS_DATABASE_URL` switches to PostgreSQL); Kafka streaming connectors, Pinecone embeddings, and SSO termination are deployment-layer integrations behind the existing interfaces.
