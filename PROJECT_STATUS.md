# Project Status

A snapshot of what this project is, how it's currently configured, what was
verified, and what changed across this working session — last updated
2026-09-04. See [README.md](README.md) for the full feature/architecture
writeup and [DECISIONS.md](DECISIONS.md) for the historical design-decision
log; this file only covers what's true *right now* in this working copy.

---

## What this is

The Enterprise Architecture Migration Agent is a conversational planning
system: it builds a validated model of an existing enterprise architecture
through dialogue, computes a dependency-aware migration strategy, reviews that
strategy with a deterministic rules engine plus an LLM critic, and exports an
11-deliverable migration package (PDF/DOCX).

Stack:

| Layer | Tech |
|---|---|
| Frontend | Next.js 16 (App Router, TypeScript, Tailwind), React 18 |
| API | FastAPI, JWT auth, SSE streaming, Redis-backed rate limiting |
| Orchestration | LangGraph `StateGraph` with Postgres checkpointing |
| Deterministic core | Python — patch validator/applier, `networkx` graph engine, rules engine, plan assembler (zero LLM calls) |
| LLM gateway | Provider-agnostic — see "LLM provider" below |
| Data | PostgreSQL 16, Redis 7, optional self-hosted Langfuse |

---

## TL;DR — is it working?

**Yes.** 263 backend tests pass, the frontend builds/lints/typechecks clean,
and the full Docker stack boots healthy. The one thing that *looks* broken if
you actually use the app — every discovery message coming back as an
"UNPARSED" raw note instead of a real extracted architecture model — is not a
bug. It's `.env`'s `ANTHROPIC_API_KEY` still being the placeholder value, so
every LLM call 401s and the app's own designed fallback (never crash, always
record something) kicks in. See "Why chat responses look unhelpful right
now" below — that's the one thing you need to fix yourself (a real key) before
the core feature does anything.

---

## Verification pass — what was checked and the result

Ran cold, from a fresh clone with no `.env`, no Python venv, no
`node_modules`, and no Node.js/npm installed on the host at all.

| Check | Result |
|---|---|
| Backend `pytest` (`backend/`) | **263 passed**, 32 skipped (integration tests correctly skip without live Postgres/Redis), 7 deselected (`live_smoke` tests that need a real LLM key — excluded from default runs by design) |
| Alembic migration chain (`0001` → `0006`) | Clean `alembic upgrade head --sql` dry run, no ambiguous heads |
| `pip check` (backend deps) | No broken/conflicting requirements |
| Frontend `npm install` | Clean, no peer-dependency conflicts |
| Frontend `npm run lint` | 0 errors, 1 warning (`app/layout.tsx:36` — manual `<link>` stylesheet tag; harmless, pre-existing, not fixed since it's out of scope) |
| Frontend `npx tsc --noEmit` | 0 type errors |
| Frontend `npm run build` | Clean production build, all 12 routes compiled |
| `docker compose up --build` (db, redis, api, web) | All four containers build and reach healthy/ready |
| `GET /health`, `GET /health/ready` | Both green (`{"status":"ok"}`, redis/database both `"ok"`) |
| Live UI walkthrough (login → new session → send a discovery message) | Works end-to-end; see below for why the *content* of the response looks wrong right now |

**Conclusion: no code defects found in the original codebase.** The only real
gaps were environment setup (no `.env`, no Node.js on this machine, 79MB of
someone's local Postgres data accidentally committed to git — see below) —
none of these are bugs in the application code itself.

---

## Why chat responses look unhelpful right now

Sent a real discovery message through the running UI. Got back:

> I couldn't fully process that message through the usual extraction step, so
> I've recorded what you said as a raw note for now...

Checked the `api` container logs for the actual cause:

```
HTTP Request: POST https://api.anthropic.com/v1/messages?beta=true "HTTP/1.1 401 Unauthorized"
Anthropic API error: Error code: 401 - {'type': 'authentication_error', 'message': 'API key is invalid.'}
ERROR: ingest failed for session ...: node 'discovery.ingest' failed to produce schema-valid output after 4 attempts
```

**Root cause: `.env`'s `ANTHROPIC_API_KEY` is still the placeholder
`sk-ant-replace-me`.** Every LLM call 401s, the gateway retries 4 times
(cheap→strong escalation, exactly as designed), then gives up — and the
discovery node's own designed failure path kicks in: record the message as an
`UNPARSED` assumption rather than lose it or crash the turn. This is the app
behaving correctly under a bad key, not a defect. **Drop a real
`ANTHROPIC_API_KEY`** (and optionally `GEMINI_API_KEYS`/`GROQ_API_KEY` for the
fallback chain) into `.env`, then `.\scripts\dev.ps1 -Reload` (see below) to
make it live.

---

## Git hygiene fix

`git status`/`git ls-files` turned up **1,358 tracked files, 79MB**, that
should never have been committed: `.local_pgdata/` — the raw binary data
directory of a *native* (non-Docker) Postgres instance someone ran locally at
some point — plus `.local_logs/*.log`, `.local_pglog`, and
`frontend/tsconfig.tsbuildinfo` (a TypeScript incremental-build cache,
regenerated on every build, never meant to be versioned).

Fixed:
- [.gitignore](.gitignore): added `.local_pgdata/`, `.local_logs/`,
  `.local_pglog*`, `*.tsbuildinfo`, and `.local_run/` (this session's new dev
  script's PID/log directory).
- `git rm -r --cached` on all of the above — **this only untracks them going
  forward, it does not touch anything on disk.** The 79MB is still in this
  repo's git *history* (older commits) since rewriting history is a
  destructive, force-push-requiring operation not done without your explicit
  say-so. If you want that reclaimed too, that's a separate, deliberate
  `git filter-repo` (or BFG) pass — ask if you want it.
- These changes are staged/in the working tree, not committed — review with
  `git status` before committing.

---

## Dependency freshness pass

Backend (`pip list --outdated`) and frontend (`npm outdated`) were checked
against latest. Bumped everything that was a safe patch/minor version:

**Backend** ([requirements.txt](backend/requirements.txt),
[requirements-dev.txt](backend/requirements-dev.txt)): `uvicorn` 0.52.2→0.52.4,
`pydantic` 2.13.4→2.13.5, `psycopg[binary,pool]` 3.3.4→3.3.5, `openai`
3.0.0→3.8.0, `boto3` 1.43.86→1.43.88, `langfuse` 4.14.4→4.15.1,
`sse-starlette` 3.4.8→3.4.10, `python-dotenv` 1.2.2→1.2.3,
`python-multipart` 0.0.20→0.0.32, `ruff` 0.16.2→0.16.6. Backend base image
[Dockerfile](backend/Dockerfile) bumped `python:3.12-slim` → `python:3.14-slim`
(already verified against this exact codebase — the local dev venv used for
every backend test in this session ran on 3.14.6).

**Frontend** ([package.json](frontend/package.json)): `next` 16.3.1→16.3.4,
`@next/eslint-plugin-next` 16.3.1→16.3.4 (kept in lockstep with `next`),
`@xyflow/react` 12.3.6→12.11.6, `autoprefixer` 10.4.20→10.5.4, `postcss`
8.5.26→8.5.28, `typescript-eslint` 8.46.4→8.69.0. CopilotKit packages picked
up their existing `^1.70.0` range's latest patch (1.70.1) via a plain
`npm install`.

**Deliberately NOT bumped** — these are major-version jumps that need
dedicated verification this pass didn't have time for (a live API key, in
`anthropic`'s case, to test the beta structured-output surface actually
survives the jump):

| Package | Current | Latest | Why held back |
|---|---|---|---|
| `anthropic` (SDK) | 0.75.0 | 1.3.0 | `AnthropicProvider` calls the beta `beta.messages.parse` API directly — a major SDK bump could rename/remove it, and the LLM boundary is mocked in tests, so this needs a real `live_smoke` run to trust |
| `reportlab` | 4.4.4 | 5.0.1 | PDF export path not re-verified against the new major |
| `eslint` | 9.27.0 | 10.9.1 | This repo's `eslint.config.mjs` already carries an explicit workaround for an ESLint 9-specific crash — bumping past it needs its own pass |
| `tailwindcss` | 3.4.17 | 4.3.3 | v4 replaced the whole config system (no more `tailwind.config.ts`) — a real migration, not a bump |
| `typescript` | 5.7.2 | 7.0.2 | Two majors up — needs its own verification pass |
| `zod` | 3.25.76 | 4.5.4 | Breaking API changes vs v3 |
| `react` / `react-dom` | 18.3.1 | 19.2.8 | Framework major version — `@types/react`/`@types/react-dom` were left on the matching 18.x defs on purpose; bumping the types alone without the runtime would just introduce false type errors |

Verified after every backend bump: **263 passed** (`pytest -q`). Verified
after every frontend bump: lint clean (same 1 pre-existing warning), `tsc
--noEmit` clean, `npm run build` clean. Full Docker stack rebuilt and
re-verified healthy on `python:3.14-slim` + all bumped deps.

**Known, not fixed**: `npm audit` reports 9 vulnerabilities (4 low, 4
moderate, 1 high — `undici`, `qs`/`body-parser`/`express`, `@ai-sdk/*`), all
several levels deep inside `@copilotkit/runtime`'s *own* transitive
dependency tree, not something this project's `package.json` pins directly.
`npm audit fix` (non-force) makes no changes; forcing it would bump
CopilotKit's internal deps outside the ranges it was built against, which
risks breaking the runtime it ships — not applied. This is upstream
CopilotKit's problem to fix in a future release, not something to force-patch
around here.

---

## `devp` / `project.devprune.json`

This repo has real dependency bloat: `backend/.venv` (293 MiB) and
`frontend/node_modules` (912 MiB), both fully recoverable from their
lockfiles. Registered the repo with `dev-prune` and created
[project.devprune.json](project.devprune.json) (the team-shared, committed
config — `devp config project . --team`) with a `project_name`. `devp doctor .`
confirms both dependency trees are correctly detected and the repo config
parses clean. Nothing was pruned — that's a separate, explicit action
(`devp run . -y`) you'd run yourself when you actually want the disk back.

---

## LLM provider: Anthropic stays primary, Gemini added as a round-robin fallback tier

```
AnthropicProvider (primary, unchanged)
  └─ on quota exhaustion ─▶ GeminiProvider (round-robins across every key in GEMINI_API_KEYS, every call)
                              └─ on total exhaustion (every key quota-hit) ─▶ GroqProvider
```

Both `GEMINI_API_KEYS` and `GROQ_API_KEY` are optional — leave either unset to
skip that stage, same as Groq always worked. `AnthropicProvider` construction
and its required `ANTHROPIC_API_KEY` are untouched from the original codebase.

- **Round-robin, not failover-triggered**: every `complete_structured()` call
  advances to the next key in the list, spreading load evenly rather than
  hammering one key until it errors.
- **Per-key exhaustion handling**: if one key comes back `429
  RESOURCE_EXHAUSTED`, only that key is pulled from rotation — the call is
  retried (by `LLMGateway`'s existing retry loop) and lands on a different
  key. `ProviderQuotaExceededError` (the signal that trips the Groq fallback)
  is only raised once *every* configured Gemini key has been exhausted.
- Implemented in
  [backend/app/llm/providers/gemini_provider.py](backend/app/llm/providers/gemini_provider.py)
  using the `google-genai` SDK's native structured-output support
  (`GenerateContentConfig.response_schema` + `response.parsed`).

**Config (`.env`):**

```bash
ANTHROPIC_API_KEY=sk-ant-...        # required, primary — unchanged

# GEMINI_API_KEYS=key1,key2,key3    # optional — comma-separated, round-robins every call
GEMINI_CHEAP_MODEL=gemini-2.5-flash
GEMINI_STRONG_MODEL=gemini-2.5-pro

# GROQ_API_KEY=...                  # optional — third and final fallback
```

**Tests**: 4 new tests in
[backend/tests/unit/test_gemini_provider.py](backend/tests/unit/test_gemini_provider.py)
(rotation order, single-key exhaustion not tripping full fallback, total
exhaustion tripping `ProviderQuotaExceededError`, single-key trivial
rotation), plus updated `test_config.py` coverage for the new optional
`GEMINI_API_KEYS` field.

---

## Node.js: 20 → 24 (Docker), host has 26

- [frontend/Dockerfile](frontend/Dockerfile): both build stages now use
  `node:24-alpine` (confirmed `node --version` inside the built container:
  `v24.20.0`).
- [frontend/package.json](frontend/package.json): added `"engines": {"node":
  ">=24"}`.
- **Host machine**: had no Node.js at all before this session. Installed via
  `winget`, which defaulted to the "Current" channel (`26.7.0`) rather than
  the `24.x` LTS. Downgrading to exactly 24 needs a genuinely elevated
  (Administrator) shell this automation can't get non-interactively — you
  confirmed 26 is fine to keep. `>=24` in `package.json` is satisfied either
  way.

---

## Setup script: `scripts/dev.ps1`

One entry point instead of memorizing the docker-compose/venv/npm commands
separately, and the direct answer to "make a script that sets everything up
and still works when `.env` changes."

```powershell
.\scripts\dev.ps1                  # docker mode (default): build + start db/redis/api/web
.\scripts\dev.ps1 -Reload          # force every container to recreate and re-read .env
.\scripts\dev.ps1 -Stop            # docker compose down

.\scripts\dev.ps1 -Mode local          # db/redis via Docker; api via `python run.py`, web via `npm run dev`, as tracked background processes
.\scripts\dev.ps1 -Mode local -Reload  # restart those two tracked processes to pick up a .env edit
.\scripts\dev.ps1 -Mode local -Stop    # kill the tracked processes, stop db/redis containers
```

What it actually does:
- **Never overwrites an existing `.env`** or `frontend/.env.local` — only
  creates them from the `.example` templates the first time they're missing,
  generating a real `JWT_SECRET` in the process. Safe to re-run any time.
- **Picks up `.env` edits**: plain Docker `up --build` recreates a container
  only when its *image* changed — it can't tell that `.env`'s *contents*
  changed, since `env_file: .env` looks identical either way. `-Reload` adds
  `--force-recreate` so the new values actually take effect. (In practice,
  BuildKit's build-provenance metadata makes the api/web images look "new" on
  almost every `--build` anyway, so a plain re-run often picks up the change
  too — `-Reload` is the guaranteed way.)
- Prints the web/API URLs and the bootstrap admin login once `/health/ready`
  goes green, so there's no separate step to go look them up.
- **Verified live this session**: plain run, `-Reload`, and `-Stop` were all
  actually executed against the real stack (not just read) — including
  catching and fixing a real bug in the first draft (`$ErrorActionPreference
  = 'Stop'` was turning Docker's ordinary stderr progress output into a
  terminating error in Windows PowerShell 5.1 — removed in favor of explicit
  `$LASTEXITCODE` checks after each native call). `-Mode local` was verified
  by parsing (no syntax errors) but not executed end-to-end, since the ports
  it needs (3000/8000) were already held by the live Docker stack during
  testing.

---

## Running it

```powershell
.\scripts\dev.ps1
```

or directly:

```bash
docker compose up --build
```

- Web: http://localhost:3000
- API: http://localhost:8000 (docs at `/docs`)
- Bootstrap admin login: `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`
  from `.env` (defaults: `admin@example.com` / `change-me-after-first-login`
  — change this in `.env` before anything resembling production use)

Runtime versions confirmed inside the built containers this session:

| Component | Version |
|---|---|
| `web` (Node) | 24.20.0 |
| `api` (Python) | 3.14 (bumped this session — see "Dependency freshness pass") |
| `db` (Postgres) | 16.15 |
| `redis` | 7.4.11 |
| `google-genai` (pinned) | 2.22.0 |

Local backend dev loop (no Docker):

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements-dev.txt
pytest -q          # 263 passed, 32 skipped, 7 deselected — expected
```

---

## Known gaps (carried over from README, still true)

- The LLM-as-judge review-quality score is a diagnostic, not a validated
  metric against real human agreement.
- The refine loop re-runs the LLM critic + judge each iteration (bounded by
  `MAX_REFINE_ITERATIONS`, not optimized to re-critique only changed
  components).
- Conversational config paste-in is read as prose by the LLM, not parsed —
  quality depends on how legible the raw config is to the model.
- The 50-component/200-dependency scale cap is enforced but not
  load-tested past that point.
- `app/layout.tsx:36` has a manual stylesheet `<link>` tag ESLint flags
  (`@next/next/no-css-tags`) — pre-existing, cosmetic, not fixed in this pass
  since it wasn't part of the requested work.
- `npm audit`'s 9 vulnerabilities are all inside `@copilotkit/runtime`'s own
  transitive dependency tree (see "Dependency freshness pass") — not
  force-fixable here without risking breaking CopilotKit's runtime.
- 79MB of a native Postgres data directory is still present in this repo's
  git *history* (not the working tree, and no longer tracked going forward)
  — a `git filter-repo`/BFG history rewrite would be needed to actually
  shrink the `.git` directory, and wasn't done since it's destructive and
  rewrites shared history.
