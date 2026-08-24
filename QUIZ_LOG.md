# Comprehension Checks

The gate before any substantial batch of changes is treated as accepted: you get quizzed on it, and it only counts as "done" if you pass. This file logs every check — questions asked, your answers, verdict, and (on a fail) exactly what was missed.

> **⏸ PAUSED as of 2026-08-20.** Building this for a Razorpay internship Buildathon with roughly a week to submission — per-phase quizzes are on hold so they don't block progress. DECISIONS.md and FLOW.md are still being kept rigorously up to date every phase specifically so nothing is lost while this is paused. Plan is one consolidated review pass over all the deferred questions before submission — see the Phase 1 entry below for the first one waiting. Un-pause this section (delete this note) whenever there's time to resume per-phase checks.

## The rule this file enforces

- **Trigger**: end of a BUILD_PLAN.md phase, or any change spanning multiple files/functions within a phase substantial enough that you couldn't have predicted every line — i.e. after "long changes," not after a one-line fix.
- **Before the quiz**: DECISIONS.md and FLOW.md are already updated for the change in question — the quiz is testing your model of the code, not asking you to recall an undocumented decision.
- **Quiz content**: what changed and why (cross-checked against the DECISIONS.md entry), how it fits into the flow (cross-checked against FLOW.md — what calls it, what it calls), and at least one "what would break if..." question that can't be answered by pattern-matching the diff.
- **Pass**: the change is marked accepted here, and we move on to the next chunk of work.
- **Fail**: the change is **not** treated as understood or accepted. I point out specifically what was missed or wrong, in enough detail to close the gap — then either re-quiz on the same material or, if the gap suggests the code itself needs to be clearer/simpler, we discuss that before moving on. A fail is not necessarily a revert — it's a stop-and-explain.

## Log

## [2026-08-20] Check after: Phase 1 (the 30-line seed) — **DEFERRED**
**Covers**: `app/main.py`, `app/kb.py`, `app/models.py`, the Groq model swap, and the 4-experiment verification (see DECISIONS.md + FLOW.md session "2026-08-20 — Phase 0 + Phase 1").

**Status**: quiz asked, not yet answered — deferred at the user's request due to time pressure (building this for a Razorpay internship Buildathon, see project context). Not a pass, not a fail — genuinely pending. Code built during this phase is provisionally treated as good enough to build on top of (it was tested live and worked), but is **not** logged as comprehension-verified until this is revisited.

Q1: Why does `client = Groq()` sit at module level instead of inside `handle_ticket`? What would differ if it were constructed lazily, given what happened when `GROQ_API_KEY` was missing?
Q2: Point to the exact line responsible for the request Groq rejected with a 413 — why did 5.9K tokens succeed but 19K didn't even reach the model?
Q3: One of 20 concurrent requests failed with an unhandled 429 that surfaced as a raw 500. Where would you catch that, what would you do, and why isn't it fixed in Phase 1?
Q4: Why doesn't today's "two-document" experiment actually prove what the guide says it tests? What has to change about the KB first?
Q5: What's the specific mechanism keeping `.env` out of git even after `git add -A`?

**Result**: PENDING — revisit when time allows, ideally before or shortly after the Buildathon submission, since these questions are exactly what a judge or interviewer would ask.

## [2026-08-20] Check after: Phase 2 (real retrieval — Postgres + pgvector) — **DEFERRED**
**Covers**: `app/db/schema.sql`, `app/db/session.py`, `app/retrieval/*`, `scripts/ingest.py`, `app/main.py` rewrite (see DECISIONS.md + FLOW.md session "2026-08-20 (cont.) — Phase 2" and its live-verification follow-up).

**Status**: also deferred, same reason as Phase 1. Code was tested live end-to-end (real Supabase DB, real Gemini embeddings, real Groq generation) and worked, including finding and fixing 3 real bugs — but not comprehension-verified yet.

Q1: `register_vector(conn)` makes a plain Python list insert correctly into a `VECTOR` column, but `vector_search.py` still needed an explicit `%s::vector` cast. Why does one context tolerate the bare list and the other doesn't?
Q2: Two files call `load_dotenv()` — one before its risky import, one after. Which was which, and what specific symptom told us which one was wrong?
Q3: The Gemini SDK swap (`google-generativeai` → `google-genai`) and the Groq model rename (Phase 1) are the same underlying lesson. State it in one sentence.
Q4: `DEFAULT_TENANT = "acme"` is hardcoded in `app/main.py`. Trace exactly what would need to change in Phase 7 for this to become per-request instead — name the files.
Q5: Why does `chunk()` produce exactly 1 chunk per sample doc right now, and what would have to be true of the KB for the 10% overlap logic to actually matter?

**Result**: PENDING — same consolidated review as Phase 1.

## [2026-08-20] Check after: Phase 3 (structured output) — **DEFERRED**
**Covers**: `app/models.py` (`SupportResolution`), `app/main.py` (`instructor.from_groq`, citation filtering, confidence-threshold override, fallback answer) — see DECISIONS.md + FLOW.md session "2026-08-20 (cont.) — Phase 3".

**Status**: also deferred, same reason. Tested live across resolve/escalate/need_info cases; the confidence-threshold-override branch specifically was **not** exercised by any live test yet (flagged in both DECISIONS.md and FLOW.md) — worth constructing a deliberate case for when this is revisited.

Q1: Why is `citations` filtered against `retrieved_ids` *after* the structured completion returns, instead of trusting the model's own list? Give the specific failure mode this prevents.
Q2: The confidence-threshold override only fires `if resolution.action == "resolve"`. What would go wrong if it fired regardless of the model's own action?
Q3: One test returned `confidence: 1.0` on a question the model correctly couldn't answer. Explain why that's not a contradiction — what is the confidence score actually measuring there?
Q4: The empty-`answer`-on-escalate bug wasn't something we planned for — it was found by running the same-intent question twice and getting different behavior. What does that tell you about testing structured LLM output in general, beyond this one bug?
Q5: Point to the exact place an unhandled Groq/instructor exception would still surface as a raw 500 today. Why does `instructor`'s validation retry not cover this case?

**Result**: PENDING — same consolidated review as Phase 1 and 2.

## [2026-08-21] Check after: Phase 4 (hybrid search + rerank) — **DEFERRED**
**Covers**: `app/db/schema.sql` (`search_vector`), `app/retrieval/keyword_search.py`, `fuse.py`, `rerank.py`, `data/kb/error_codes.md`, `evals/retrieval_eval.py` — see DECISIONS.md + FLOW.md session for Phase 4.

**Status**: also deferred, same reason. See DECISIONS.md for two findings worth remembering even though the quiz is paused: (1) on this KB, vector search alone was **not** blind to the exact error-code question the way the guide's toy example predicted; (2) the eval came back tied 100%/100% at both precision@1 and precision@5 for vector-only vs. hybrid+rerank — a real ceiling-effect result of a 6-document corpus, not a demonstrated improvement. The honest claim is "hybrid+rerank is correctly implemented," not "measurably better on this demo."

Q1: Why does RRF combine ranked lists by rank instead of by score? What specifically would break if cosine similarity and `ts_rank_cd` were just added together?
Q2: `search_vector` is a `GENERATED ALWAYS AS ... STORED` column. Why did `scripts/ingest.py` need zero code changes to populate it?
Q3: The reranker reads query+chunk together; vector/keyword search score them independently. Why not just rerank all chunks directly and skip the fast-narrowing step?
Q4: On this specific KB, vector-only retrieval already ranked `error_codes.md` correctly for an exact-error-code question — the guide's toy example predicts it shouldn't. What's the actual explanation, and does it mean hybrid search was pointless to build here?
Q5: The eval came back 15/15 for both vector-only and hybrid+rerank, at both k=1 and k=5. Does this mean Phase 4 was pointless? Explain why not, in terms of what the eval can and can't detect at this corpus size.

**Result**: PENDING — same consolidated review as Phase 1-3.

## [2026-08-24] Check after: Phase 5 (async queue — Celery + Redis) — **DEFERRED**
**Covers**: `app/pipeline.py`, `app/tasks/celery_app.py`, `app/tasks/process_ticket.py`, `app/main.py` (async `POST /tickets` + `GET /tickets/{id}`), `app/db/schema.sql` (`tickets` table) — see DECISIONS.md + FLOW.md session "2026-08-24 — Phase 5 verification".

**Status**: also deferred, same reason. Unlike some earlier phases, this one has two findings that are load-bearing for the Day 4 demo goal in ONE_WEEK_PLAN.md, not just background trivia — worth reading DECISIONS.md's two `visibility_timeout` / `time_limit` entries before answering Q3/Q4 below.

Q1: `task_acks_late=True` alone was not enough to make worker-crash redelivery actually happen quickly. What second setting was missing, what does it control, and why was it set to `120 + buffer` specifically rather than some other number?
Q2: Trace exactly why `autodiscover_tasks(["app.tasks"])` registered zero tasks even though the worker started without error. What convention was it assuming that this repo doesn't follow?
Q3: `time_limit=5` was set on a task that slept for 30s, and the task still ran to completion. Why? What specific OS-level mechanism is missing on Windows that this depends on?
Q4: Given Q3's answer, is `time_limit=120` on `process_ticket` currently a real safety net, decorative, or something in between? What would have to change (which pool, which platform) for it to become real?
Q5: Idempotency dedup happens by checking `idempotency_key` before the `INSERT`. What's the actual failure mode this prevents on the *webhook sender's* side (not the code's), and why does the check need to happen before enqueueing rather than inside the Celery task?

**Result**: PENDING — same consolidated review as Phase 1-4.

### Entry template
```
## [YYYY-MM-DD] Check after: <phase/change>
**Covers**: files/functions changed (link to DECISIONS.md entry + FLOW.md session)

Q1: ...
Your answer: ...
Verdict: correct / partial / wrong — [note]

Q2: ...
...

**Result**: PASS / FAIL
**If FAIL — gaps identified**:
- ...
```
