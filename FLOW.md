# Execution Flow

A living map of how a request actually moves through the running code — not the intended design, the *current* reality. Updated every time control flow changes: a new entry point, a function that starts calling something new, a file that gets added or removed from the path. If this file and the code disagree, the code is right and this file is stale and needs fixing.

## How to read this file

- **Entry points**: where execution begins (HTTP routes, WebSocket handlers, Celery task entry, CLI scripts).
- **Call graph**: for each entry point, the ordered list of what it calls, one level at a time — function → function it calls → function *that* calls, etc. Not a full trace of every helper, but enough that you can follow a request from "arrives" to "responds" without opening every file.
- **Changed this session**: reset at the start of each coding session. Lists exactly which files/functions were touched and a one-line reason, so you can review a session's delta without diffing the whole repo. Cross-reference [DECISIONS.md](DECISIONS.md) for *why* each change was made.

---

## Entry points

- `POST /tickets` — `app/main.py:handle_ticket`. Asynchronous (Phase 5): validates idempotency, inserts a `tickets` row, enqueues a Celery task, returns `202 {"ticket_id", "status": "pending"}` immediately (or `200` with the prior result if the `Idempotency-Key` was already seen).
- `GET /tickets/{ticket_id}` — `app/main.py:get_ticket`. Polls the `tickets` row for status/resolution.
- Celery task `app.tasks.process_ticket.process_ticket` — consumes the queue, runs the actual pipeline, writes the result back.
- `python -m scripts.ingest` — one-off/re-run-on-change CLI, not part of the running app. Populates `knowledge_chunks`.

## Call graph

```
POST /tickets  [app/main.py:handle_ticket]
  ├─ get_connection()                           [app/db/session.py]
  ├─ SELECT id, status, resolution FROM tickets WHERE tenant_id=... AND idempotency_key=...
  │    └─ if found: return existing row as-is, 200 — do NOT re-enqueue
  ├─ INSERT INTO tickets (...) RETURNING id      (only if idempotency_key not seen before)
  ├─ process_ticket.delay(ticket_id)             [app/tasks/process_ticket.py — enqueues onto Redis/Upstash]
  └─ return {"ticket_id", "status": "pending"}, 202

GET /tickets/{ticket_id}  [app/main.py:get_ticket]
  ├─ SELECT status, resolution FROM tickets WHERE id=... AND tenant_id=...
  └─ 404 if not found, else {"ticket_id", "status", "resolution"}

process_ticket(ticket_id)  [app/tasks/process_ticket.py — Celery worker, bind=True, task_acks_late=True,
                             max_retries=3, default_retry_delay=60, time_limit=120 (prefork-only, see DECISIONS.md)]
  ├─ SELECT tenant_id, subject, body FROM tickets WHERE id=ticket_id
  ├─ resolve_ticket(subject, body, tenant_id)     [app/pipeline.py — the actual RAG pipeline, shared logic]
  │    ├─ embed_query(query)                                    [app/retrieval/embed.py — Gemini gemini-embedding-001]
  │    ├─ vector_search(query_embedding, tenant_id, top_k=20)   [app/retrieval/vector_search.py]
  │    │    └─ SELECT ... ORDER BY embedding <=> %s::vector LIMIT 20   (cosine distance, tenant_id-filtered)
  │    ├─ keyword_search(query, tenant_id, top_k=20)            [app/retrieval/keyword_search.py]
  │    │    └─ SELECT ... WHERE search_vector @@ plainto_tsquery(...) ORDER BY ts_rank_cd(...) LIMIT 20
  │    ├─ reciprocal_rank_fusion(semantic, keyword, top_k=20)   [app/retrieval/fuse.py]
  │    ├─ rerank(query, fused, top_k=5)                         [app/retrieval/rerank.py — local CrossEncoder]
  │    ├─ context = top-5 reranked chunks joined, each tagged "[chunk_id=N] ..."
  │    ├─ structured_client.chat.completions.create(..., response_model=SupportResolution)   [Groq call via instructor]
  │    ├─ filter resolution.citations to ids actually in retrieved_ids
  │    ├─ if confidence < 0.72 and action == "resolve": force action = "escalate"
  │    └─ if action != "resolve" and answer is empty: substitute fallback holding message
  └─ UPDATE tickets SET status=resolution.action, resolution=<json>, updated_at=now() WHERE id=ticket_id
       (still unhandled: a Groq/instructor exception here is caught by `autoretry_for=(Exception,)` and retried up
       to 3 times at 60s intervals, then gives up silently — a permanently-failing ticket is left `status="pending"`
       forever with no operator-visible signal; noted in Phase 1 for the sync path, still true for the async one)

python -m scripts.ingest  [scripts/ingest.py:ingest]
  ├─ DELETE FROM knowledge_chunks   (wipe before reload)
  └─ for each data/kb/*.md file:
       ├─ chunk(text, size=512, overlap=0.1)     [app/retrieval/chunk.py — tiktoken cl100k_base]
       └─ for each chunk: embed_document(chunk) → INSERT INTO knowledge_chunks
```

`app/kb.py` and the hardcoded-KB code path from Phase 1 are gone — deleted, not deprecated in place (see DECISIONS.md).

## Changed this session

### Session: 2026-08-20 — Phase 0 + Phase 1
- `requirements.txt`, `.env.example`, `.gitignore` — new (Phase 0 scaffolding)
- `git init` — repo wasn't initialized before this session
- `app/__init__.py` — new, empty (makes `app/` importable as `app.main` for uvicorn)
- `app/kb.py` — new, hardcoded `KB` string (guide's §02 example verbatim)
- `app/models.py` — new, `Ticket` pydantic model
- `app/main.py` — new, the entry point above; `MODEL` corrected from `llama-3.3-70b-versatile` (deprecated) to `openai/gpt-oss-120b` — see DECISIONS.md
- Environment: rebuilt `.venv` with `uv` instead of stdlib `venv`+`pip` (same `requirements.txt`) — see DECISIONS.md
- Fixed a real secrets-hygiene mistake: user had pasted live `GROQ_API_KEY`/`GEMINI_API_KEY` into `.env.example` (the template meant to be committed) instead of `.env` (gitignored). Moved the real values into `.env`, restored `.env.example` to blank placeholders. Confirmed nothing had been committed yet, so no git-history exposure.
- **Phase 1 verify step — all 4 of the guide's §02 experiments run against the live server, with a real `GROQ_API_KEY`:**
  1. *Outside the KB* ("do you ship to Nepal?") → correctly said the KB doesn't cover it, no invented policy.
  2. *Huge KB pasted in* → a ~19K-token system prompt was rejected outright (413, request too large); a ~5.9K-token version succeeded in 2.24s (4360 prompt tokens, 214 completion tokens). Discovered in the process: this Groq account's free tier caps at **8000 TPM** for `gpt-oss-120b` — tighter than the guide's generic "cost and latency" framing implies, and a hard failure mode, not just a slow/expensive one.
  3. *Two-document question* ("past 14 days AND package never arrived") → correctly combined both facts (store credit + shipping timeline). Not a strong test of the guide's point yet, since the whole 4-line KB fits in one prompt — the real test comes once Phase 4's rerank has to surface both relevant chunks out of many.
  4. *20 concurrent requests* → 19 succeeded (1.05s–6.59s, latency clearly stacking under load on a single sync endpoint), 1 failed with an unhandled Groq 429 (TPM limit) that surfaced as a raw 500 — confirms the guide's §05 problem (no queue yet) and adds a concrete new one: errors from the provider aren't caught anywhere yet either.

### Session: 2026-08-20 (cont.) — Phase 2
- `requirements.txt` — added `google-generativeai`, `psycopg[binary]`, `pgvector`, `tiktoken`; installed and verified in the `uv`-managed venv.
- `app/db/__init__.py`, `app/db/session.py` — new; `get_connection()` wraps `psycopg.connect(DATABASE_URL)` + `register_vector`.
- `app/db/schema.sql` — new; `knowledge_chunks` table (`VECTOR(768)` + HNSW cosine index, `tenant_id` column + index added now per the Phase 7 lesson learned during the earlier Qdrant discussion — see DECISIONS.md).
- `app/retrieval/__init__.py`, `chunk.py`, `embed.py`, `vector_search.py` — new.
- `data/kb/*.md` (5 files: refund, shipping, account management, payments/billing, escalation) — new sample doc set, richer than Phase 1's 4-line KB.
- `scripts/__init__.py`, `scripts/ingest.py` — new; not in the original BUILD_PLAN.md layout, added because Phase 2 needs an actual ingestion path (see DECISIONS.md).
- `app/main.py` — rewritten: hardcoded `KB` replaced with embed → `vector_search` → context-from-chunks, still synchronous.
- `app/kb.py` — deleted.
- Verified so far (without a live DB — blocked on a real `DATABASE_URL`): `py_compile` passes on every new file; `chunk()` tested standalone against the real `data/kb/*.md` files — confirmed overlap behavior at a small size (120 tokens → 4 overlapping chunks from `refund_policy.md`), and confirmed each doc produces exactly 1 chunk at the real 512-token size (docs are short enough that overlap doesn't trigger within a single doc yet — fine for now, revisit if retrieval quality suffers from documents being too coarse).
- **Not yet done**: anything requiring a live Postgres connection — schema creation, `scripts.ingest`, and an end-to-end `/tickets` call through real retrieval. Blocked on the user setting up Supabase (enable `pgvector`, get `DATABASE_URL`).

### Session: 2026-08-20 (cont.) — Phase 2 live verification, with real `DATABASE_URL`
- Applied `app/db/schema.sql` to the real Supabase DB; verified live: `knowledge_chunks` table, `pgvector` extension, both indexes all present.
- Found and fixed 3 real bugs while getting this to actually run (all logged in DECISIONS.md):
  1. `google-generativeai` is fully deprecated, `text-embedding-004` 404s → swapped to `google-genai` + `gemini-embedding-001` (768-dim, verified live).
  2. `app/main.py` called `load_dotenv()` *after* an import that needed the env already loaded → reordered.
  3. `vector_search.py`'s `<=>` comparison needed an explicit `%s::vector` cast — a bare list parameter works for `INSERT` but not inside the operator.
- `python -m scripts.ingest` run for real: 5/5 sample docs → 5 chunks → 5 rows in `knowledge_chunks`.
- `/tickets` tested live, twice, through the *entire* path (embed_query → vector_search → Groq generation):
  - refund question → answer paraphrased `data/kb/refund_policy.md` almost verbatim (mentions "reply with refund request + order number", "5-7 business days" — both specific to that file, not something a model would guess).
  - Nepal shipping question → answer paraphrased `data/kb/shipping_policy.md` ("ships only within India... no workaround or forwarding service").
  - Both confirm retrieval is actually selecting and using the correct document per query, not just returning *a* document.
- Server stopped after testing (not left running).

### Session: 2026-08-20 (cont.) — Phase 3
- `app/models.py` — added `SupportResolution` (`answer`, `citations: list[int]`, `confidence`, `action: Literal["resolve","escalate","need_info"]`, `escalation_reason`).
- `app/main.py` — rewritten: `structured_client = instructor.from_groq(client, mode=instructor.Mode.TOOLS)`; the Groq call now returns a validated `SupportResolution` instead of a raw string; endpoint's return type is `SupportResolution` (FastAPI serializes it directly). Added citation-filtering (drop any cited id not actually retrieved), the `confidence < 0.72` → force-escalate override, and a fallback `answer` when the model escalates with an empty one.
- Verified live (real Groq + real retrieval, several distinct queries):
  - Refund question → `action="resolve"`, `confidence=0.99`, `citations=[4]` (matches the actual retrieved refund-policy chunk).
  - Loyalty-program question (genuinely not in the KB) → `action="need_info"`, `citations=[]` (model correctly didn't cite anything, unprompted), `confidence=1.0` — confirms confidence tracks "sure this is the right classification," not "sure the answer is correct."
  - Injury/legal question → `action="escalate"` both times it was tried, matching `escalation_and_support.md`'s explicit criteria; first run returned an empty `answer` (fixed by the fallback), second run generated its own non-empty one — model behavior isn't deterministic run-to-run, fallback covers both cases.
- **Not yet verified live**: the confidence-threshold override branch itself (a case where the model says `action="resolve"` but `confidence < 0.72`) — not hit by any query tried so far. Code path exists and is straightforward, but flagged in DECISIONS.md as worth constructing a deliberate test case for before the Phase 3 comprehension check.

### Session: 2026-08-21 — Phase 4
- `app/db/schema.sql` — added `search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED` + GIN index; applied live to Supabase via `ALTER TABLE` (schema.sql's `CREATE TABLE IF NOT EXISTS` doesn't retroactively add columns to an already-existing table).
- `app/retrieval/keyword_search.py` — new; Postgres full-text search (`ts_rank_cd` + `plainto_tsquery`) as the keyword half of hybrid search.
- `app/retrieval/fuse.py` — new; `reciprocal_rank_fusion` merging ranked lists by rank, not score.
- `app/retrieval/rerank.py` — new; local `sentence-transformers` `CrossEncoder` (`cross-encoder/ms-marco-MiniLM-L-6-v2`), no external API.
- `data/kb/error_codes.md` — new 6th sample doc, added specifically to give the keyword half something to demonstrate (see DECISIONS.md).
- `app/main.py` — `handle_ticket` now does `vector_search(top_k=20)` + `keyword_search(top_k=20)` → `reciprocal_rank_fusion(top_k=20)` → `rerank(top_k=5)`, replacing the Phase 2 vector-only top-5.
- `evals/__init__.py`, `evals/retrieval_eval.py` — new; 15-question labeled retrieval eval, precision@1 and precision@5, vector-only vs. hybrid+rerank.
- Re-ran `python -m scripts.ingest` (6 docs → 6 chunks, up from 5) to pick up the new doc and populate `search_vector` on all rows.
- **Live results, reported honestly rather than assumed:**
  - Direct comparison on `"error TXN_4471 on checkout"` → vector search alone already ranked `error_codes.md` first (similarity 0.76), contrary to the guide's toy-example prediction that vector search would be blind to it.
  - `evals/retrieval_eval.py` → **15/15 (100%) for both vector-only and hybrid+rerank, at both k=1 and k=5.** No measurable difference between the two methods on this 6-document corpus — a ceiling effect, not evidence the hybrid implementation doesn't matter. See DECISIONS.md for the full reasoning; the honest claim is "correctly implemented," not "measurably better here."
  - Full `/tickets` endpoint re-tested live with the new pipeline wired in (`error TXN_4471` question) → `citations=[7]` (the `error_codes.md` chunk), `confidence=0.99`, `action="resolve"`, answer correctly matches that doc's TXN_4471 entry. Confirms hybrid+rerank didn't regress anything from Phase 3.
  - Minor tooling note, not a code bug: uvicorn's own startup log line intermittently didn't appear in redirected log files during this session's testing (buffering/timing quirk with this shell setup), even though the server was actually up and listening (`netstat` confirmed port 8000 bound) — worth remembering so a quiet log file isn't mistaken for a failed start next time; check the port/process directly if a log looks stuck.

### Session: 2026-08-24 — Phase 5 verification (code from 2026-08-21 session, verified live this session)
- Applied `app/db/schema.sql`'s `tickets` table to the live Supabase DB — it had been written but never actually run there.
- `app/tasks/celery_app.py` — fixed two real startup bugs found by actually running a worker instead of assuming the code worked: (1) `autodiscover_tasks(["app.tasks"])` never finds `process_ticket.py` (Django-convention helper, looks for `tasks.py`) — swapped for explicit `include=["app.tasks.process_ticket"]`; (2) Upstash's `rediss://` URL needs `ssl_cert_reqs` spelled out explicitly or the worker crashes on startup — added `broker_use_ssl`/`redis_backend_use_ssl`. Also added `broker_transport_options={"visibility_timeout": 130}` — see DECISIONS.md, this one changes actual redelivery behavior, not just startup.
- Windows forces `--pool=solo` (no prefork) — noted as the reason two of the day's demo goals split into "verified" vs. "found a real gap," not a code change.
- **Live results:**
  - Full async path (`POST /tickets` → `202` → Celery → `resolve_ticket` → DB update → `GET /tickets/{id}`) — submitted a refund question, a Nepal-shipping-style question, and a TXN_4471 error-code question; all resolved correctly with citations matching the right source doc.
  - Idempotency — two `POST /tickets` with the same `Idempotency-Key` produced exactly one `tickets` row and one enqueued task (confirmed via direct Postgres query and celery log `received` count), second request returned the first's state instead of re-processing.
  - Worker-crash redelivery — killed the worker ~2s into a task (mid-embedding call), confirmed the message sat in Redis's `unacked` hash and the ticket stayed `"status":"pending"`, restarted the worker, confirmed the *same task UUID* was re-received and completed successfully. Required the `visibility_timeout` fix above — without it, this would not have happened within any reasonable wait (Celery's redis-transport default is 3600s).
  - Hard `time_limit` under `--pool=solo` — **not enforced**. A throwaway task (`sleep(30)`, `time_limit=5`) ran to full completion instead of being killed at 5s. Documented in DECISIONS.md as a real, undeployed-yet gap rather than papered over; `time_limit=120` in `process_ticket.py` is left as-is since it'll work correctly once the worker runs on a Linux/prefork host.
- Both FastAPI and the Celery worker left running in the background (`nohup`, detached) after verification, for continued local testing.
