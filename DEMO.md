# Demo Script

A live walkthrough for the Razorpay Buildathon demo — exact commands, in order, each one demonstrating a specific engineering property rather than just "the bot answers a question." Run from the repo root with the API and worker already running (see [README.md](README.md#setup) steps 1–6).

Every `curl` below assumes:
```bash
API=http://127.0.0.1:8000
```

---

## 0. Setup check

```bash
curl -s $API/tickets/999999 -H "X-Tenant-ID: acme"   # → 404 {"detail":"ticket not found"} confirms the API + DB connection are alive
```

## 1. The core loop — submit, resolve, push

```bash
curl -s -X POST $API/tickets \
  -H "Content-Type: application/json" -H "X-Tenant-ID: acme" \
  -d '{"subject":"Refund request","body":"I bought this 10 days ago, can I get a refund?"}'
```
Note the returned `ticket_id` and `request_id`. This returns **immediately** (`202`) — the LLM call hasn't happened yet, it's just been queued.

Now watch it resolve live instead of polling:
```bash
python -m scripts.watch_ticket <ticket_id> ws://localhost:8000 acme
```
You'll see a `{"status": "resolve", "resolution": {...}}` push once the worker finishes — answer, cited `citations` (knowledge-chunk IDs), and a `confidence` score, all in one structured object, not free text.

**What this proves**: the queue decouples the slow LLM call from the request thread (Phase 5), and the WebSocket push means the client didn't have to poll for it (Phase 6).

## 2. Escalation — the system doesn't grade its own homework

```bash
curl -s -X POST $API/tickets \
  -H "Content-Type: application/json" -H "X-Tenant-ID: acme" \
  -d '{"subject":"Legal","body":"My child was injured by a defective product I bought from you and I am considering legal action."}'
```
Poll or watch the result — `action` comes back `"escalate"` or `"need_info"`, not `"resolve"`, with an `escalation_reason`. This is a low-confidence / out-of-scope-for-automation case by design (it's one of the golden-set cases in `evals/golden_set.jsonl`).

**What this proves**: a confidence threshold (`0.72`) forces escalation even if the model itself claimed `"resolve"` — the override lives in `app/pipeline.py`, independent of the model's own judgment.

## 3. Idempotency — a repeated webhook delivery doesn't double-process

```bash
curl -s -X POST $API/tickets \
  -H "Content-Type: application/json" -H "X-Tenant-ID: acme" -H "Idempotency-Key: demo-key-1" \
  -d '{"subject":"Late delivery","body":"My package has not arrived in 12 business days."}'
# → 202, ticket_id N

curl -s -X POST $API/tickets \
  -H "Content-Type: application/json" -H "X-Tenant-ID: acme" -H "Idempotency-Key: demo-key-1" \
  -d '{"subject":"Late delivery","body":"My package has not arrived in 12 business days."}'
# → 200 (not 202), the SAME ticket_id N, no second job enqueued
```

**What this proves**: a payment/webhook-style retry (the exact failure mode Razorpay's own webhook delivery has to handle) can't create a duplicate ticket or send a duplicate reply.

## 4. Tenant isolation — enforced by the database, not the app

```bash
# Submit as globex
curl -s -X POST $API/tickets \
  -H "Content-Type: application/json" -H "X-Tenant-ID: globex" \
  -d '{"subject":"Refund request","body":"I bought this 10 days ago, can I get a refund?"}'
# → 202, ticket_id M

# Try to read it back as acme instead
curl -s $API/tickets/M -H "X-Tenant-ID: acme"
# → 404 — not "403 forbidden", genuinely indistinguishable from "doesn't exist"
```
`globex` has no knowledge base of its own, so its tickets resolve to `need_info`/`escalate` regardless of question — also visible proof the two tenants don't share data.

**What this proves**: this isn't an app-level `WHERE tenant_id = ...` filter that a future bug could forget — it's Postgres Row-Level Security, `FORCE`d even for the app's own connection role. `tests/test_tenant_isolation.py` proves this by test, not by inspection: it runs a query that deliberately "forgets" the tenant filter and confirms Postgres itself still returns zero rows.

## 5. Semantic cache — a near-duplicate question skips the pipeline

Use a question not submitted earlier in this script — steps 1–4 already resolved (and therefore cached) their own questions, so reusing one here wouldn't demonstrate a genuine cold run.

```bash
time curl -s -X POST $API/tickets \
  -H "Content-Type: application/json" -H "X-Tenant-ID: acme" \
  -d '{"subject":"Duplicate charge","body":"I was charged twice for the same order, can you look into it?"}'
# poll/watch until resolved — this is a cold-path run (embed + hybrid retrieval + LLM call)

# resubmit the exact same question
time curl -s -X POST $API/tickets \
  -H "Content-Type: application/json" -H "X-Tenant-ID: acme" \
  -d '{"subject":"Duplicate charge","body":"I was charged twice for the same order, can you look into it?"}'
# resolves noticeably faster — served from semantic_cache, no LLM call made
```
Watch the Celery worker's console output: the second run's log line will jump straight from `task_started` to `task_completed` with no retrieval/generation logging in between.

**What this proves**: near-duplicate tickets don't re-run the full retrieve-and-generate pipeline. The cache threshold (cosine ≥ 0.97) was deliberately picked stricter than retrieval's own bar — a false cache hit hands back a wrong answer with no model in the loop to catch it, so "close enough" has a much higher bar here than it does for citation relevance.

## 6. Distributed tracing — one ticket, three processes, one ID

Take the `request_id` returned by any `POST /tickets` call above (also echoed as the `X-Request-ID` response header) and grep it across whichever terminals are running the API and the worker:

```bash
grep <request_id> <api-terminal-output>      # ticket_received → ticket_enqueued
grep <request_id> <worker-terminal-output>   # task_started → task_completed
grep <request_id> <api-terminal-output>      # websocket_push (back in the API process)
```
All three log lines share the same `request_id`, in order, across two OS processes.

**What this proves**: a ticket's full journey — API receipt, background processing, and the real-time push back out — is reconstructable from logs alone by a human, with no trace viewer required. (Basic OpenTelemetry spans are also emitted per hop via the console exporter, visible in the same terminal output.)

## 7. The accuracy gate

```bash
python -m pytest evals/test_eval.py -v
```
18 real end-to-end calls against the live pipeline (not mocked), asserting both the `action` classification and that citations trace back to the expected source document. This is the CI gate this project's numbers are allowed to be measured against — see [README.md](README.md#testing) for why no single "accuracy %" is claimed beyond what this set actually shows.

---

## If something in this demo doesn't work live

- **`ECONNREFUSED` on `curl`**: the API isn't running — `uvicorn app.main:app --reload --port 8000` in one terminal.
- **Ticket stuck on `"pending"` forever**: the Celery worker isn't running, or Windows needs `--pool=solo` explicitly (`celery -A app.tasks.celery_app worker --loglevel=info --pool=solo`).
- **`unknown tenant` error**: `python -m scripts.apply_schema` seeds `acme` and `globex` automatically — re-run it if the database was freshly created.
- **Every ticket resolves to `need_info`**: the knowledge base is empty — `python -m scripts.ingest`.
