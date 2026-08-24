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

### [2026-08-24] WebSocket registry keyed on `asyncio.Queue`, not the raw `WebSocket` object
- **Phase**: 6
- **Decision**: `app/realtime/connections.py`'s in-memory registry maps `ticket_id -> set[asyncio.Queue]`. The Redis pub/sub subscriber (and the "already resolved" immediate-push path) put messages onto a connection's queue; they never touch the `WebSocket` object directly.
- **Alternatives considered**: registry holds the `WebSocket` directly, and a pushed update calls `websocket.send_json()` straight from the pub/sub callback — simpler, but then the per-connection handler loop (which also needs to read client messages for disconnect detection and drive the heartbeat timer) would have two independent code paths writing to/reading from the same socket with no way to wait on "next pushed update OR next client message OR heartbeat timeout" as one coordinated thing.
- **Reasoning**: routing pushes through a queue lets the single per-connection handler (`app/main.py:watch_ticket`) use one `asyncio.wait({queue.get(), websocket.receive_text()}, timeout=30)` loop to drive all three concerns (deliver an update, notice a client disconnect, send a heartbeat on timeout) without extra locking or a second task per connection.
- **Files affected**: `app/realtime/connections.py`, `app/main.py`.

### [2026-08-24] Went straight to the Redis pub/sub design instead of literally shipping the single-instance direct-dispatch version first
- **Phase**: 6
- **Decision**: did not commit an intermediate version where the Celery worker calls the connections registry directly (in-process only, no Redis). Went straight to: worker publishes to Redis → every FastAPI instance's background subscriber (started in `lifespan`) receives every message → each instance's `connections.dispatch_local` decides whether it has a local queue for that ticket.
- **Reasoning**: ONE_WEEK_PLAN.md's Day 5 plan calls for building the silent-drop bug on purpose (single-instance direct dispatch) and then fixing it, as a *learning* exercise. That's the right way to build the mental model live, but it's not a good reason to put deliberately-broken code into the actual repo history/main branch. The mechanism is documented instead: the Celery worker process and any given FastAPI instance are different processes; a direct in-process call from the worker can't reach a client connected to a *different* uvicorn instance, so with 2+ instances behind a proxy, updates would silently never reach roughly half of connected clients depending on which instance handled the original `POST /tickets` vs. which instance the client's websocket landed on. Verified live instead by literally running two instances (ports 8000 and 8001) and confirming a ticket enqueued via 8000 is pushed to a client connected on 8001 — see FLOW.md session log.
- **Files affected**: `app/realtime/pubsub.py`, `app/realtime/connections.py`, `app/main.py`.

### [2026-08-24] Heartbeat and disconnect detection both live inside one `asyncio.wait` loop, not separate tasks
- **Phase**: 6
- **Decision**: `watch_ticket`'s loop races `queue.get()` against `websocket.receive_text()` with a 30s `timeout`. If neither completes in time, a `{"type": "ping"}` heartbeat is sent and the loop repeats.
- **Alternatives considered**: a separate `asyncio.create_task` heartbeat loop running concurrently with the main receive loop (send a ping every 30s regardless of other activity) — simpler to read, but then cleanup requires explicitly cancelling a second task on every exit path (normal disconnect, terminal-state break, exception), and it sends a ping every 30s of wall-clock time even in the middle of active traffic, which is wasted chatter.
- **Reasoning**: a single `asyncio.wait(..., timeout=30)` naturally resets the heartbeat clock on any activity (a delivered update or a client message both count) and there's only one exit path to manage in `finally`. Verified live: connected to a ticket that was inserted directly into the DB without ever being enqueued (so it can never resolve) and confirmed exactly one `{"type": "ping"}` arrived at ~30s with no other activity.
- **Files affected**: `app/main.py`.

### [2026-08-24] Phase 6 end-to-end verification — live, not simulated
- **Phase**: 6
- **What was actually run**: found and killed two *stale* FastAPI/Celery processes left running from the prior (Phase 5) session, still bound to `127.0.0.1:8000` — they were silently answering `localhost` requests instead of the freshly-started process on `0.0.0.0:8000`, which produced a confusing `403` on the very first websocket test (Starlette/uvicorn's `websockets` implementation returns 403 when the ASGI app closes before ever accepting — here because the old process had no `/ws/tickets/{id}` route at all). Killed both stale PIDs, confirmed via `netstat` only the new process remained on 8000, then re-ran every test:
  1. Ticket submitted, websocket connected while it was still pending → resolution pushed live the moment the worker finished (`app/tasks/process_ticket.py`'s new `publish_ticket_update` call).
  2. Websocket connected *after* the ticket had already resolved → the immediate DB-read-and-push-on-connect path fired instead of waiting on a pub/sub message that would never come.
  3. A ticket inserted directly (never enqueued, so it never resolves) → confirmed exactly one `{"type":"ping"}` heartbeat at ~30s, connection stayed open.
  4. **The actual Day 5 milestone**: ran a second uvicorn instance on port 8001 alongside the original on 8000 (both subscribed to Redis independently via their own `lifespan`-started background task). Submitted a ticket through instance A (8000); connected the websocket client to instance B (8001) — a process that never touched the enqueue. The resolution still arrived on instance B, proving the Redis pub/sub fanout (not same-process luck) is what's doing the work.
- **Files affected**: none (verification record only) — see FLOW.md's session log for the full sequence.

### [2026-08-24] Bug fix: `finally` block's `websocket.close()` could raise `WebSocketDisconnect`, not just `RuntimeError`
- **Phase**: 6
- **Decision**: `contextlib.suppress(RuntimeError, WebSocketDisconnect)` around the cleanup `websocket.close()` call, instead of `RuntimeError` alone.
- **Reasoning**: found by running the app for real and reading the server log afterward — a client that hangs up abnormally at the same moment the server delivers the terminal update (e.g. the demo CLI client getting killed by an external `timeout` right as it received the resolution) leaves the connection in a state where Starlette's `close()` raises `WebSocketDisconnect(code=1006)` from inside `send()`, not `RuntimeError`. It didn't crash the server (FastAPI/Starlette scope this per-request), but it printed an unhandled-exception traceback for what is actually a benign, expected race — the client is gone either way, closing an already-gone connection isn't an error.
- **Files affected**: `app/main.py`.

---

### [2026-08-24] A dedicated least-privilege `app_role`, not the Supabase-provided `postgres` role, for the running app
- **Phase**: 7
- **Decision**: added `scripts/setup_app_role.py` — creates/rotates a Postgres role `app_role` (LOGIN, no special attributes) with grants scoped to the app's own tables, and writes its connection string to a new `APP_DATABASE_URL` env var. `DATABASE_URL` (the original Supabase-provided connection) becomes admin-only: schema/migrations (`scripts/apply_schema.py`) and ingestion (`scripts/ingest.py`).
- **Reasoning**: checked live before assuming anything (same discipline as every other provider/role fact in this log) — `SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user` on the existing `DATABASE_URL` connection returned `true`. A role with `rolbypassrls = true` ignores Row-Level Security entirely, `FORCE ROW LEVEL SECURITY` included — so every policy added below would have been silently inert if the running app kept using that connection. `app_role` has no such attribute (`rolbypassrls = false`, confirmed live), so the policies apply to it normally. The password is generated fresh (`secrets.token_urlsafe(24)`) and written straight into `.env`, never printed to stdout, so it doesn't end up in shell history or a captured log.
- **Files affected**: `scripts/setup_app_role.py` (new), `scripts/apply_schema.py` (new — see next entry), `app/db/session.py`, `.env.example`.

### [2026-08-24] `scripts/apply_schema.py` added — schema.sql was previously applied by ad hoc one-off commands each session
- **Phase**: 7
- **Decision**: a small script that reads `app/db/schema.sql` and executes it against `DATABASE_URL` in one call, `autocommit=True`.
- **Reasoning**: every earlier phase's schema change (Phase 2's tables, Phase 4's `search_vector` column, Phase 5's `tickets` table) was applied to the live Supabase DB via an inline one-off command during that session, not a reusable script — fine when schema.sql only grew occasionally, but Phase 7 adds a `tenants` table, two `ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY` statements, and two `CREATE POLICY` statements, and Phase 8 will add `semantic_cache` on top — enough recurring surface to be worth not re-deriving by hand every time. Confirmed live that psycopg3's `cur.execute()` accepts a whole multi-statement `;`-separated script in one call (falls back to the simple query protocol when no parameters are passed), so no manual statement-splitting was needed. Safe to re-run because every statement in schema.sql is already idempotent (`IF NOT EXISTS`, `DROP POLICY IF EXISTS`, `ON CONFLICT DO NOTHING`).
- **Files affected**: `scripts/apply_schema.py` (new).

### [2026-08-24] RLS policies on `knowledge_chunks` and `tickets`; `tenants` is the one table deliberately left unprotected
- **Phase**: 7
- **Decision**: `ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY` + one `CREATE POLICY ... USING (tenant_id = current_setting('app.current_tenant', true)) WITH CHECK (...)` per table, on `knowledge_chunks` and `tickets`. `tenants` itself gets no RLS — it's the global tenant directory (used to validate an incoming `X-Tenant-ID` before anything else), not per-tenant data, so scoping it to a tenant would make it impossible to look itself up.
- **Reasoning**: `current_setting('app.current_tenant', true)` — the `true` argument means "return NULL instead of raising" if the session variable was never set. `tenant_id = NULL` is never true in SQL (three-valued logic), so a connection that never calls `set_config` at all sees zero rows from every tenant, not just the "wrong" one — fail-closed by default, matching what `tests/test_tenant_isolation.py::test_no_tenant_set_at_all_sees_nothing` proves live. `WITH CHECK` (not just `USING`) matters too: without it, `app_role` could `INSERT`/`UPDATE` a row into a *different* tenant than the one its own transaction is scoped to, even though it could never read that row back afterward — `USING` alone only protects reads.
- **Files affected**: `app/db/schema.sql`.

### [2026-08-24] Bug fix: `SET LOCAL app.current_tenant = %s` doesn't work — Postgres's `SET` doesn't accept bind parameters
- **Phase**: 7
- **Decision**: `SELECT set_config('app.current_tenant', %s, true)` instead of `SET LOCAL app.current_tenant = %s`. The third argument to `set_config` (`is_local`) is what makes this `SET LOCAL`'s exact equivalent — scoped to the current transaction, not the whole session.
- **Reasoning**: found by actually running the new tenant-isolation test suite, not by inspection — `psycopg.errors.SyntaxError: syntax error at or near "$1"` on the very first call. Postgres's `SET`/`SET LOCAL` commands only accept a literal in that position; the extended query protocol's bind-parameter mechanism (`$1`) isn't valid there at all, regardless of client library. `set_config()` is a normal SQL function, so it takes parameters exactly like any other function call — this is the standard, documented way to parameterize a session/transaction-local GUC value in Postgres.
- **Files affected**: `app/db/session.py`.

### [2026-08-24] `X-Tenant-ID` header (default `"acme"`) is the tenant-resolution mechanism — no real auth, by design
- **Phase**: 7
- **Decision**: `POST /tickets`, `GET /tickets/{id}`, and `WS /ws/tickets/{id}` all read tenant identity from a plain `X-Tenant-ID` header (or `websocket.headers` for the websocket route), defaulting to `"acme"` when absent. Checked against the new `tenants` table (`_tenant_exists` in `app/main.py`) before anything else runs; an unknown tenant gets `400` (HTTP) or a `4400` close (websocket).
- **Alternatives considered**: real auth (API keys mapped to a tenant, JWT claims) — explicitly out of scope; BUILD_PLAN.md's Phase 7 description is about RLS storage-enforcement, not an auth system, and adding one would be scope creep this buildathon doesn't need. A header is enough to prove the multi-tenant *data* story (isolation, RLS) end-to-end without pretending there's real authentication where there isn't one.
- **Reasoning**: keeps every existing demo command from earlier phases working unchanged (no header → `"acme"`, same tenant every prior curl/websocket example already assumed), while making a second tenant (`globex`) fully reachable for anyone who does send the header. Verified live: a `globex`-scoped `POST /tickets` for a shipping question correctly came back `action="need_info"` with `citations: []` and `confidence: 0.2` — because `globex` has zero ingested `knowledge_chunks` (only `acme`'s KB was ever ingested) and RLS means it structurally cannot see `acme`'s rows to answer from, even though `acme`'s `shipping_policy.md` chunk exists in the very same table.
- **Files affected**: `app/main.py`, `scripts/watch_ticket.py` (added a `tenant_id` positional arg).

### [2026-08-24] Phase 7 end-to-end verification — live, not simulated
- **Phase**: 7
- **What was actually run**: `python -m scripts.apply_schema` (added `tenants` table + RLS to the live Supabase DB), `python -m scripts.setup_app_role` (created `app_role`, wrote `APP_DATABASE_URL` to `.env`), confirmed live that `postgres` has `rolbypassrls=true` and `app_role` has `rolbypassrls=false`. Ran `pytest tests/test_tenant_isolation.py` — all 4 tests green against the real database (found and fixed the `SET LOCAL` bug above in the process). Restarted the full stack (uvicorn + Celery) on the new `app_role` connection and re-verified the whole request path still works: `POST /tickets` → `202` → Celery → `resolve_ticket` → DB update → websocket push, for a real `acme` ticket. Then proved isolation at the *API* level, not just the DB level: `globex` requesting `acme`'s ticket 11 by ID got `404` (not `403` — doesn't even reveal the ticket exists); an unknown tenant slug got `400`; a `globex`-scoped websocket connection to `acme`'s ticket 11 was accepted (per Phase 6's accept-then-check design) then immediately closed with code `4404`; a real `globex` ticket resolved correctly through the full pipeline as `need_info` due to having no KB of its own (see the header-resolution entry above for why that's a good sign, not a bug).
- **Files affected**: none (verification record only) — see FLOW.md's session log for the full sequence.

---

### [2026-08-24] Semantic cache threshold: kept at BUILD_PLAN.md's 0.97, after live-measuring that it's much stricter in practice than "catches paraphrases" suggests
- **Phase**: 8
- **Decision**: `SIMILARITY_THRESHOLD = 0.97` in `app/cache/semantic_cache.py`, matching BUILD_PLAN.md's stated 0.97–0.98 range.
- **What was actually measured** (real `gemini-embedding-001` calls, not assumed): the guide's adversarial pair itself — `"refund after 14 days"` vs `"refund within 14 days"` — scored **0.9141**, comfortably below 0.97, so no threshold near that range risks a false collision. But several genuine paraphrases scored *lower still*: `"Can I get a refund?"` vs `"Is a refund possible?"` → 0.8526; `"My card was charged twice"` vs `"I was billed twice for one order"` → 0.8486. Even trivial non-semantic edits to the *same* question dropped well below 0.97: a bare case change → 0.9407, a mid-sentence extra space → 0.9275, trailing punctuation added → 0.8592. A single *trailing* space, by contrast, measured 0.9944 — and byte-identical text is the only case that reliably hits 1.0000.
- **What this means, reported honestly rather than spun**: at 0.97, this cache does not catch "the same question asked a different way" the way "semantic cache" might imply — it catches *near-exact resubmissions* (a retried webhook with slightly different whitespace/casing, a customer literally pasting the same message twice). That's still a real, useful cache (avoids redundant Groq/embed/rerank cost on exact repeats) — just narrower than the phrase suggests. Lowering the threshold to catch more paraphrases isn't safely possible without real risk: the gap between the adversarial pair (0.9141) and realistic near-duplicates (0.93–0.94) is too narrow to reliably separate "same intent, different wording" from "opposite intent, similar wording" on this embedding model at this corpus/query style. Kept at the guide's own specified value rather than tuning down for a higher hit rate that would reopen the exact false-collision risk Phase 8 exists to prevent.
- **Files affected**: `app/cache/semantic_cache.py`, `tests/test_semantic_cache.py` (positive test uses a trailing-space variant, not a paraphrase, to match what was actually verified to hit).

### [2026-08-24] Cache-eligibility gate lives in `app/pipeline.py`, not `app/cache/semantic_cache.py`
- **Phase**: 8
- **Decision**: `semantic_cache.store()`/`lookup()` are unconditional store/read primitives with no policy logic. The decision of *whether* a result is cacheable (`action == "resolve"` and `confidence >= CONFIDENCE_THRESHOLD`) lives in `resolve_ticket()`, right next to the pipeline's other business rules (the confidence-override, the empty-answer fallback).
- **Reasoning**: those are already the place this project keeps ticket-resolution policy, and `resolve_ticket` is the only caller of `semantic_cache.store()` — no second caller exists that would need the gate re-applied, so there's no duplication risk from keeping it there instead of pushing it down into the cache module. Verified live via `tests/test_semantic_cache.py::test_non_resolve_result_is_never_cached` — a real out-of-KB question run through the actual `resolve_ticket()` (Groq included, not mocked) correctly left nothing in the cache.
- **Files affected**: `app/pipeline.py`, `app/cache/semantic_cache.py`.

### [2026-08-24] Cache invalidation on KB change: `scripts/ingest.py` wipes `semantic_cache`, not just `knowledge_chunks`
- **Phase**: 8
- **Decision**: added `DELETE FROM semantic_cache` alongside the existing `DELETE FROM knowledge_chunks` at the top of `scripts/ingest.py`'s wipe-and-reload.
- **Reasoning**: matches BUILD_PLAN.md's Phase 8 requirement ("invalidate on KB document changes") — a cached resolution's citations point at `knowledge_chunks.id` values that a KB reload doesn't preserve (ingest deletes and re-inserts, so ids are never stable across a re-ingest), so a cached answer surviving a KB change could cite chunks that no longer exist, or worse, IDs now reused by different content. Same wipe-everything simplicity already chosen for `knowledge_chunks` itself back in Phase 2 (real incremental invalidation was explicitly deferred, not this project's problem to solve at buildathon scope) — no reason for the cache's invalidation story to be more sophisticated than the KB's own.
- **Files affected**: `scripts/ingest.py`.

### [2026-08-24] No TTL reaper — expiry is enforced lazily at lookup time (`WHERE expires_at > now()`), not by a scheduled cleanup job
- **Phase**: 8
- **Decision**: `semantic_cache` rows past their `expires_at` (24h TTL) are simply excluded by every lookup's `WHERE` clause; nothing ever deletes them proactively.
- **Reasoning**: correctness doesn't depend on rows being physically removed — an expired row is functionally invisible the moment `now() > expires_at`, matching how HTTP caches and most application-level TTL caches work. A cron/scheduled `DELETE FROM semantic_cache WHERE expires_at < now()` would only be about reclaiming storage, and at this project's scale (a handful of tenants, hundreds of cached resolutions at most) that's not worth a new scheduled-job mechanism for a buildathon-scoped system. Worth revisiting if this table's row count ever grows large enough that an ever-larger set of dead rows starts measurably slowing the unindexed `<=>` scan (see the next entry).
- **Files affected**: none (design decision, no code) — `app/db/schema.sql`'s `semantic_cache_expires_at` index exists to make this lazy-filter cheap, not to support a reaper.

### [2026-08-24] Discovered, not fixed: per-call `psycopg.connect()` overhead (~3.5s to Supabase) dominates the observed cache-hit speedup
- **Phase**: 8
- **What was found**: live-timed a full ticket submission twice with identical text — the first (cold) took 26.69s end-to-end (embed + hybrid retrieval + rerank + Groq); the second (cache hit, confirmed via the Celery log showing only one Gemini HTTP call and *no* Groq call or CrossEncoder rerank output) took 13.67s. A real, meaningful win — it skipped the reranker and the Groq completion entirely — but far short of "near-instant." Measured why directly: three bare `get_connection()` round trips (SELECT the ticket, the cache lookup itself, UPDATE the ticket) at ~3.5–3.6s each ≈ 10.5s, timed in isolation with a plain `SELECT 1`. That's most of the 13.67s, and it's not new to Phase 8 — every phase since Phase 2 has paid this same per-call cost (`app/db/session.py` opens a fresh physical TLS connection to Supabase on every single call, never pooled) — it was just previously hidden by the larger embed/rerank/Groq costs a cache hit now skips.
- **Why not fixed now**: fixing this means connection pooling (e.g. `psycopg_pool`), which is a cross-cutting change touching every caller of `get_connection`/`get_admin_connection`, not something scoped to "add a semantic cache." Flagging it here rather than silently letting "cache hit" be read as "instant" — same discipline as the Phase 5 `time_limit`-under-`--pool=solo` entry: a real, verified limitation, documented rather than glossed over or fixed as uninvited scope creep.
- **Files affected**: none (finding, not a code change).

### [2026-08-24] Phase 8 end-to-end verification — live, not simulated
- **Phase**: 8
- **What was actually run**: `python -m scripts.apply_schema` (added `semantic_cache` + RLS to live Supabase); confirmed the Phase 7 `ALTER DEFAULT PRIVILEGES` grant covered the new table automatically — `app_role` could `SELECT`/`INSERT`/`DELETE` on `semantic_cache` immediately with no re-run of `scripts/setup_app_role.py` needed. `pytest tests/test_semantic_cache.py` — all 3 tests green against the real DB and real Gemini/Groq APIs (one iteration caught a bad test fixture — see the threshold entry above — fixed before the suite passed). Restarted the full stack and submitted the same ticket text twice through the real `POST /tickets` → Celery → websocket path: first took 26.69s (full pipeline, resolved via KB citation), second took 13.67s and — confirmed via the Celery log's absence of a Groq HTTP call or CrossEncoder output — genuinely served from cache, not just faster by chance.
- **Files affected**: none (verification record only) — see FLOW.md's session log for the full sequence.

---

### [2026-08-24] Application-level `request_id`, not OTel's own `trace_id`, is what actually links the three hops
- **Phase**: 9
- **Decision**: `app/telemetry/tracing.py` generates a short `request_id` (`uuid4().hex[:12]`) at `POST /tickets`, threads it explicitly through every function call and message payload that crosses a process boundary (`process_ticket.delay(ticket_id, tenant_id, request_id)`, `publish_ticket_update(..., request_id)`), and logs it via a plain `log_event()` helper at each hop. Each hop *also* gets its own OTel span (API process, Celery worker process, and back in the API process for the websocket push), each carrying `request_id` as a span attribute — but they're three independent OTel traces, not one linked distributed trace.
- **Alternatives considered**: real distributed tracing via W3C traceparent propagation — inject the trace context into the Celery task's headers on `.delay()`, extract it in the worker, and again for the Redis pub/sub message. This is the "correct" production answer and is what a real Jaeger/Tempo setup would want.
- **Reasoning**: matches ONE_WEEK_PLAN.md's own cut-list ("basic OTel tracing... not a full Jaeger/Grafana dashboard stack") and BUILD_PLAN.md's actual Phase 9 verify step, which only asks to "trace one ticket end-to-end by correlation ID across all three hops" — a plain string threaded through function calls and logged at each hop satisfies that exactly, and is verifiable with `grep request_id=<id>` across three separate log files with zero extra infrastructure. Real trace-context propagation through Celery's broker is a legitimate next step but it's added complexity (Celery task headers, Redis message metadata) this buildathon's scope doesn't call for — noted here as the honest gap between "traced" and "OTel-distributed-traced," not glossed over.
- **Files affected**: `app/telemetry/tracing.py` (new), `app/main.py`, `app/tasks/process_ticket.py`, `app/realtime/pubsub.py`.

### [2026-08-24] `evals/test_eval.py`'s golden set uses `expected_action: "resolve" | "not_resolve"`, not a literal `"escalate"` vs `"need_info"` choice
- **Phase**: 9
- **Decision**: 18 hand-written Q&A pairs (`evals/golden_set.jsonl`), each with an `expected_action` of either `"resolve"` or `"not_resolve"` — the latter passes for *either* `action == "escalate"` or `action == "need_info"`.
- **Reasoning**: Phase 3's own DECISIONS.md entry already found that which of those two non-resolve labels the model picks isn't fully deterministic run-to-run (a legal-injury question got `escalate` on one run), and confidence itself "isn't a probability" (§04's callout, reconfirmed live in Phase 3). Grading against one specific label between two reasonable choices would make the eval flaky for reasons that have nothing to do with whether the system is actually safe — the property that matters for an accuracy gate is "did NOT incorrectly auto-resolve something it shouldn't have," which `!= "resolve"` captures precisely.
- **Files affected**: `evals/golden_set.jsonl`, `evals/test_eval.py`.

### [2026-08-24] Eval suite wipes its own tenant's semantic cache before running, so Phase 8's cache can't silently defeat Phase 9's accuracy gate
- **Phase**: 9
- **Decision**: an `autouse` module-scoped fixture in `evals/test_eval.py` runs `DELETE FROM semantic_cache WHERE tenant_id = 'acme'` before any golden-set case executes.
- **Reasoning**: found by reasoning about the interaction before it caused a real problem, not after — the semantic cache (Phase 8, 0.97 threshold, 24h TTL) means re-running this exact eval suite twice in the same day would serve the second run's identical questions from cache instead of exercising the live Groq/Gemini pipeline. An "accuracy gate" that's silently just replaying yesterday's cached answers on repeat runs isn't testing what it claims to test — it would pass even if a prompt or model regression shipped between runs, as long as the cache hadn't expired. Scoped to `acme` only (not a blanket cache wipe) so it doesn't touch other tenants' real cached data if this were ever run against a shared environment.
- **Files affected**: `evals/test_eval.py`.

### [2026-08-24] Environment gotcha found while testing, not a code bug: `localhost` resolved to `[::1]`, answered by an unrelated `wslrelay.exe`, not our uvicorn
- **Phase**: 9
- **What was found**: the very first post-restart smoke test (`curl http://localhost:8000/tickets/1`) returned a generic Starlette `{"detail":"Not Found"}` with an unfamiliar `x-request-id` header value that didn't match anything this app generates. `curl -v` showed it had connected to `[::1]:8000` (IPv6 loopback) — `netstat -ano` confirmed `wslrelay.exe` (WSL2's port-forwarding helper, unrelated to this project) was also listening on `[::1]:8000` for some other local dev service, while this session's own uvicorn was bound only to IPv4 `0.0.0.0:8000` (`--host 0.0.0.0` does not also bind the IPv6 wildcard on Windows). `curl`/browsers on this machine prefer IPv6 when `localhost` resolves both ways, so every `localhost`-addressed request was silently landing on the wrong service.
- **Decision**: use `http://127.0.0.1:8000` explicitly (not `localhost`) for all manual testing and demo scripts going forward this session, to remove the ambiguity. Not fixed at the uvicorn-binding level (e.g. `--host ::` for dual-stack) since that's an unrelated pre-existing local machine quirk (some other WSL-hosted dev tool happens to also use port 8000), not something to design the app's own bind address around.
- **Files affected**: none (a testing-tooling finding, not a code change) — same category as the Phase 6 stale-process discovery: verify the port is actually answering the process you think it is before trusting a confusing response.

### [2026-08-24] Phase 9 end-to-end verification — live, not simulated
- **Phase**: 9
- **What was actually run**: restarted the full stack with tracing wired in; submitted a real ticket, captured the generated `request_id` from the `202` response's `X-Request-ID` header, watched it resolve over the websocket, and confirmed the *same* `request_id` string appears in the correct order across all three independent log streams — API (`ticket_received` → `ticket_enqueued`), Celery worker (`task_started` → `task_completed`), and back in the API process (`websocket_push`) — satisfying BUILD_PLAN.md's Phase 9 verify step exactly. Ran `pytest evals/test_eval.py` alone (18/18 passed, real Groq+Gemini calls, ~6m10s) and then the full integration pass together — `pytest tests/ evals/test_eval.py` in one invocation (25/25 passed, ~7m36s) — per ONE_WEEK_PLAN.md Day 7's "all together, not in isolation" instruction. Then repeated the correlation-ID trace test across two live uvicorn instances (8000/8001) simultaneously with a cross-tenant submission (`globex`, which correctly has no KB of its own and got `need_info` again, consistent with the Phase 7 finding) — the same `request_id` traced correctly even when the create-hop and push-hop happened on physically different FastAPI processes.
- **Honest note on the 18/18 result**: not cited as "the system is 100% accurate" — this golden set was hand-written specifically to be reasonably clear-cut (each question closely matches exactly one KB doc's topic, or is unambiguously outside the KB entirely), which is a different, easier claim than "100% on real-world ticket variety." Same discipline as the Phase 4 retrieval-eval honesty entry: the eval suite existing and passing is real evidence the pipeline is wired correctly end-to-end; it is not evidence of the auto-resolution rate a production deployment would actually see.
- **Files affected**: none (verification record only) — see FLOW.md's session log for the full sequence.

---

*(Further entries land as coding continues into Phase 10 / week-1 wrap-up.)*
