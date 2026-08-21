# One-Week Build Plan — AI Support Copilot

## Honesty check first

[BUILD_PLAN.md](BUILD_PLAN.md) already flags that all 10 phases, done well, from scratch, is realistically 4–8 weeks — and that the source spec's own "two weeks" is optimistic by roughly 4×. This document compresses that into 7 days anyway, which only works under three conditions:

1. **Full-time, focused days** — realistically 7–9 hours each, not "a couple hours after work."
2. **You're applying known concepts, not learning them mid-build.** The understanding guide assumes you already know how to write a RAG pipeline, an async endpoint, a Pydantic model — this week is about *assembling* those into this specific architecture, not learning FastAPI or embeddings for the first time. If a day's concepts are genuinely new to you, that day alone eats 2–3 days and the schedule slips honestly rather than silently.
3. **Deliberate scope cuts**, listed below, so "done in a week" doesn't quietly become "half-built in a week."

If either of the first two isn't true, treat this as a 2-week plan at half-pace instead — the phase order and content don't change, only the calendar.

## What's cut to make 7 days real (not a hidden compromise — decide now, log if it changes)

- **Reranker**: local `cross-encoder/ms-marco-MiniLM-L-6-v2` (sentence-transformers), not a hosted rerank API. Runs offline, no extra free-tier quota to babysit mid-sprint. Swappable later.
- **Infra hosting**: free-tier *hosted* Postgres (Supabase) and Redis (Upstash), not local Docker containers — consistent with why Groq/Gemini were chosen (no local infra to babysit). `docker-compose.yml` from BUILD_PLAN.md remains documented as the general-purpose/offline option, just not what week 1 uses.
- **Knowledge base size**: a focused 10–20 page doc set (a few policies, an FAQ, a couple of "resolved ticket" examples), not an exhaustive corpus. Enough to prove hybrid retrieval and chunking work correctly.
- **Eval golden set**: 15–20 Q&A pairs by end of week, not the full ~30 BUILD_PLAN.md targets for Phase 9. Expand after week 1.
- **Observability**: correlation-ID logging + basic OTel tracing, not a full Jaeger/Grafana dashboard stack. The guide itself calls full observability a "mentioned but not taught" stretch topic (§12).

All five of these are things to *revisit*, not permanent decisions — note in DECISIONS.md if any gets upgraded later.

## Day-by-day

Each day ends with something demoable, per BUILD_PLAN.md's phase philosophy. Each day's work must pass its [QUIZ_LOG.md](QUIZ_LOG.md) comprehension check before the next day starts — don't let two ungraded days stack.

### Day 1 — Phase 0 + Phase 1 (scaffolding + the seed)
- **Setup (first, before writing code)**: create free-tier accounts and collect every credential you'll need this week up front, so no later day blocks on signup — Groq API key, Google AI Studio (Gemini) key, Supabase Postgres connection string (with `pgvector` enabled), Upstash Redis URL.
- Repo scaffolding: `pyproject.toml`/`requirements.txt`, `.env.example`, `.gitignore`, git init.
- Phase 1: the 30-line seed (`app/main.py`, `app/kb.py`, `app/models.py`) — hardcoded KB, one Groq call, `/tickets` endpoint.
- Run the guide's own 4 experiments (§02) against it and confirm each fails as predicted.
- **Demo**: `curl` a ticket, get a reply.

### Day 2 — Phase 2 (real retrieval: Postgres + pgvector)
- `db/schema.sql`: `knowledge_chunks` table, `VECTOR(768)` column + HNSW index (sized for Gemini), `embed_model` column, `tenant_id` column added now even though enforcement is Phase 7 — retrofitting it later is worse.
- `retrieval/chunk.py`, `retrieval/embed.py` (Gemini), `retrieval/vector_search.py`.
- Ingest the focused doc set, drop the hardcoded KB.
- **Demo**: same `/tickets` endpoint now answers from real ingested documents instead of the hardcoded string.

### Day 3 — Phase 3 + Phase 4 (structured output, hybrid search, rerank)
- Morning: `SupportResolution` (via `instructor` + Groq), `confidence < 0.72` branch.
- Afternoon: `tsvector`/GIN column + keyword search, `fuse.py` (RRF merging vector + keyword top-20), local cross-encoder rerank to top-5.
- Build the retrieval eval script early (even at 10 questions) — you'll keep using it, don't leave it to Day 7.
- **Demo**: a forced out-of-KB question now escalates instead of hallucinating; retrieval eval script shows hybrid+rerank beats vector-only.

### Day 4 — Phase 5 (queue)
- Celery + Upstash Redis. `POST /tickets` → insert row → enqueue → `202`.
- Idempotency key on the endpoint. Task written as a plain `def` calling `asyncio.run(...)` internally — **not** `async def` under `@shared_task` (the known bug from the guide, avoid it from the start rather than fixing it later).
- `max_retries=3, default_retry_delay=60, time_limit=120`.
- **Demo**: kill a worker mid-job, confirm redelivery doesn't double-send; confirm a deliberately hung job times out instead of parking the worker forever.

### Day 5 — Phase 6 (WebSockets) — end of the portfolio-ready milestone
- Morning: single-instance WebSocket push, in-memory registry.
- Afternoon: run two instances, reproduce the silent-drop bug on purpose, fix with Redis pub/sub (subscribe at startup) + 30s heartbeats.
- **Demo — do this properly, it's the milestone checkpoint**: submit a ticket through the full pipeline (webhook → queue → retrieval → generation → websocket push) and watch it resolve live in a browser/CLI client, with two instances running.

### Day 6 — Phase 7 + Phase 8 (multi-tenancy, semantic cache)
- Morning: `tenant_id` on every table, RLS + `FORCE ROW LEVEL SECURITY`, per-transaction `SET LOCAL`. Since everything — vectors included — is in the one RLS-protected database, this one policy covers retrieval too, no separate enforcement path needed.
- Afternoon: `semantic_cache` table, `pgvector`-keyed lookup, 0.97–0.98 threshold, TTL, tenant-scoped, invalidate on KB change.
- **Demo**: `tests/test_tenant_isolation.py` passes — a query that forgets the tenant filter, including a vector-search query, returns zero rows; the guide's adversarial pair (`"refund after 14 days"` vs `"refund within 14 days"`) does **not** hit each other's cache entry.

### Day 7 — Phase 9 + integration + the real quiz
- Morning: correlation-ID (`request_id`) threaded through API → Celery → websocket push; basic OTel tracing.
- Midday: expand the eval set toward 15–20 pairs, pytest gate green.
- Afternoon: full integration pass — multi-instance websocket test, cross-tenant test, cache negation test, all together, not in isolation.
- Evening: README + a short demo script (what to run, in what order, to show the whole thing working) + **cumulative comprehension check** — bigger than a single day's quiz, covering the whole week, per QUIZ_LOG.md. This is the actual gate on calling week 1 done.

## After the week

What you'll have: BUILD_PLAN.md's phases 1–6 solid and demoed, 7–8 functional but lightly tested, 9 present but thin (smaller eval set, basic tracing). That's an honest, working portfolio-grade system — not the full production hardening BUILD_PLAN.md's 4–8 week estimate targets. Next steps after this week, in priority order per the guide's own §12: grow the eval set, harden observability, revisit the cut-list above.
