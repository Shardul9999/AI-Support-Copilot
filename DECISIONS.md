# Decisions Log

Chronological log of implementation decisions made while building the AI Support Copilot. Every non-trivial choice gets an entry the moment it's made — not just top-level architecture, but "why this library over that one," "why this approach and not the obvious alternative," "why this default value." The goal is that six months from now (or six minutes from now), you can find out *why* a piece of code looks the way it does without archaeology.

## Rules for this file

- One entry per decision, appended in date order at the bottom. Never silently rewritten.
- If a later decision reverses an earlier one, the earlier entry gets a one-line `**Superseded by**: [date] — [reason]` addition rather than being deleted. The history of being wrong is as useful as the current answer.
- Entries are written at the time the decision is made, during coding — not reconstructed afterward from memory.
- Cross-reference [FLOW.md](FLOW.md) for *where* a decision lives in the running code, and [BUILD_PLAN.md](BUILD_PLAN.md) for which phase it belongs to.

## Entry template

```
### [YYYY-MM-DD] Short title
- **Phase**: BUILD_PLAN.md phase this belongs to
- **Decision**: what was chosen
- **Alternatives considered**: what else was on the table, and why each was passed over
- **Reasoning**: the actual why — tradeoffs, constraints, something learned by testing
- **Files affected**: paths
```

---

### [2026-08-12] Generation and embedding provider swap: Groq + Gemini instead of Anthropic + OpenAI
- **Phase**: Planning (affects Phase 0–2 setup)
- **Decision**: Use Groq (`llama-3.3-70b-versatile`) for generation and Google Gemini (`text-embedding-004`, 768-dim) for embeddings, both on free tiers.
- **Alternatives considered**:
  - Anthropic Claude + OpenAI embeddings (the guide's original stack) — rejected, requires paid keys.
  - Gemini for both generation and embeddings — simpler (one provider, one key), but bulk-embedding thousands of KB chunks during Phase 2 ingestion would run into Gemini's free-tier embedding rate limits.
  - Groq + local `sentence-transformers` embeddings — fully free with no rate limit at all, but adds a local model dependency instead of a clean cloud API; user preferred the two-cloud-API option.
- **Reasoning**: Groq's free tier is fast and has generous limits for chat completion; Gemini's embedding free tier is separate from Groq's quota so bulk ingestion doesn't compete with per-request generation traffic. Two API keys is an acceptable cost for that separation.
- **Files affected**: `BUILD_PLAN.md` (tech stack + phase descriptions), future `app/main.py`, `retrieval/embed.py`, `.env.example`.

---

### [2026-08-13] Vector store swap: Qdrant Cloud instead of pgvector, keeping Postgres for keyword search + everything relational
- **Superseded by**: [2026-08-13] — reverted back to single Postgres+pgvector (Supabase). On reflection the isolation trade-off wasn't worth it: Qdrant has no RLS equivalent, so tenant isolation on the vector side would have degraded from storage-enforced to app-enforced, undercutting the exact defense-in-depth lesson (§07) this build is meant to demonstrate — for a "try something new" reason rather than a technical need for Qdrant's scale/features. Kept below for the reasoning trail; not the current design.
- **Phase**: Planning (affects Phase 2, 4, 7)
- **Decision**: Move the dense-vector half of retrieval from Postgres/pgvector to a Qdrant Cloud (free tier) collection. Postgres keeps the `tsvector`/GIN keyword half of hybrid search, plus all relational data (tickets, tenants, semantic cache) and RLS.
- **Alternatives considered**:
  - Full Qdrant hybrid (dense + sparse BM25 vectors in one Qdrant collection, native RRF via Qdrant's Query API) — cleaner single-system retrieval, considered but not chosen; user preferred keeping Postgres in the keyword-search loop, closer to the guide's original two-system design and fewer new concepts (fastembed sparse models) to learn at once.
  - Self-hosted Qdrant via Docker — rejected in favor of Qdrant Cloud free tier, to match the "free hosted API, no extra container to babysit" pattern already set by Groq/Gemini.
- **Reasoning**: this was explicitly a "try something new" request, not a requirement driven by a limitation of pgvector. Splitting cleanly (Qdrant = dense vectors only, Postgres = keyword + relational) keeps the change scoped to Phase 2/4 rather than touching Phase 7's RLS story, tenants, or the cache.
- **Known trade-off — read before touching Phase 7**: Postgres RLS is a *storage-enforced* guarantee — a query that forgets the tenant filter still returns zero rows, because Postgres appends the policy itself. Qdrant has no equivalent. Tenant isolation on the Qdrant side is *app-enforced*: `retrieval/qdrant_search.py` must be the only code path allowed to touch the Qdrant client, and it must treat `tenant_id` as a required argument with a mandatory payload filter. This is weaker than RLS — a bug in that one file is a cross-tenant leak, where a bug in a random Postgres query is not. If tenant count stays small (a few dozen), a further hardening option is one Qdrant *collection* per tenant instead of one shared collection with a payload filter — physical isolation instead of a filter that must always be remembered by the one function allowed to build queries. Deferred unless/until Phase 7 testing finds the payload-filter approach insufficient.
- **Files affected**: `BUILD_PLAN.md` (tech stack, repo layout, Phase 2/4/7), future `retrieval/qdrant_search.py`, `retrieval/fuse.py`, `db/schema.sql`, `.env.example`.

---

### [2026-08-13] `pgvector` stays enabled — but only for the semantic cache table
- **Superseded by**: [2026-08-13] — moot once the Qdrant swap above was reverted. `pgvector` is back to covering `knowledge_chunks` as well as `semantic_cache`, same as the original single-Postgres design. Kept for the reasoning trail (the cache-table-only scoping argument may be useful again if a dedicated vector DB is revisited later).
- **Phase**: Planning (affects Phase 8)
- **Decision**: Keep the `pgvector` Postgres extension enabled, scoped to the `semantic_cache` table only. `knowledge_chunks` has no vector column any more — Qdrant is the sole store for chunk embeddings.
- **Alternatives considered**:
  - Drop `pgvector` entirely, put the cache in a second Qdrant collection — rejected: Qdrant has no native TTL, so cache expiry and "invalidate on KB change" would need to be reimplemented as manual payload-timestamp filtering instead of a plain SQL `DELETE ... WHERE expires_at < now()`. Postgres already does this well.
  - Drop `pgvector`, make the cache exact-match only (no semantic matching) — rejected: this defeats the entire point of a *semantic* cache (§08 of the guide), which is catching paraphrases of the same question.
- **Reasoning**: the cache table is small (hundreds–low thousands of rows per tenant, not the full KB), so a lightweight or even unindexed cosine query is fine — this isn't the scale problem HNSW exists to solve. Keeping cache logic (TTL, invalidation, tenant scoping) in the same relational system as tickets/tenants is simpler than splitting it across two databases.
- **Files affected**: `BUILD_PLAN.md` (tech stack, Phase 8), future `db/schema.sql`, `cache/semantic_cache.py`.

---

### [2026-08-13] Reverted: back to single Postgres+pgvector (Supabase), dropped Qdrant
- **Phase**: Planning (affects Phase 2, 4, 7, 8 — undoes the two entries above)
- **Decision**: Drop Qdrant entirely. `knowledge_chunks` (vectors + `tsvector`) and `semantic_cache` both live in one Supabase Postgres database, matching the guide's original design.
- **Alternatives considered**: keeping Qdrant as planned; keeping Qdrant but only for a stretch/optional branch after the core build works.
- **Reasoning**: asked directly whether to keep Qdrant or revert, and recommended reverting — the main driver was that Qdrant's tenant isolation is app-enforced (a mandatory filter in one function) versus Postgres RLS's storage-enforced guarantee (a query that forgets the filter still returns zero rows). That gap matters more here than usual, because §07 of the companion guide is specifically about that RLS defense-in-depth lesson, and weakening it for "try something new" rather than a real scale/feature need wasn't worth it. Single-database also removes the dual-write consistency problem (every chunk write needing to succeed in two systems with no shared transaction) that would have added real risk to a one-week build. Qdrant remains a reasonable thing to swap in later, on a branch, once the core system is working end-to-end.
- **Files affected**: `BUILD_PLAN.md` (tech stack, repo layout, Phase 2/4/7/8 — reverted), `ONE_WEEK_PLAN.md` (reverted).

---

### [2026-08-20] `requirements.txt` instead of `pyproject.toml`/Poetry
- **Phase**: 0 — Scaffolding
- **Decision**: Plain `requirements.txt` for dependency management.
- **Alternatives considered**: `pyproject.toml` with Poetry or PDM — gives lockfiles and packaging metadata, but this project is a runnable app, not a distributed package. No `pip install`-by-others use case exists.
- **Reasoning**: `pyproject.toml`'s extra structure (build backend, packaging metadata) buys nothing here and adds a tool dependency (Poetry/PDM) to install before anything else runs. `requirements.txt` is the smaller thing that works.
- **Files affected**: `requirements.txt`.

### [2026-08-20] Phase 1's `requirements.txt` only lists Phase 0–1 dependencies
- **Phase**: 0/1
- **Decision**: `requirements.txt` currently has only `fastapi`, `uvicorn[standard]`, `groq`, `python-dotenv`, `pydantic` — not the full eventual stack (Gemini SDK, Celery, Redis, `instructor`, OpenTelemetry, etc.).
- **Alternatives considered**: list the entire tech stack from BUILD_PLAN.md up front, since the choices are already made.
- **Reasoning**: an install list that only contains what's actually imported anywhere in the repo is a more honest signal of what's built versus what's planned — and it keeps `git diff` on this file meaningful per phase (each phase's diff shows exactly what new capability it added) rather than one large speculative list from day one.
- **Files affected**: `requirements.txt` (will grow every phase that adds a new dependency).

### [2026-08-20] `Groq()` client is constructed at import time, matching the guide's `Anthropic()` pattern
- **Phase**: 1
- **Decision**: `client = Groq()` at module level in `app/main.py`, reading `GROQ_API_KEY` from the environment (via `load_dotenv()` first).
- **Alternatives considered**: lazy client construction inside `handle_ticket` (construct-on-first-request), or dependency-injection via FastAPI's `Depends`.
- **Reasoning**: matches the guide's own 30-line seed exactly (`client = Anthropic()` at module level) — the point of Phase 1 is fidelity to that seed, not introducing patterns (DI, lazy init) that belong later. Confirmed by testing: this means `Groq()` raises `GroqError` immediately at import if `GROQ_API_KEY` isn't set — a real key is required before the app can even start, not just before the endpoint is called. That's expected, not a bug; worth knowing before it looks like an app crash.
- **Files affected**: `app/main.py`.

---

### [2026-08-20] Environment tooling: `uv` instead of stdlib `venv`+`pip`
- **Phase**: 0
- **Decision**: Use `uv` (Astral) to create the virtualenv and install dependencies — `uv venv` + `uv pip install -r requirements.txt` — instead of `python -m venv` + `pip install`.
- **Alternatives considered**: stdlib `venv`+`pip` (what was originally set up), Poetry/PDM (rejected earlier — see the `requirements.txt` entry above, still applies: no packaging need here).
- **Reasoning**: user preference, and `uv` is materially faster (this install: 25 packages in ~755ms) with no other tradeoff — it still reads the same `requirements.txt`, so it doesn't reopen the earlier requirements.txt-vs-pyproject.toml decision, just replaces the installer/venv-creation tool underneath it.
- **Files affected**: none in-repo (`.venv/` is gitignored) — this is a local tooling choice, not a code or dependency-manifest change. Recreate with `uv venv && uv pip install -r requirements.txt` on any machine.

---

### [2026-08-20] Model swap: `llama-3.3-70b-versatile` → `openai/gpt-oss-120b` (Groq deprecated the original pick)
- **Phase**: 1
- **Decision**: `MODEL = "openai/gpt-oss-120b"` in `app/main.py`.
- **Alternatives considered**: queried `GET /openai/v1/models` with the real key and got the current lineup: `allam-2-7b`, `groq/compound`, `groq/compound-mini`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.6-27b` (plus whisper/guard models, not relevant). `groq/compound` was passed over — it's Groq's agentic/tool-calling system, not a plain instruct model, and behaves less predictably for a fixed single-turn prompt. `openai/gpt-oss-20b` is the faster/cheaper fallback if `120b` turns out to be too slow or rate-limit-constrained once Phase 5 adds queue-driven volume.
- **Reasoning**: the original choice (`llama-3.3-70b-versatile`) no longer exists on Groq — model lineups on hosted-inference free tiers change over time, so this is exactly the kind of thing to verify against the live API rather than trust from memory (found by actually calling `/models`, not by assumption). `gpt-oss-120b` is the closest match in capability tier and is well-suited to instruction-following, which matters again for Phase 3's structured-output work.
- **Also discovered while testing** (worth remembering for later phases, not a decision on its own): this Groq account's free/on-demand tier caps at **8000 tokens/minute** for `gpt-oss-120b`. Confirmed two ways — a ~19K-token system prompt was rejected outright (413), and firing 20 concurrent requests made 1 of 20 fail with a 429 `rate_limit_exceeded`, which the current code doesn't catch, so it surfaced to the client as a raw 500. Relevant to Phase 5 (the retry/backoff logic needs to specifically handle Groq 429s, not just generic failures) and reinforces Phase 2's motivation independently of cost/latency — on this tier, a large pasted-in KB isn't just wasteful, it can outright fail the request.
- **Files affected**: `app/main.py`.

---

### [2026-08-20] `psycopg` (v3, sync) + `pgvector` python package for the DB layer
- **Phase**: 2
- **Decision**: `app/db/session.py` uses `psycopg.connect(...)` (synchronous) plus `pgvector.psycopg.register_vector` so Python lists round-trip as `VECTOR` columns transparently.
- **Alternatives considered**: `asyncpg` (async, faster, but Phase 2 explicitly stays synchronous per BUILD_PLAN.md — no reason to introduce async here before Phase 5 actually needs it); `psycopg2` (older, still fine, but `psycopg` v3 is the actively developed successor with a cleaner `pgvector` adapter).
- **Reasoning**: matching the phase's own stated constraint (still fully synchronous) rather than reaching for async early. `register_vector` is what makes `vector_search.py` able to pass a plain Python `list[float]` straight into a parameterized query.
- **Files affected**: `app/db/session.py`, `app/retrieval/vector_search.py`, `requirements.txt`.

### [2026-08-20] `tiktoken` as the chunk-sizing tokenizer, despite not being Groq's or Gemini's actual tokenizer
- **Phase**: 2
- **Decision**: `app/retrieval/chunk.py` uses `tiktoken`'s `cl100k_base` encoding purely to count/split tokens for the 512-token chunk size.
- **Alternatives considered**: a word-count or character-count proxy (simpler, no dependency) — rejected because token count correlates much more closely with what actually fills a model's context/rate-limit budget than word count does, and that budget is now a *confirmed* real constraint (the 8000 TPM limit found in Phase 1 testing).
- **Reasoning**: chunk sizing only needs to be *consistent*, not exactly matched to the generation model's own tokenizer — `cl100k_base` is a reasonable, well-supported proxy. Using the real Groq/Gemini tokenizers would mean pulling in two more tokenizer dependencies for a rough sizing decision that doesn't need that precision.
- **Files affected**: `app/retrieval/chunk.py`, `requirements.txt`.

### [2026-08-20] `embed_document` / `embed_query` as two functions, not one
- **Phase**: 2
- **Decision**: `app/retrieval/embed.py` exposes separate functions, both calling Gemini's `embed_content` but with `task_type="retrieval_document"` vs `"retrieval_query"`.
- **Reasoning**: Gemini's embedding API tunes the output differently depending on declared task type — an asymmetric-embedding feature that measurably helps retrieval quality when queries and documents are usually phrased differently (a question vs. a policy statement). Costs nothing extra to use correctly, so there's no reason to collapse it into one generic `embed()` function.
- **Files affected**: `app/retrieval/embed.py`.

### [2026-08-20] `DEFAULT_TENANT = "acme"` placeholder constant in `app/main.py`
- **Phase**: 2 (will be removed in Phase 7)
- **Decision**: `vector_search` requires `tenant_id` as a real parameter (per the Phase 7 plan, decided back in the Qdrant discussion and kept after reverting to Postgres), but until Phase 7 adds real tenant resolution, `app/main.py` passes a single hardcoded `"acme"`.
- **Reasoning**: keeps `vector_search`'s signature already shaped the way Phase 7 needs (`tenant_id` required, not optional) without building tenant resolution (auth, headers, lookup) before there's any multi-tenant need. A single-tenant demo is exactly what a Buildathon submission needs first anyway.
- **Files affected**: `app/main.py`.

### [2026-08-20] `scripts/ingest.py` — not in the original BUILD_PLAN.md repo layout, added because Phase 2 needs it
- **Phase**: 2
- **Decision**: added `scripts/ingest.py` (wipes `knowledge_chunks` and reloads from `data/kb/*.md` every run) and a `data/kb/` folder of sample policy docs — neither was in BUILD_PLAN.md's original repo layout, which only said "ingest a real doc set" without specifying how.
- **Reasoning**: "ingest a real doc set" isn't optional filler — Phase 2 doesn't actually work without something to embed. Wipe-and-reload is deliberately simple (delete-all then re-insert) rather than diffing what changed; real incremental invalidation is Phase 8's problem (cache invalidation on KB change), not Phase 2's.
- **Files affected**: `scripts/ingest.py`, `data/kb/*.md`, `BUILD_PLAN.md` (repo layout updated to match).

### [2026-08-20] Deleted `app/kb.py`
- **Phase**: 2
- **Decision**: removed the Phase 1 hardcoded `KB` string entirely rather than leaving it unused.
- **Reasoning**: BUILD_PLAN.md's Phase 2 description says "drop the hardcoded KB" — once retrieval replaced every use of it, keeping the file around would just be dead code with no path back to being used.
- **Files affected**: `app/kb.py` (deleted), `app/main.py` (import removed).

---

### [2026-08-20] `google-generativeai` swapped for `google-genai`; `text-embedding-004` swapped for `gemini-embedding-001`
- **Phase**: 2
- **Decision**: `app/retrieval/embed.py` now uses `from google import genai` (the `google-genai` package) and `models/gemini-embedding-001`, requested at `output_dimensionality=768` via `types.EmbedContentConfig`.
- **Alternatives considered**: none really — this wasn't a preference choice, it was forced. Running the real ingestion script surfaced that `google-generativeai` is fully deprecated (`FutureWarning: All support ... has ended`) and `models/text-embedding-004` 404s outright on `embedContent`. Queried the live API (`client.models.list()`, filtered for `embedContent` support) and found the current options: `gemini-embedding-001` (stable), `gemini-embedding-2-preview`, `gemini-embedding-2`. Picked the stable, non-preview one.
- **Reasoning**: same lesson as the Groq model rename in Phase 1 — provider model/SDK lineups drift, and the fix is to check the live API rather than trust a plan written from memory. Confirmed `gemini-embedding-001` actually returns 768 dims when asked, matching the existing schema, so no schema change was needed — only the client code and the `embed_model` default (also updated in `app/db/schema.sql`, plus an `ALTER TABLE` against the already-applied live schema, since `CREATE TABLE IF NOT EXISTS` doesn't retroactively fix column defaults).
- **Files affected**: `requirements.txt`, `app/retrieval/embed.py`, `app/db/schema.sql`, live Supabase table (`ALTER TABLE ... ALTER COLUMN embed_model SET DEFAULT ...`).

### [2026-08-20] Bug fix: `load_dotenv()` was running after an import that needed it
- **Phase**: 2
- **Decision**: moved `load_dotenv()` to the very first executable line in `app/main.py`, before any `app.*` import.
- **Reasoning**: `app/retrieval/embed.py` reads `GEMINI_API_KEY` from `os.environ` at module import time (same eager-construction pattern as `Groq()` in Phase 1, logged earlier). `app/main.py` had `from app.retrieval.embed import embed_query` listed *before* its own `load_dotenv()` call, so the import ran first, before the `.env` file had been loaded, raising `KeyError: 'GEMINI_API_KEY'` even though the key was correctly set in `.env`. `scripts/ingest.py` already had the correct order (load_dotenv, then import) and worked first try — `main.py` didn't. General rule going forward: any file whose module-level code reads an env var must have `load_dotenv()` called before it's imported, not just before it's used.
- **Files affected**: `app/main.py`.

### [2026-08-20] `%s::vector` explicit cast needed in `vector_search.py` — plain-list adaptation isn't enough inside an operator expression
- **Phase**: 2
- **Decision**: `ORDER BY embedding <=> %s::vector` (and the same in the `similarity` calculation), not the bare `%s` the code originally had.
- **Reasoning**: `register_vector(conn)` (called in `get_connection()`) is enough for `INSERT` to correctly store a Python `list[float]` into a `VECTOR` column — Postgres accepts an implicit cast in an assignment context. But inside the `<=>` operator, Postgres resolves the parameter's type more strictly, and a bare `%s` gets treated as `double precision[]`, for which no `vector <=> double precision[]` operator exists (`psycopg.errors.UndefinedFunction`, confirmed live). Adding an explicit `::vector` cast on the parameter fixed it immediately. Insert-context leniency and operator-context strictness are just different — this isn't a pgvector bug, it's normal Postgres type resolution.
- **Files affected**: `app/retrieval/vector_search.py`.

### [2026-08-20] Phase 2 end-to-end verification — live, not simulated
- **Phase**: 2
- **What was actually run**: applied `app/db/schema.sql` to the real Supabase database (confirmed table, `pgvector` extension, and both indexes exist via a live query); ran `scripts/ingest.py` against the real Gemini embedding API and real Postgres (5 chunks inserted, one per sample doc); started the real FastAPI server and sent live `/tickets` requests through the full embed → vector-search → Groq-generation path. Both a refund question and the Nepal-shipping question returned answers that paraphrase the correct source document closely (confirmed by comparing the reply text against `data/kb/refund_policy.md` / `shipping_policy.md`), not the old hardcoded 4-line KB — genuine evidence retrieval is wired correctly end-to-end, not just that the code compiles.
- **Files affected**: none (verification record only) — see FLOW.md's session log for the full sequence.

---

### [2026-08-20] `instructor.from_groq(client, mode=instructor.Mode.TOOLS)` for structured output
- **Phase**: 3
- **Decision**: patch the existing Groq client with `instructor` (v1.15.4) rather than hand-rolling JSON-mode prompting + manual `SupportResolution.model_validate_json(...)` parsing.
- **Reasoning**: verified live before committing to it (same discipline as the Groq/Gemini model checks) — `instructor.from_groq` exists, uses Groq's native tool-calling under the hood (`Mode.TOOLS`), and a smoke test against the real API returned a correctly-typed `SupportResolution`-shaped object on the first try, including correctly setting `action="escalate"` for an out-of-KB question. Using the library gets automatic validation-retry-on-malformed-output for free, which a hand-rolled JSON parse wouldn't.
- **Files affected**: `requirements.txt`, `app/main.py`.

### [2026-08-20] Citations are filtered to actually-retrieved chunk ids after the model responds
- **Phase**: 3
- **Decision**: `resolution.citations = [cid for cid in resolution.citations if cid in retrieved_ids]` runs after every structured completion, before the response is returned.
- **Reasoning**: `citations` is only a debugging lifeline (per the guide's §04) if it's trustworthy — a model can still name a chunk id that wasn't actually in its context window, and filtering makes that structurally impossible in the response rather than hoping the model always behaves. Confirmed by testing: on an out-of-KB question, the model correctly self-reported `citations: []` on its own (didn't fabricate a citation), so this filter is a safety net for a failure mode not yet observed rather than a fix for one that was.
- **Files affected**: `app/main.py`.

### [2026-08-20] Confidence-threshold override is independent of the model's own `action` field
- **Phase**: 3
- **Decision**: `if resolution.confidence < 0.72 and resolution.action == "resolve": force action to "escalate"`. The model's own action classification is trusted as-is in every other case (including when it already says escalate/need_info at high confidence).
- **Reasoning**: this is BUILD_PLAN.md's stated design (the guide's §04 point that the threshold is a *business* safety net layered on top of, not a replacement for, the model's own judgment). Live-tested: an out-of-KB question got `action="need_info", confidence=1.0` on its own (correct, no override needed) — a genuinely low-confidence-but-labeled-resolve case wasn't observed in this round of manual testing, so the override branch itself is unit-verified by code inspection but not yet hit by a live example; worth specifically constructing one before the Phase 3 comprehension check.
- **Files affected**: `app/main.py`.

### [2026-08-20] Fallback customer-facing `answer` when the model escalates with an empty one
- **Phase**: 3
- **Decision**: if `action != "resolve"` and `answer` is empty/whitespace, substitute a canned holding message ("forwarded to a specialist...").
- **Reasoning**: found by testing, not anticipated — an injury/legal-escalation question sometimes got back `answer=""` (defensible on the model's part: declining to fabricate a substantive reply for a legal matter), which would look like a broken response in a demo. A second identical-intent request on retest actually got a real generated answer instead (model behavior isn't deterministic run-to-run) — the fallback makes the customer-facing behavior consistent either way rather than depending on which path the model takes.
- **Files affected**: `app/main.py`.

### [2026-08-20] Confidence is not a probability — reconfirmed live, worth remembering going into evals (Phase 9)
- **Phase**: 3 (relevant again later)
- Observed directly: an out-of-KB question returned `confidence: 1.0` alongside `action: "need_info"`. That 1.0 isn't "100% sure the answer is correct" — it's "100% sure escalating/asking for more info is the right call here." Exactly the distinction the guide's §04 callout warns about. Matters for Phase 9's eval design: grading should check whether the *action* was right, not treat a high confidence score as a correctness signal on its own.

---

### [2026-08-20] Postgres full-text search (`tsvector`/`ts_rank_cd`) as the keyword half, local cross-encoder as the reranker
- **Phase**: 4
- **Decision**: `search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED` + GIN index for keyword search; `sentence-transformers`' `cross-encoder/ms-marco-MiniLM-L-6-v2` (local, no API) for reranking.
- **Reasoning**: matches the guide's original design (Postgres does both halves of hybrid search) and the earlier ONE_WEEK_PLAN.md cut-list decision to use a local reranker rather than a hosted rerank API, specifically to avoid depending on a third free-tier quota mid-Buildathon. A `GENERATED ALWAYS AS ... STORED` column means `search_vector` is maintained automatically by Postgres on every insert — `scripts/ingest.py` needed zero changes.
- **Files affected**: `app/db/schema.sql`, `app/retrieval/keyword_search.py`, `app/retrieval/rerank.py`, `requirements.txt`.

### [2026-08-20] Added `data/kb/error_codes.md` specifically to exercise hybrid search's reason for existing
- **Phase**: 4
- **Decision**: added a sixth sample doc containing specific error codes (`TXN_4471`, `TXN_9987`, `CHK_2210`, `ADDR_1103`) — deliberately mirroring the guide's own "exact rare token" example (§03).
- **Reasoning**: the original 5 docs are all generic policy prose with no unique identifiers, so there was no case in the sample KB where keyword search's actual advantage (finding exact tokens vector search is theoretically blind to) could be demonstrated or measured. Without this doc, Phase 4's hybrid-vs-vector-only comparison would have nothing interesting to show.
- **Also discovered while testing this**: on this specific corpus, with Gemini's `gemini-embedding-001`, vector search was **not** actually blind to `"error TXN_4471 on checkout"` — it ranked `error_codes.md` correctly as the top result (similarity 0.76) even without any keyword help. This nuances the guide's toy-example claim rather than confirming it as-is: modern, larger embedding models pick up partial signal from alphanumeric tokens via subword tokenization, so the "total blind spot" is less absolute than a 4-line-KB illustration suggests. Hybrid search is still the architecturally correct design (matters more at larger scale, with truly opaque tokens like UUIDs, or with weaker/smaller embedding models) — but the retrieval eval script's actual before/after numbers are what should be cited, not the assumption that vector search fails here.
- **Files affected**: `data/kb/error_codes.md`.

### [2026-08-20] `evals/retrieval_eval.py` — precision@5, not a full IR metric suite
- **Phase**: 4
- **Decision**: a 15-question labeled set (`question` → `expected_source`), measuring precision@5 as "did the expected source document appear in the top-5 results" for vector-only vs. hybrid+rerank.
- **Reasoning**: BUILD_PLAN.md's Phase 4 verify step explicitly calls for measuring before/after rather than assuming improvement ("if you can't measure it you're guessing"). Since every doc in the current KB is exactly one chunk, "expected source in top-5" is equivalent to "the correct chunk was retrieved" — a full precision/recall/NDCG suite would need per-chunk relevance judgments that don't add information yet at this corpus size. Revisit once documents are chunked into multiple pieces each (more realistic KB size) — then this simple metric stops being equivalent to chunk-level precision.
- **Files affected**: `evals/retrieval_eval.py`, `evals/__init__.py`.

### [2026-08-21] Retrieval eval result: vector-only and hybrid+rerank tied at 100%, on both precision@1 and precision@5 — reported honestly, not spun
- **Phase**: 4
- **What actually happened**: ran the 15-question eval twice (once at precision@5, then again with precision@1 added after the first run showed a ceiling effect). Result both times: **15/15 (100%) for vector-only, 15/15 (100%) for hybrid+rerank** — at both k=1 and k=5. No measurable difference between the two methods on this corpus.
- **Why, and why this is not a failure of the hybrid implementation**: the KB has only 6 total documents, each topically distinct and each exactly one chunk. `gemini-embedding-001` is strong enough that vector similarity alone already ranks the correct document first for every one of the 15 questions, including the exact-error-code question (see the earlier entry above — vector search wasn't actually "blind" to `TXN_4471` here). With only ~6 candidates total, there's very little room for a retrieval method to fail, so this eval currently has no discriminating power between the two approaches.
- **What this means going forward**: hybrid search + rerank stays in the pipeline — it's still the architecturally correct design (matters at real scale, with a weaker/smaller embedding model, with truly opaque IDs like UUIDs, or with a corpus containing genuinely confusable near-duplicate documents), and removing it would contradict both BUILD_PLAN.md's design and the point of building it at all. But **the honest claim to make about this project is "hybrid+rerank is correctly implemented," not "hybrid+rerank measurably improved retrieval on this demo"** — the eval doesn't show the latter, and shouldn't be cited as if it did. If there's time later, a harder eval (more documents, some deliberately near-duplicate/confusable, or documents with much longer content requiring the 10% chunk overlap to matter) would be a better test of the mechanism than adding more of the current easy-to-distinguish policy questions.
- **Files affected**: `evals/retrieval_eval.py` (added precision@1 alongside precision@5, since precision@5 alone was nearly unfalsifiable at this corpus size).

---

### [2026-08-24] Bug fix: `autodiscover_tasks(["app.tasks"])` never actually found `process_ticket`
- **Phase**: 5
- **Decision**: replaced `celery_app.autodiscover_tasks(["app.tasks"])` with an explicit `include=["app.tasks.process_ticket"]` in the `Celery(...)` constructor.
- **Reasoning**: `autodiscover_tasks` is a Django-convention helper — for each package name given, it imports `<package>.tasks`. The actual task lives in `app/tasks/process_ticket.py`, not `app/tasks/tasks.py`, so the worker started successfully but registered zero tasks (`celery inspect registered` came back empty). Found by actually starting a worker and inspecting its registered-task list rather than assuming the call worked because it didn't error. `include=` is the more direct mechanism for a non-Django layout and doesn't depend on a filename convention.
- **Files affected**: `app/tasks/celery_app.py`.

### [2026-08-24] Bug fix: Upstash's `rediss://` URL needs explicit `ssl_cert_reqs`
- **Phase**: 5
- **Decision**: added `broker_use_ssl={"ssl_cert_reqs": ssl.CERT_REQUIRED}` and `redis_backend_use_ssl={"ssl_cert_reqs": ssl.CERT_REQUIRED}` to `celery_app.conf`.
- **Reasoning**: the worker crashed immediately on startup (`ValueError: A rediss:// URL must have parameter ssl_cert_reqs...`) — Celery's redis backend refuses to build a TLS connection without this being spelled out, even though the URL scheme alone (`rediss://`) already implies TLS. `CERT_REQUIRED` works because Upstash presents a publicly-trusted certificate; no need to relax to `CERT_NONE`.
- **Files affected**: `app/tasks/celery_app.py`.

### [2026-08-24] `broker_transport_options={"visibility_timeout": 130}` — the default (1 hour) would have silently defeated the worker-crash-redelivery design
- **Phase**: 5
- **Decision**: set the redis transport's `visibility_timeout` to 130s (`time_limit=120` + a small buffer), instead of leaving Celery's default of 3600s.
- **Reasoning**: found by live-testing the exact scenario BUILD_PLAN.md/ONE_WEEK_PLAN.md's Day 4 demo goal calls for — kill a worker mid-task and confirm redelivery. With `task_acks_late=True` but no `visibility_timeout` override, the in-flight message sat in Redis's `unacked` hash indefinitely; restarting the worker did **not** trigger immediate redelivery (kombu's redis transport only restores unacked messages once `visibility_timeout` elapses, checked via a periodic timer at roughly `visibility_timeout/2`). Left at the 3600s default, a crashed worker's ticket would have looked "stuck forever" for up to an hour — defeating the entire point of `task_acks_late`. Setting it just above `time_limit` keeps the two numbers coupled on purpose: a task that's still legitimately running (under 120s) must never be redelivered out from under itself, but a genuinely dead worker's task now recovers in roughly a couple of minutes instead of up to an hour.
- **Verified live**: submitted a ticket, killed `-9`'d the worker ~2s after it received the task (mid-embedding), confirmed via direct Redis inspection (`HLEN unacked` = 1, ticket stayed `"status":"pending"`), restarted the worker, and watched the *same task UUID* get re-received and complete successfully once the visibility window elapsed. Exactly one final DB row/resolution — no duplicate side effects, matching the intent of `task_acks_late`.
- **Files affected**: `app/tasks/celery_app.py`.

### [2026-08-24] Discovered, not fixed: `time_limit=120` is a no-op under `--pool=solo`, which Windows forces
- **Phase**: 5
- **What was found**: Celery's hard `time_limit` is enforced by the **prefork** pool killing the worker's child process at the deadline. Prefork isn't available on Windows, so the worker must run with `--pool=solo` (single process, no subprocess to kill). Verified directly: a throwaway task (`time.sleep(30)`, decorated with `time_limit=5`) was submitted and ran to full completion in ~31.8s, returning its value normally — no `TimeLimitExceeded`, no interruption. `worker_prefetch_multiplier=1` + solo pool also means a genuinely hung task blocks that single worker from picking up *any* other ticket for the entire hang duration, not just the one task.
- **Why not fixed now**: fixing this properly means either (a) deploying the worker on Linux/Docker for a real prefork pool — the actual intended production target, matching the free-hosted-infra pattern already used for Postgres/Redis — or (b) an application-level timeout inside `resolve_ticket`/`process_ticket` itself (e.g. running the pipeline in a helper thread and enforcing a deadline with `concurrent.futures.Future.result(timeout=...)`), which adds real complexity for a problem that's specific to local Windows dev, not the deployed system.
- **Decision for now**: leave `time_limit=120` in the code as documentation of intent and as something that *will* work correctly once deployed to a Linux host with `--pool=prefork` (the default there) — but don't claim it's an active safety net while developing on Windows with `--pool=solo`. Noted here so this isn't mistaken for "verified" the way the redelivery test above genuinely was.
- **Files affected**: none (finding, not a code change) — `app/tasks/process_ticket.py`'s `time_limit=120` is unchanged.

### [2026-08-24] Phase 5 end-to-end verification — live, not simulated
- **Phase**: 5
- **What was actually run**: applied the `tickets` table migration to the live Supabase DB (it had never actually been run there, despite `schema.sql` already having the table defined from the Phase 5 coding session). Started a real FastAPI server and a real Celery worker (`--pool=solo`, against live Upstash Redis) and: (1) submitted a ticket through the full `POST /tickets` → `202` → worker → `resolve_ticket` → DB update → `GET /tickets/{id}` path, got back a correct, citation-backed answer; (2) sent two requests with the same `Idempotency-Key`, confirmed only one row/task was created and the second request returned the first's (in-progress, then final) state without re-enqueuing; (3) killed the worker mid-task and confirmed redelivery (see the `visibility_timeout` entry above); (4) found and documented that `time_limit` doesn't actually fire under the Windows-forced `--pool=solo` (see the entry above).
- **Files affected**: none (verification record only) — see FLOW.md's session log for the full sequence.

---

*(Further entries land as coding continues into Phase 6 onward.)*
