# AI Support Copilot

An AI-powered support-ticket resolution system: a ticket comes in, gets retrieved against a knowledge base with hybrid search, generates a structured, confidence-scored answer, and either auto-resolves or escalates to a human — all async, multi-tenant, cached, and traced end-to-end.

Built for the **Razorpay Buildathon** (internship track). This README covers what the system does, how it's built, and how to run it. For the *why* behind every non-trivial decision, see [DECISIONS.md](DECISIONS.md); for the current, code-accurate request flow, see [FLOW.md](FLOW.md).

## Why this exists

Most "AI support bot" demos are a prompt wrapped around an LLM call. That's a legitimate starting point, but it breaks in predictable ways the moment it meets real traffic: no retrieval means it hallucinates outside a tiny hardcoded context; no queue means a slow LLM call blocks the request thread; no idempotency means a retried webhook double-sends a reply; no tenant isolation means one customer's data can leak into another's answer; no confidence gating means it "resolves" things it shouldn't; no caching means every repeat question re-runs the full pipeline; no tracing means a bug report is unreproducible.

This project was built by deliberately hitting each of those failures first, then fixing them one at a time — see [BUILD_PLAN.md](BUILD_PLAN.md) for the phase-by-phase build order this followed. What's running now is the result of all nine phases, each with a live-verified checkpoint, not just code that compiles.

## What it does

1. A ticket (`subject` + `body`) comes in over `POST /tickets` and is queued immediately (`202`, non-blocking).
2. A background worker embeds the ticket, retrieves relevant knowledge-base chunks via **hybrid search** (vector + keyword, fused and reranked), and asks an LLM to produce a **structured** resolution: an answer, the chunk IDs it actually cited, a self-reported confidence score, and an `action` — `resolve`, `escalate`, or `need_info`.
3. Low-confidence "resolve" verdicts are automatically downgraded to `escalate` — the system doesn't get to grade its own homework.
4. The result is pushed to the client in real time over a **WebSocket**, or can be polled via `GET /tickets/{id}`.
5. Everything is **tenant-isolated at the database level** (Postgres Row-Level Security, not app-level filtering), so a bug that forgets a `WHERE tenant_id = ...` clause fails closed, not open.
6. A near-duplicate ticket is served from a **semantic cache** instead of re-running the whole pipeline.
7. Every ticket's journey across all three hops (API → worker → WebSocket push) carries one `request_id`, so it can be traced end-to-end from logs alone.

## Architecture

```
Client                    FastAPI (app/main.py)                 Postgres (Supabase + pgvector)
  │  POST /tickets  ────────▶  validate tenant, idempotency        ── tenants
  │                            INSERT tickets (status=pending)     ── tickets            (RLS)
  │  ◀──── 202 {ticket_id} ── enqueue Celery task                  ── knowledge_chunks    (RLS, HNSW + tsvector)
  │                                     │                          ── semantic_cache      (RLS, pgvector)
  │                                     ▼
  │                          Celery worker (Redis/Upstash queue)
  │                            resolve_ticket()  [app/pipeline.py]
  │                              ├─ semantic_cache.lookup()  ── HIT → skip straight to result
  │                              ├─ vector_search() + keyword_search() ── reciprocal_rank_fusion() ── rerank()
  │                              ├─ Groq LLM call (structured output via `instructor`)
  │                              ├─ confidence < 0.72 & action=="resolve"  ⇒  forced to "escalate"
  │                              └─ semantic_cache.store()  (only if resolved + confident)
  │                            UPDATE tickets SET status, resolution
  │                            publish_ticket_update()  ──▶  Redis pub/sub "ticket_updates"
  │                                                              │
  │  ◀── WS push {status, resolution} ── every FastAPI instance's background subscriber
  │      (or GET /tickets/{id} to poll)
```

Redis pub/sub (not an in-process registry) is what makes the WebSocket push work correctly with **more than one FastAPI instance** running behind a load balancer — a ticket enqueued via instance A can be pushed to a client connected to instance B. This was deliberately built, broken, and fixed in that order (see [DECISIONS.md](DECISIONS.md), Phase 6) rather than designed correctly from the start, to make the failure mode concrete before fixing it.

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| API | FastAPI + Pydantic v2 | async endpoints, WebSocket route |
| Generation | Groq (`openai/gpt-oss-120b`) | free-tier, fast inference; structured output enforced via `instructor` |
| Embeddings | Gemini `gemini-embedding-001` (768-dim) | separate `RETRIEVAL_QUERY` / `RETRIEVAL_DOCUMENT` task types |
| Storage | Postgres (Supabase) + `pgvector` | one database — tickets, tenants, knowledge chunks, and cache all live together, all RLS-protected |
| Retrieval | Hybrid: pgvector (HNSW) + `tsvector`/GIN keyword search | merged via reciprocal rank fusion, then reranked |
| Queue | Celery + Redis (Upstash) | `task_acks_late`, per-task `time_limit`, retry with backoff, idempotency key on the endpoint |
| Realtime | FastAPI WebSockets + Redis pub/sub | multi-instance-safe fanout, 30s heartbeat |
| Multi-tenancy | Postgres RLS (`FORCE ROW LEVEL SECURITY`) | app connects as a least-privilege role that cannot bypass policies (unlike the default Supabase owner role) |
| Semantic cache | pgvector, tenant-scoped, cosine ≥ 0.97, 24h TTL | stricter than retrieval's own threshold — a false cache hit is worse than a cache miss |
| Observability | OpenTelemetry (console exporter) + `request_id` correlation | traces one ticket across API → worker → WebSocket without needing a collector |
| Evals | pytest, 18-case golden set | end-to-end accuracy gate against the *real* pipeline, not mocked |

## Setup

Requires: Python 3.11+, a free [Groq](https://console.groq.com) API key, a free [Google AI Studio](https://aistudio.google.com) Gemini API key, a free [Supabase](https://supabase.com) Postgres project, and a free [Upstash](https://upstash.com) Redis database. No Docker needed — everything runs against these hosted free tiers.

```bash
# 1. Install dependencies
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
# fill in GROQ_API_KEY, GEMINI_API_KEY, DATABASE_URL (Supabase), REDIS_URL (Upstash rediss://)

# 3. Apply the schema (creates tables, RLS policies, indexes — safe to re-run)
python -m scripts.apply_schema

# 4. Create the least-privilege app role RLS actually applies to,
#    and write APP_DATABASE_URL into .env automatically
python -m scripts.setup_app_role

# 5. Load the knowledge base (wipes and reloads knowledge_chunks + semantic_cache)
python -m scripts.ingest

# 6. Run the API and the worker, in separate terminals
uvicorn app.main:app --reload --port 8000
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo   # --pool=solo required on Windows
```

The API is now live at `http://127.0.0.1:8000` (use `127.0.0.1`, not `localhost` — see [DECISIONS.md](DECISIONS.md) for a local port-binding quirk this sidesteps).

## Try it

```bash
# Submit a ticket (tenant defaults to "acme" if X-Tenant-ID is omitted)
curl -s -X POST http://127.0.0.1:8000/tickets \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: acme" \
  -d '{"subject": "Refund request", "body": "I bought this 10 days ago, can I get a refund?"}'
# → 202 {"ticket_id": 1, "status": "pending", "request_id": "..."}

# Poll for the result
curl -s http://127.0.0.1:8000/tickets/1 -H "X-Tenant-ID: acme"

# Or watch it resolve live over the websocket instead of polling
python -m scripts.watch_ticket 1 ws://localhost:8000 acme
```

See [DEMO.md](DEMO.md) for a full walkthrough script covering every engineering property (async processing, idempotency, tenant isolation, semantic cache, escalation, tracing) with exact commands and expected output — written for a live demo, not just a quick smoke test.

## Testing

```bash
python -m pytest tests/                    # tenant isolation (RLS) + semantic cache correctness
python -m pytest evals/test_eval.py -v     # 18-case end-to-end accuracy gate (real LLM + embedding calls, ~a few minutes)
python -m evals.retrieval_eval             # retrieval-only precision@k, vector-only vs hybrid+rerank
```

All three suites run against the real pipeline — Groq and Gemini calls are not mocked, per this project's own testing discipline (see [DECISIONS.md](DECISIONS.md)). `evals/test_eval.py` clears its tenant's semantic cache before running, so a same-day re-run can't be silently served stale cached answers instead of exercising the live pipeline.

As of the last full run: **18/18 eval cases pass**, **25/25 tests pass** across the full suite. On the 18-case golden set, 13 cases (72%) are expected to auto-resolve and 5 to escalate/need-info — that split, not a single "accuracy %", is the honest measure of this system's auto-resolution behavior on a small, hand-labeled set (see the caveat in [DECISIONS.md](DECISIONS.md) about not overstating this to real-world ticket variety).

## Honest limitations

This was built in about a week for a buildathon submission, not hardened for production. Documented explicitly rather than glossed over:

- **`time_limit=120` on the Celery task is currently a no-op in local dev.** Windows forces Celery's `--pool=solo`, which doesn't enforce hard time limits (no OS-level mechanism for it under that pool). The setting is real and will actively enforce once deployed to a Linux host running the default `--pool=prefork`.
- **No DB connection pooling yet.** Each request opens a fresh Postgres connection. Fine at buildathon scale; `psycopg_pool` is the documented next step before any real load.
- **The eval set is 18 hand-picked, fairly unambiguous cases**, not a stress test of real-world ticket phrasing variety. It's real evidence the pipeline is wired correctly end-to-end — it is not evidence of the auto-resolution rate a production deployment would see.
- **Tracing is basic by design** — console exporter, `request_id` string correlation across three independently-traced hops, no collector/Jaeger/Grafana. Sufficient to answer "trace this one ticket," not a production observability stack.
- **One HNSW index per tenant** doesn't scale indefinitely — fine for a handful of tenants (this demo runs two), documented in [BUILD_PLAN.md](BUILD_PLAN.md) as a follow-up if tenant count grows past a few dozen.

## Project docs

- [BUILD_PLAN.md](BUILD_PLAN.md) — the phase-by-phase plan this was built against
- [DECISIONS.md](DECISIONS.md) — why every non-trivial choice was made, logged as it happened
- [FLOW.md](FLOW.md) — the current, code-accurate request flow and call graph
- [DEMO.md](DEMO.md) — step-by-step live demo script
- [ONE_WEEK_PLAN.md](ONE_WEEK_PLAN.md) — the day-by-day schedule this build followed
