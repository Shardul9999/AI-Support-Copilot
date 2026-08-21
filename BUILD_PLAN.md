# Build Plan — AI Support Copilot (Phase 5 · Project 01)

## Context

`e:\AI-Support-Copillot` currently contains a single file: `p1-support-copilot-understanding.html`, a companion learning guide (not a spec, not code). It teaches the architecture of an AI support-ticket copilot by growing a 30-line FastAPI+Claude script through five deliberate failures until it becomes the full system: retrieval (pgvector + BM25 + rerank) → structured/confidence-scored generation → async processing (Celery/Redis) → realtime delivery (WebSockets + pub/sub) → multi-tenant isolation (RLS) → semantic caching → observability/evals.

The referenced spec it's a companion to (`phase5-part1.html`) is **not** in this repo — only the guide is. There is no existing code, no `phase5-part1.html`, and no repo scaffolding yet. This plan turns the guide's own §10 "Build order" (the source's recommended sequence) into a concrete, file-level implementation plan, since that section is explicitly designed to be buildable step-by-step, each step ending in something that runs.

Per the guide's own honesty check (§10, §12): steps 1–6 make a legitimate portfolio project; steps 7–9 are what make it interview/production-grade. Realistic timeline is closer to 4–8 weeks of focused work than the "two weeks" the fictional spec claims.

**Companion files, maintained alongside the code from Phase 0 onward:**
- [DECISIONS.md](DECISIONS.md) — why each non-trivial choice (library, approach, default) was made, logged as it's made.
- [FLOW.md](FLOW.md) — current entry points and call graph, plus a per-session log of what changed.
- [QUIZ_LOG.md](QUIZ_LOG.md) — after each phase or substantial change, a comprehension check must be passed before the change counts as accepted.
- [ONE_WEEK_PLAN.md](ONE_WEEK_PLAN.md) — this same build order compressed into a day-by-day 7-day schedule, with the scope cuts made explicit.

## Tech stack (adapted from the guide's code samples to run entirely on free-tier APIs)

- **API**: Python, FastAPI, Pydantic v2
- **Generation**: **Groq** (OpenAI-compatible API, free tier), model `llama-3.3-70b-versatile` — fast inference, generous free rate limits. Structured output (`SupportResolution`) via the `instructor` library patched onto Groq's client, so Pydantic models are still enforced the same way the guide describes.
- **Embeddings**: **Google Gemini** `text-embedding-004` (768-dim, free tier) via the `google-generativeai` SDK — keep the `embed_model` column from the guide's schema so the dimension/provider stays swappable later
- **Storage**: **PostgreSQL (Supabase, free tier)** + `pgvector` extension — one database for everything: `knowledge_chunks` (HNSW vector index + `tsvector`/GIN keyword index, both on the same table, per the guide's original design), tickets, tenants, semantic cache
- **Queue**: Celery + Redis (background jobs, retries, idempotency)
- **Realtime**: FastAPI WebSockets + Redis pub/sub (multi-instance fanout)
- **Multi-tenancy**: Postgres Row-Level Security (`FORCE ROW LEVEL SECURITY`, `SET LOCAL app.current_tenant`) — storage-enforced across every table, vectors included, since everything lives in the one RLS-protected database
- **Reranker**: a cross-encoder (e.g. Cohere rerank or a local cross-encoder model) — TBD lightweight choice at Phase 4
- **Observability**: OpenTelemetry, a `request_id`/correlation-ID context var
- **Evals**: pytest-based golden set (~30 Q&A pairs) as a CI gate

## Repo layout to create

```
AI-Support-Copillot/
  p1-support-copilot-understanding.html   (existing, untouched)
  app/
    main.py                # FastAPI app, /tickets endpoint, websocket endpoint
    models.py               # Pydantic: Ticket, SupportResolution
    kb.py                   # Phase 1 only: hardcoded KB string
    retrieval/
      embed.py              # Gemini embedding client wrapper
      vector_search.py      # pgvector <=> query
      keyword_search.py     # tsvector query
      fuse.py                # reciprocal_rank_fusion
      rerank.py              # cross-encoder rerank
      chunk.py               # chunk(text, size=512, overlap=0.1)
    db/
      schema.sql             # knowledge_chunks, tickets, tenants, cache tables + RLS policies
      session.py             # connection/pool + SET LOCAL tenant helper
    tasks/
      celery_app.py
      process_ticket.py      # the async-pipeline task (sync def wrapping asyncio.run)
    realtime/
      connections.py         # websocket registry
      pubsub.py               # redis publish/subscribe glue
    cache/
      semantic_cache.py       # lookup/store, tenant-scoped, 0.97 threshold, TTL
    telemetry/
      tracing.py               # OTel setup, request_id_var
  data/
    kb/                        # sample doc set to ingest (Phase 2) — refund/shipping/account/payments/escalation .md files
  scripts/
    ingest.py                  # chunk + embed + insert data/kb/*.md into knowledge_chunks (wipe-and-reload for now)
  evals/
    golden_set.jsonl          # ~30 question/answer/citation pairs
    test_eval.py                # pytest gate on retrieval + answer accuracy
  tests/
    test_tenant_isolation.py    # proves cross-tenant reads return zero rows
    test_websocket_multiinstance.py
  docker-compose.yml            # postgres+pgvector, redis (added at Phase 2/5, not before) — Supabase can replace local postgres if preferred
  requirements.txt / pyproject.toml
  .env.example
```

## Phased build order

Each phase ends with something runnable and testable, mirroring the guide's own §10/§11. Phases 1–6 are the "portfolio-ready" milestone; 7–9 harden it.

**Phase 0 — Scaffolding**
Minimal `pyproject.toml`/`requirements.txt`, `.env.example` (`GROQ_API_KEY`, `GEMINI_API_KEY`, `DATABASE_URL` for Supabase Postgres), `.gitignore`, git init. No Docker/Celery yet — the guide is explicit that standing up infra before there's a product to run is how projects stall.

**Phase 1 — The 30-line seed** (`app/main.py`, `app/kb.py`, `app/models.py`)
Single `/tickets` POST endpoint, hardcoded `KB` string, one Groq call, returns `{"reply": str}`. No database.
*Verify*: run the guide's own 4 experiments (§02 checkpoint) — ask something outside the KB, paste a huge KB, ask a two-document question, fire 20 concurrent requests — and confirm each one fails the way the guide predicts.

**Phase 2 — Real retrieval: Postgres + pgvector**
`db/schema.sql` (`knowledge_chunks` table with `embed_model` column + `VECTOR(768)` HNSW index, sized for Gemini's `text-embedding-004`), `retrieval/chunk.py` (512 tokens, 10% overlap), `retrieval/embed.py` (Gemini embeddings client), `retrieval/vector_search.py`. Ingest a real doc set, drop the hardcoded KB, endpoint now does embed → vector search → stuff top-k into prompt. Still fully synchronous.
*Verify*: cosine similarity sanity check on a known pair; confirm chunk boundaries don't split key sentences.

**Phase 3 — Structured output** (`app/models.py`: `SupportResolution`)
Add `answer, citations, confidence, action: Literal["resolve","escalate","need_info"], escalation_reason`. Endpoint branches on `confidence < 0.72`.
*Verify*: force a low-confidence case and confirm it escalates instead of auto-sending; confirm `citations` actually maps to retrieved chunk IDs.

**Phase 4 — Hybrid search + rerank** (`retrieval/keyword_search.py`, `fuse.py`, `rerank.py`)
Add `tsvector`/GIN column, BM25-style keyword search, `reciprocal_rank_fusion` merging vector+keyword top-20, cross-encoder rerank down to top-5.
*Verify*: build a 20-question retrieval eval script comparing top-5 precision before/after hybrid+rerank — the guide is explicit ("if you can't measure it you're guessing").

**Phase 5 — Move to a queue** (`tasks/celery_app.py`, `tasks/process_ticket.py`)
Celery + Redis. `POST /tickets` becomes: insert row, enqueue job, return `202`. Idempotency key on the endpoint (dedupe repeat webhook deliveries). Task config: `max_retries=3, default_retry_delay=60, time_limit=120`. **Deliberately avoid the spec's known bug**: do not declare the task `async def` under `@shared_task` — write it as a normal `def` that calls `asyncio.run(...)` on an inner async pipeline function.
*Verify*: kill a worker mid-job and confirm redelivery doesn't double-send a reply (idempotency key holds); confirm a hung job times out at 120s instead of parking a worker forever.

**Phase 6 — WebSocket delivery** (`app/main.py` websocket route, `realtime/connections.py`, `realtime/pubsub.py`)
First get single-instance push working (in-memory registry). Then deliberately run two uvicorn instances behind a reverse proxy and reproduce the silent-drop bug the guide describes. Then fix it with Redis pub/sub (each instance subscribes at startup in a background task) + 30s ping/pong heartbeats.
*Verify*: with 2 instances running, confirm a job's result reaches an agent connected to the *other* instance.

**Phase 7 — Multi-tenancy + RLS** (`db/schema.sql` policies, `db/session.py`)
Add `tenant_id` to every table. `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY`, app connects as non-owner role, `SET LOCAL app.current_tenant` set per-transaction (not per-connection, to avoid pool bleed).
*Verify*: `tests/test_tenant_isolation.py` — a query that "forgets" the tenant filter must return zero rows, proven by test, not by inspection.

**Phase 8 — Semantic cache** (`cache/semantic_cache.py`)
Tenant-scoped cache keyed on query embedding, threshold ~0.97–0.98 (stricter than retrieval's), TTL, never cache `action != "resolve"` or low-confidence results, invalidate on KB document changes.
*Verify*: the guide's own adversarial pair — `"refund after 14 days"` vs `"refund within 14 days"` — must NOT hit each other's cache entry at the chosen threshold.

**Phase 9 — Observability + evals** (`telemetry/tracing.py`, `evals/`)
OTel tracing with a `request_id` correlation var threaded through API → Celery task → websocket push. `evals/golden_set.jsonl` (~30 real Q&A pairs with expected citations), `evals/test_eval.py` as a CI gate on accuracy.
*Verify*: trace one ticket end-to-end by correlation ID across all three hops; eval suite runs green in CI.

## Notes carried from the guide's "honest scope" section

- Treat the async Celery bug fix (Phase 5) as mandatory, not optional — the guide flags this as present in the spec it's based on.
- Don't cite "65% auto-resolution" or similar numbers as achieved results anywhere (docs, README) — only as targets, and only once the eval suite (Phase 9) can actually measure them.
- Re-evaluate "one HNSW index per tenant" if tenant count grows past a few dozen — likely needs partitioning/filtering within one index instead; not a Phase 7 blocker, just a documented follow-up.

## Verification approach overall

Each phase's "Verify" step above is a manual or scripted check to run before moving on — this mirrors the guide's checkpoints. No phase is "done" until its verify step passes. Phases 1–6 should be demoed together (run the app, submit a ticket, watch it resolve) before starting Phase 7.
