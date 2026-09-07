# Project Status

Reference doc for this repo's current state: what's verified, what changed
relative to the upstream project, and what's still open. See
[README.md](README.md) for the feature/architecture writeup and
[DECISIONS.md](DECISIONS.md) for the historical design-decision log. Update
this file alongside future changes rather than treating it as a one-time
snapshot.

---

## What this is

The Enterprise Architecture Migration Agent is a conversational planning
system: it builds a validated model of an existing enterprise architecture
through dialogue, computes a dependency-aware migration strategy, reviews that
strategy with a deterministic rules engine plus an LLM critic, and exports an
11-deliverable migration package (PDF/DOCX).

| Layer | Tech |
|---|---|
| Frontend | Next.js 16 (App Router, TypeScript, Tailwind), React 19 |
| API | FastAPI, JWT auth, SSE streaming, Redis-backed rate limiting |
| Orchestration | LangGraph `StateGraph` with Postgres checkpointing |
| Deterministic core | Python — patch validator/applier, `networkx` graph engine, rules engine, plan assembler (zero LLM calls) |
| LLM gateway | Provider-agnostic — Anthropic primary, Gemini/Groq fallback (see below) |
| Data | PostgreSQL 16, Redis 7, optional self-hosted Langfuse |

---

## Verified state

| Check | Result |
|---|---|
| Backend `pytest` (`backend/`) | 271 passed, 32 skipped (integration tests, need live Postgres/Redis), 7 deselected (`live_smoke`, need a real LLM key) |
| Alembic migration chain (`0001`→`0006`) | Clean `alembic upgrade head --sql` dry run |
| Frontend `npm run lint` | 0 errors, 1 pre-existing warning (`app/layout.tsx:36`, `@next/next/no-css-tags`) |
| Frontend `npx tsc --noEmit` | 0 type errors |
| Frontend `npm run build` | Clean production build, all 12 routes compile |
| `docker compose up --build` | All four containers (db, redis, api, web) build and reach healthy |
| `GET /health`, `GET /health/ready` | Both green |
| End-to-end discovery→planning walkthrough with a real Anthropic key | Discovery and Gate 1 acceptance work correctly (see "Findings from live testing" for issues found) |

No defects were found in the original application logic itself; the issues
below are either genuine bugs found through live use, or infrastructure/config
gaps (missing `.env`, no Node.js on a fresh host, accidentally-committed local
artifacts).

---

## Changes in this branch

### Added
- **Gemini LLM provider** — [backend/app/llm/providers/gemini_provider.py](backend/app/llm/providers/gemini_provider.py). Round-robins across every key in `GEMINI_API_KEYS` on each call; a key that comes back quota-exhausted is skipped for a time-bounded cooldown (not treated as total failure, and not permanently marked dead — see "PR self-review" #1). Slots in as an optional fallback tier behind Anthropic (primary, unchanged) and ahead of Groq. 6 tests in [backend/tests/unit/test_gemini_provider.py](backend/tests/unit/test_gemini_provider.py).
- **`scripts/dev.ps1`** — single entry point for bringing the stack up via Docker or locally, idempotent, never overwrites an existing `.env`, supports `-Reload` (force-recreate so `.env` edits take effect) and `-Stop`. Also creates the personal `.devprune.json` (dev-prune config) when the `devp`/`dev-prune` CLI is installed.
- **`project.devprune.json`** — registers the repo with [dev-prune](https://github.com/Life-Experimentalist/dev-prune) and declares `frontend/.next` (633 MiB, rebuilt via `npm --prefix frontend run build`) as a prunable directory alongside the auto-detected `backend/.venv` and `frontend/node_modules`.
- **`PROJECT_STATUS.md`** (this file).

### Modified
- **`.gitignore`** — added `.local_pgdata/`, `.local_logs/`, `.local_pglog*`, `*.tsbuildinfo`, `.local_run/`.
- **Untracked from git** (`git rm --cached`, files left on disk): `.local_pgdata/` (a native, non-Docker Postgres data directory — 79MB across 1358 binary files), `.local_logs/*.log`, `.local_pglog`, `frontend/tsconfig.tsbuildinfo`. None of these are meant to be versioned. The 79MB remains in git history; removing it from history entirely would need a separate, deliberate `git filter-repo`/BFG pass since that rewrites history.
- **`backend/app/config.py`, `backend/app/main.py`** — wired in the optional Gemini/Groq fallback chain behind Anthropic.
- **`backend/tests/unit/test_config.py`** — fixture updated for the new optional `GEMINI_API_KEYS` field.
- **`.env.example`, `README.md`** — document the Gemini fallback config and the current provider order.
- **`frontend/tsconfig.json`** — removed the deprecated `baseUrl` option (TypeScript 6/7 deprecation; unnecessary with `moduleResolution: "bundler"` and relative `paths`).

### Dependency and runtime version bumps

| Component | From | To | Notes |
|---|---|---|---|
| `backend/Dockerfile` base image | `python:3.12-slim` | `python:3.14-slim` | |
| `frontend/Dockerfile` base image | `node:20-alpine` | `node:24-alpine` | |
| `next` | 16.3.1 | 16.3.4 | |
| `@next/eslint-plugin-next` | 16.3.1 | 16.3.4 | kept in lockstep with `next` |
| `@xyflow/react` | 12.3.6 | 12.11.6 | |
| `autoprefixer` | 10.4.20 | 10.5.4 | |
| `postcss` | 8.5.26 | 8.5.28 | |
| `typescript-eslint` | 8.46.4 | 8.69.0 | |
| `react`, `react-dom` | 18.3.1 | 19.2.8 | peer deps confirmed compatible with `next`, `@copilotkit/*`, `@xyflow/react` before bumping |
| `@types/react`, `@types/react-dom` | 18.x | 19.2.18 / 19.2.7 | |
| `eslint` | 9.27.0 | 10.9.1 | `eslint-plugin-react-hooks` and `typescript-eslint` both confirmed to support 10.x first |
| `typescript` | 5.7.2 | 6.0.3 | **not** bumped to the latest 7.0.2 — `typescript-eslint@8.69.0` requires `typescript <6.1.0`, so 7.x would break linting; 6.0.3 is the newest version inside that constraint |
| `tailwindcss` | 3.4.17 | 4.3.3 | migrated via the official `@tailwindcss/upgrade` codemod (`tailwind.config.ts` → `@theme` in `app/globals.css`, `!util` → `util!`, `autoprefixer` → `@tailwindcss/postcss`) — the codemod also rewrote a vendored, pre-built CopilotKit stylesheet it should never have touched; that file was reverted to its original committed state, nothing else outside `app/`/`components/` changed |
| `zod` | ^3.25.76 | ^4.5.4 | not imported directly by app code — only a transitive dependency of CopilotKit's own stack, which declares `zod: >=3.25` as an open peer range |
| `uvicorn` | 0.52.2 | 0.52.4 | |
| `pydantic` | 2.13.4 | 2.13.5 | |
| `psycopg[binary,pool]` | 3.3.4 | 3.3.5 | |
| `openai` | 3.0.0 | 3.8.0 | |
| `anthropic` | 0.75.0 | 1.3.0 | verified `beta.messages.parse` keeps the same core signature (`model`/`max_tokens`/`system`/`messages`/`output_format`) that `AnthropicProvider` calls; full test suite passes against it |
| `boto3` | 1.43.86 | 1.43.88 | |
| `langfuse` | 4.14.4 | 4.15.1 | |
| `sse-starlette` | 3.4.8 | 3.4.10 | |
| `python-dotenv` | 1.2.2 | 1.2.3 | |
| `python-multipart` | 0.0.20 | 0.0.32 | |
| `ruff` | 0.16.2 | 0.16.6 | |
| `google-genai` | — | 2.22.0 (new) | Gemini provider dependency |

**Deliberately not bumped:**

| Package | Current | Latest | Why |
|---|---|---|---|
| `reportlab` | 4.4.4 | 5.0.1 | PDF export path not yet re-verified against the new major |

Every backend bump was re-verified with a full `pytest -q` run (271 passed).
Every frontend bump was re-verified with `npm run lint`, `npx tsc --noEmit`,
and `npm run build`.

**Known, not fixed**: `npm audit` reports 9 vulnerabilities (4 low, 4
moderate, 1 high — in `undici`, `qs`/`body-parser`/`express`, `@ai-sdk/*`),
all several levels deep inside `@copilotkit/runtime`'s own transitive
dependency tree, not something this project's `package.json` pins directly.
`npm audit fix` (non-force) makes no changes; forcing it would bump
CopilotKit's internal deps outside the ranges it was built against. This is
upstream CopilotKit's issue to resolve in a future release.

---

## LLM provider chain

```
AnthropicProvider (primary, unchanged)
  └─ on quota exhaustion ─▶ GeminiProvider (round-robins across every key in GEMINI_API_KEYS, every call)
                              └─ on total exhaustion (every key quota-hit) ─▶ GroqProvider
```

`GEMINI_API_KEYS` and `GROQ_API_KEY` are both optional — leave either unset to
skip that stage. `ANTHROPIC_API_KEY` remains required, exactly as in the
original wiring.

```bash
ANTHROPIC_API_KEY=sk-ant-...        # required, primary

# GEMINI_API_KEYS=key1,key2,key3    # optional — comma-separated, add as many keys as needed
GEMINI_CHEAP_MODEL=gemini-2.5-flash
GEMINI_STRONG_MODEL=gemini-2.5-pro

# GROQ_API_KEY=...                  # optional — third and final fallback
```

### Reconciled with a parallel upstream Gemini integration

Upstream `main` grew its own, independent Gemini integration while this
branch existed (single `GOOGLE_AI_STUDIO_API_KEY`, raw `httpx` calls, no
round-robin, Groq marked legacy/unwired) — a genuine merge conflict, not a
mechanical one. Decision (confirmed): keep this branch's design (the
`google-genai` SDK, multi-key round-robin, Groq kept as the third tier) —
but pull in one real improvement from the other side rather than discard
it: a new `ProviderRequestError` exception, distinct from a schema
validation failure, for a provider that's genuinely unreachable
(connection refused, timeout, a non-quota API error). `FallbackLLMProvider`
treats it the same as `ProviderQuotaExceededError` — a permanent switch —
which is sound, not risky: `LLMGateway`'s own retry budget has already been
exhausted by the time either exception reaches `FallbackLLMProvider` at
all, so a lone transient blip never gets this far in the first place.

Also adopted: `AnthropicProvider`'s quota-keyword check no longer requires
status code 429 specifically (a billing cutoff has been observed to also
surface as other status codes with the same wording in the body).

Fixed as a side effect of this reconciliation: `GeminiProvider.complete_structured`
only ever caught `google.genai.errors.APIError` — a genuine transport-level
failure would have propagated as a raw, unrecognized exception instead of
the `StructuredOutputError` subclass every caller is entitled to expect.
Now caught and reraised as `ProviderRequestError`.

**Frontend conflicts are still open** — upstream `main` also independently
built substantial new UI (a `.stat-tile`/`.chip-toggle` component system,
large changes to `ChatPanel.tsx`/`PlanViewer.tsx`) while staying on
Tailwind v3. This branch's Tailwind v4 migration touches nearly the same
files for syntax reasons alone, and even the smallest conflicting file
mixes a pure syntax change with a genuinely new upstream component — not
safely auto-resolvable in either direction without either discarding that
new UI work or undoing the v4 migration. Needs the migration re-applied on
top of the new components (not the other way around) once that work is
visible in full.

---

## Findings from live testing (real Anthropic key)

A full discovery→Gate 1→planning walkthrough was run against the live app
with a real Anthropic API key (not a mock). Results:

**Working well:**
- Architecture extraction from a free-text description is accurate: correct
  component identification, sensible dependency inference (direction and
  kind), and criticality tiers that respect explicit user instructions.
- The discovery follow-up questions are genuinely useful (payment handling,
  race conditions on inventory, data residency, auth, peak load, freeze
  windows) rather than generic.
- Vague/low-effort user input (e.g. "idk, just figure it out") is handled
  gracefully: the agent makes explicitly-labeled default assumptions instead
  of failing or silently guessing, and still surfaces the genuinely
  high-stakes remaining questions.
- A self-critique step ("requirement coverage critic") catches gaps the
  first-pass question generator misses (e.g. no rollback/contingency
  question for a stated near-zero-downtime requirement).
- Backend graph execution is decoupled from the client connection — a client
  disconnect during discovery does not stop the server from finishing and
  persisting that turn.

**Bugs found:**
1. **Garbled text fragment leaks into agent narration.** A raw fragment like
   `user user_technical_signal f` appears mid-sentence in the rendered agent
   response, reproducibly, across multiple turns. Looks like an internal
   field name or state marker being concatenated into user-facing text
   somewhere in the narration-assembly path.
2. **Trailing empty bullet in narration text lists** — the free-text
   narration's question list sometimes ends with a dangling `- ` (empty list
   item), separate from the structured `questions` array which renders
   correctly as its own UI cards.
3. **LangGraph checkpoint deserialization warnings** — every turn logs
   `WARNING:langgraph.checkpoint.serde.jsonplus:Deserializing unregistered
   type ... This will be blocked in a future version` for the app's own
   custom types (`ArchitectureModel`, `RequestIntent`, `RequestImpact`,
   `GeneratedQuestion`, `PatchOp`, `PatchOutcome`, `PatchResult`). A future
   `langgraph` upgrade that enforces `LANGGRAPH_STRICT_MSGPACK=true` by
   default would break resuming every existing checkpointed session. Fix is
   to register these types with LangGraph's serializer (or explicitly opt
   into `allowed_msgpack_modules`) before that happens.
4. **A disconnected SSE client appears to silently cancel in-flight planning
   work with no resumption and no error surfaced.** Observed once: a
   plan-generation turn was accompanied by a client-side timeout partway
   through; the session was left indefinitely in `planning` status with no
   plan, no error, and no stale lock in Redis (ruled out as the cause).
   If a real browser tab loses network mid-turn and hits the same path, the
   user has no way to know the turn needs to be resent rather than just
   still "in progress."
5. **No real progress feedback during multi-call turns.** A single discovery
   or planning turn issues several sequential/concurrent LLM calls (observed
   range: ~10s to 4+ minutes depending on API conditions and retry backoff).
   The UI only shows a static "Sending…" / "Updating the architecture
   model…" label with no step indicator or elapsed-time cue, which reads as
   hung well before it actually is.
6. **Per-component planning fires every component in a wave concurrently
   (`asyncio.gather` in `per_component_planning_node`,
   `backend/app/orchestration/nodes/planning.py:295`), and a real attempt on
   a 10-component, single-wave-heavy architecture failed for all 10
   components at once**, surfacing only a blunt `could not produce plans
   for: [...]. Try again or simplify those components.` with no diagnosis of
   *why*. Each component call independently retries up to the strong tier's
   budget (`StructuredOutputError` after exhaustion returns `None` for that
   component — see lines 283–285), but nothing throttles or staggers the
   burst of simultaneous calls a single wave produces, which is a very
   plausible way to trip a real account's rate limit and fail every
   component in that wave together rather than each in isolation. Retrying
   the exact same request later can succeed once the burst has cleared, but
   the app gives no indication that's the fix — a user just sees a generic
   failure and no path forward beyond "try again."

Discovery is solid end to end. Planning's extraction quality on the pieces
that do come back is good, but item 6 makes the planning stage itself
unreliable for anything beyond a small, single-wave architecture as
currently written — that, along with 1, 3, and 4, is worth fixing before
relying on this for real migration planning work.

---

## PR self-review

An 8-angle review pass (line-by-line, removed-behavior, cross-file call-site
tracing, reuse, simplification, efficiency, altitude, and CLAUDE.md
conventions) against this branch's own diff, each candidate independently
verified — including two live reproductions against the real classes and one
empirical browser check — before being fixed. All ten survived verification;
all ten are fixed on this branch.

| # | Finding | Fix |
|---|---|---|
| 1 | `GeminiProvider` treated every 429 as permanent quota exhaustion, but Gemini can't distinguish a transient per-minute rate limit from real billing exhaustion the way every sibling provider's disambiguation logic requires | Per-key exhaustion is now time-bounded (`_exhausted_until`, 60s cooldown) instead of a permanent set — a key rejoins rotation on its own once its cooldown passes; `ProviderQuotaExceededError` only fires when every key is in cooldown *right now* |
| 2 | `gemini_api_keys` was a plain `list[str]`, unlike every other credential field (`SecretStr`) — `repr()`/logging leaked the raw keys | Changed to `list[SecretStr] \| None`; `main.py` unwraps with `.get_secret_value()` at the one point it's needed |
| 3 | The new 3-tier fallback chain can need two provider switches to reach the last tier, but the cheap-tier retry budget was sized for one — could silently escalate cheap-tier calls to the strong tier | `main.py` now adds one extra cheap-tier retry per configured optional fallback tier |
| 4 | This doc's own "deliberately not bumped" table still listed `tailwindcss`/`zod` as unapplied after later commits in the same PR bumped both | Fixed here — moved to the applied-bumps table |
| 5 | The Tailwind codemod produced a spec-invalid self-referencing `--font-display` CSS variable in `app/globals.css` (both Tailwind's `@theme` token and `next/font`'s loaded-font variable happened to be named `--font-display`) | Verified live it wasn't currently breaking anything (`next/font`'s declaration wins the cascade — computed `font-family` correctly resolved to `Outfit`), but fixed anyway since it's a latent hazard — renamed `next/font`'s own variable to `--font-outfit` (`app/layout.tsx`) so it no longer collides; Tailwind's `.font-display` utility class name is unchanged, no component edits needed |
| 6 | `scripts/dev.ps1` local mode: a crashed backend/frontend process left a stale PID file that silently blocked the next restart | Added a liveness check (`Get-Process -Id`) before trusting an existing PID file; a dead process's stale file is now cleaned up and the process restarted automatically |
| 7 | `config.py`'s `_split_gemini_keys` duplicated `_split_origins` almost verbatim | Extracted a shared `_split_csv` helper |
| 8 | `GeminiProvider`'s round-robin index counter grew unboundedly instead of staying bounded | Normalized modulo `n` at the point of increment |
| 9 | `main.py`'s fallback-chain construction was hand-nested ternaries that don't scale to a future 4th provider | Rewritten as a right-to-left fold over an ordered list of optional providers |
| 10 | `scripts/dev.ps1` local mode ran backend/frontend installs unconditionally and sequentially | `pip install` now gated the same way `npm install` already was (skipped unless missing or `-Reload`); both installs now run in parallel via background jobs |

Verified after every fix: full backend `pytest -q` (271 passed — 5 new tests
added for these fixes plus everything already there), frontend
lint/typecheck/build clean, and a full Docker stack rebuild reaching healthy.

**Eleventh fix found through this pass's own testing, not the 8-angle
review**: actually running `-Mode local` end to end for the first time (it
had only been syntax-checked before) surfaced that it never worked at all —
`.env`'s `DATABASE_URL`/`REDIS_URL` point at the Docker-internal `db`/`redis`
hostnames, which only resolve inside the Docker network, not from a
`python run.py` started directly on the host. Fixed: `dev.ps1` now builds
the equivalent `localhost:<published-port>` URLs itself (from `.env`'s own
`POSTGRES_*`/`*_HOST_PORT` values, mirroring `docker-compose.yml`'s own
override) and sets them for the current process and everything it spawns,
without touching `.env` itself.

Verification of that fix was then blocked by something unrelated to it: a
**stray native `postgres.exe`** already squatting on host port 5432 (a
leftover from whatever produced the `.local_pgdata`/`.local_pglog` files
removed from tracking earlier in this branch — see "Git hygiene fix"),
silently intercepting connections meant for the Docker container. Confirmed
via `Get-NetTCPConnection`/`Get-CimInstance`; stopping it was attempted and
refused with "Access is denied" — the same non-elevated-token limitation
noted under "Node.js: 20 → 24" below. **If `-Mode local` fails with a
Postgres password/auth error on this machine, check `Get-NetTCPConnection
-LocalPort 5432` for a process that isn't `com.docker.backend.exe` and stop
it from an elevated shell first.**

---

## Setup script: `scripts/dev.ps1`

```powershell
.\scripts\dev.ps1                       # docker mode (default): build + start db/redis/api/web
.\scripts\dev.ps1 -Reload               # force every container to recreate and re-read .env
.\scripts\dev.ps1 -Stop                 # docker compose down

.\scripts\dev.ps1 -Mode local           # db/redis via Docker; api via `python run.py`, web via `npm run dev`
.\scripts\dev.ps1 -Mode local -Reload   # restart those tracked processes to pick up a .env edit
.\scripts\dev.ps1 -Mode local -Stop     # kill the tracked processes, stop db/redis containers
```

- Never overwrites an existing `.env` / `frontend/.env.local` — only creates
  them from the `.example` templates when missing, generating a real
  `JWT_SECRET`.
- `-Reload` force-recreates containers so `.env` edits actually take effect
  (a plain `docker compose up` can't detect that `env_file`'s *contents*
  changed, only that the reference is unchanged).
- Prints the web/API URLs and bootstrap admin login once `/health/ready`
  goes green.
- Creates the personal `.devprune.json` on first run if `devp`/`dev-prune`
  is on `PATH`; silently skipped otherwise.

---

## Running it

```powershell
.\scripts\dev.ps1
```

or directly: `docker compose up --build`.

- Web: http://localhost:3000
- API: http://localhost:8000 (docs at `/docs`)
- Bootstrap admin login: `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`
  in `.env`

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements-dev.txt
pytest -q          # 271 passed, 32 skipped, 7 deselected — expected
```

---

## Known gaps

- The LLM-as-judge review-quality score is a diagnostic, not a validated
  metric against real human agreement.
- The refine loop re-runs the LLM critic + judge each iteration (bounded by
  `MAX_REFINE_ITERATIONS`, not optimized to re-critique only changed
  components).
- Conversational config paste-in is read as prose by the LLM, not parsed.
- The 50-component/200-dependency scale cap is enforced but not
  load-tested past that point.
- `app/layout.tsx:36` has a manual stylesheet `<link>` tag ESLint flags
  (`@next/next/no-css-tags`) — pre-existing, cosmetic.
- `npm audit`'s 9 vulnerabilities live entirely inside `@copilotkit/runtime`'s
  transitive dependency tree — see "Dependency and runtime version bumps."
- 79MB of a native Postgres data directory remains in git history (no longer
  tracked in the working tree) — reclaiming that needs a separate history
  rewrite.
- See "Findings from live testing" for the six concrete application bugs
  found via a real end-to-end run (distinct from the PR-review findings on
  this branch's own new code, listed under "PR self-review" below).
