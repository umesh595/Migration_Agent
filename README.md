# Enterprise Architecture Migration Agent

A conversational planning system that (1) builds a validated model of an existing
enterprise architecture through dialogue, (2) computes a dependency-aware migration
strategy, and (3) reviews that strategy with an auditable rules engine before
delivering the 10-deliverable migration package from the PoC brief.

**Governing principle:** the LLM *proposes and narrates*; deterministic code *decides
and persists*. Every consequential artifact — the architecture model, the migration
sequence, the review findings — is either computed by code or validated by code
before it exists.

Read [DECISIONS.md](DECISIONS.md) first if you're reviewing this: it records every
open question from the design docs that had to be answered before code could be
written, and why each was answered the way it was.

---

## Quick start

```bash
cp .env.example .env
```

Set `OPENAI_API_KEY` and `BOOTSTRAP_ADMIN_EMAIL`/`BOOTSTRAP_ADMIN_PASSWORD` in `.env`
(there is no self-service sign-up — see FR-A5 below — so the bootstrap admin is how
you get your first login), then:

```bash
docker compose up --build
```

The web app is at `http://localhost:3000`, the API at `http://localhost:8000`
(interactive docs at `http://localhost:8000/docs`). Migrations run automatically on
container start; the bootstrap admin account is created on first API startup if one
doesn't already exist.

If ports 3000 / 5432 / 6379 / 8000 are already taken on your machine, override them
in `.env` (`WEB_HOST_PORT`, `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `API_HOST_PORT`)
— no compose edit needed. If you change `API_HOST_PORT`, also update
`NEXT_PUBLIC_API_BASE_URL` to match — it's baked into the frontend's browser bundle
at build time, so `docker compose up --build web` after changing it.

### Running locally without Docker

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python run.py
```

Use `python run.py`, not bare `uvicorn app.main:app`. On Windows, uvicorn's default
ProactorEventLoop is incompatible with psycopg's async driver; `run.py` selects a
compatible loop per platform (see `app/platform_compat.py`). On Linux/macOS the two
are equivalent.

---

## Architecture

```
L4  API            FastAPI · REST + SSE streaming · JWT auth · Redis rate limiting
L3  ORCHESTRATION  LangGraph StateGraph · Postgres checkpointing · human gates
L2  DETERMINISTIC  PatchValidator/Applier · GapAnalyzer · GraphEngine (networkx)
    CORE           CoverageChecker · ReviewRulesEngine · PlanAssembler · Exporter
    (zero LLM)
L1  LLM GATEWAY    Provider abstraction · structured output · tiered retry · budget
L0  DATA           PostgreSQL 16 · Redis · Langfuse (optional)
```

Roughly 60–70% of the pipeline's decision work runs at **zero tokens**. LLM calls are
confined to four jobs: text→patches, gaps→questions, per-component planning, and
semantic critique.

### Session lifecycle

```
create session
  └─> DISCOVERY LOOP  (repeat until the model is right)
        user text ─> ingest (LLM: text → patches)
                  ─> apply_patches (code: validate, apply, audit, version++)
                  ─> gap_analysis (code: compute unknowns)
                  ─> generate_questions (LLM: top-3 gaps → contextual questions)
  └─> GATE 1: POST /model/accept ─> ModelVersion frozen as accepted
  └─> PLANNING
        elicit context (LLM) ─> compute_sequence (CODE: topo sort → waves)
                             ─> per-component planning (LLM, wave already fixed)
                             ─> assemble (code) ─> DRAFT
  └─> REVIEW  rules (code, 0 tokens) ─> LLM critic ─> refine loop (max 2)
  └─> GATE 2: POST /plan/approve ─> plan FINAL
  └─> EXPORT: markdown / docx with Mermaid diagrams
```

### Why migration order can't be wrong

`compute_sequence` (networkx topological sort) runs **before** any component-level LLM
call. Each component is handed to the LLM with its wave already fixed, and
`ComponentPlanLLMOutput` has no field through which the model could express ordering —
that omission is deliberate and enforced by a test. RULE-001 then re-verifies order on
the assembled plan. Dependency-order errors are prevented twice over.

---

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` · `/auth/refresh` | JWT auth (no self-service signup — FR-A5) |
| GET | `/auth/me` | Current user id/email/admin status |
| POST | `/auth/change-password` | Self-service password change (requires current password) |
| POST | `/admin/users` | **Admin** — provision a new account |
| GET | `/admin/users` | **Admin** — list accounts |
| PATCH | `/admin/users/{id}/active` | **Admin** — disable / re-enable an account |
| POST | `/admin/users/{id}/reset-password` | **Admin** — issue a new temporary password |
| POST | `/sessions` | Create a planning session |
| GET | `/sessions` | List the caller's own sessions (most recent first) |
| GET | `/sessions/{id}/state` | Current model, plan, and context |
| POST | `/sessions/{id}/messages` | A conversation turn (SSE stream) |
| POST | `/sessions/{id}/model/accept` | **Gate 1** — freeze the model |
| POST | `/sessions/{id}/plan/approve` | **Gate 2** — finalize the plan |
| GET | `/sessions/{id}/findings` | Review findings (rule + LLM) |
| GET | `/sessions/{id}/audit` | Every patch proposed, applied or rejected |
| GET | `/sessions/{id}/export?format=markdown\|docx` | The 10-deliverable package |
| GET | `/sessions/{id}/review-quality` | LLM-as-judge scores over the semantic critic's own findings |
| GET | `/health` · `/health/ready` | Liveness / readiness (+ tracing status) |

### Accounts (FR-A5 — no self-service registration)

The very first admin comes from `BOOTSTRAP_ADMIN_EMAIL`/`BOOTSTRAP_ADMIN_PASSWORD` in
`.env` (created once on startup, idempotent thereafter). Every other account is
created by an admin via `POST /admin/users` or the **Admin** page in the web app.

Gates are enforced against **persisted session status**, not graph state, so a
replayed or stale checkpoint cannot skip one.

### SSE resume semantics

Events carry an id equal to the completed graph node. A client reconnecting with
`Last-Event-ID` resumes from the last **completed node**, replayed from the Postgres
checkpoint. This is stage granularity, not token granularity — LangGraph checkpoints
at node boundaries, so an in-flight LLM call is never re-run to reconstruct partial
text the user already saw. See DECISIONS.md.

---

## The 10 deliverables

Every deliverable in the PoC brief maps to a typed field on `MigrationPlan`; the
exporter renders those fields and never generates fresh prose.

| # | Deliverable | Field |
|---|---|---|
| 1 | Current Architecture | `ArchitectureModel` |
| 2 | Target Architecture | `target_architecture_description` |
| 3 | Component Mapping | `component_mappings[]` |
| 4 | Component Migration Approach | `component_plans[]` |
| 5 | Migration Sequence | `waves[]` |
| 6 | Risks & Assumptions | `risks[]` + model `assumptions[]` |
| 7 | Validation Approach | `validation_summary` |
| 8 | Cutover Strategy | `cutover_strategy` |
| 9 | Rollback Strategy | `rollback_strategy` |
| 10 | Migration Roadmap | `roadmap_items[]` |

Deliverables 7 and 10 had no home in the original data model — they were given real
typed fields rather than being synthesized at export time (DECISIONS.md).

---

## Review rules (zero tokens)

| Rule | Checks | Severity |
|---|---|---|
| RULE-001 | Wave order never violates a dependency | error |
| RULE-002 | Every component has a mapping, plan, and wave | error |
| RULE-003 | Nothing retired while a non-retired component depends on it | error |
| RULE-004 | Plan-level and per-component rollback present | error |
| RULE-005 | Validation checks and cutover go/no-go criteria present | error |
| RULE-006 | Mapping and plan dispositions agree | error |
| RULE-007 | Cross-wave dependencies have a documented coexistence strategy | warning |

The LLM critic runs afterward and is explicitly told not to re-report any of these —
it only reports judgment-level problems a rule can't encode.

### AI critique quality (LLM-as-judge)

A second, independent model call scores the semantic critic's own findings —
never the rules above, which are already provably correct. Four dimensions
(relevance, specificity, actionability, context awareness) plus an overall score
and flagged issues, recorded per refine iteration and served at
`GET /sessions/{id}/review-quality`. **Diagnostic only — never gates the refine
loop or Gate 2.** A judge that scores an iteration poorly, or fails outright,
changes nothing about whether the plan can be approved; it's there so an
Engineering Director or Platform Admin can see, after the fact, whether the AI's
own review was actually any good. See DECISIONS.md ("PRD-bump overrides") for why
this exists — it accelerates PRD Decision Q7, deferred to v2, into v1.

---

## Frontend

`frontend/` is a Next.js (App Router, TypeScript, Tailwind) app: login, a session
dashboard, and a per-session workspace with the conversational chat panel, a React
Flow architecture canvas (plus a text-equivalent list view for accessibility), the
plan viewer for all ten deliverables, the findings panel, the two gate buttons, and
export downloads — plus an admin console for the account-provisioning endpoints
above.

```bash
cd frontend
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_BASE_URL at your API
npm run dev
```

SSE turn streaming is consumed via `fetch` + a hand-rolled `ReadableStream` reader
(`lib/api.ts:streamMessage`), not a browser `EventSource` — the backend serves it
over `POST` (so it can carry a body and an `Authorization` header), which
`EventSource` can't do natively anyway.

The chat panel's **Attach** button lets you paste or upload a text file
(`docker-compose.yml`, a Terraform summary, a README architecture section)
straight into a discovery turn — it's read into the message box and sent through
the exact same conversational ingestion path as anything typed by hand. This is
not an IaC parser or a cloud-account scanner (those remain explicit v1 Non-Goals
per the PRD); see DECISIONS.md.

## Testing

```bash
cd backend
pytest -q
```

97 tests, 90% coverage. The suite has four layers:

- **unit** — the deterministic core, security paths, config guards
- **eval/golden** — a scripted discovery transcript must reproduce an exact model,
  wave order, and a clean rules pass
- **eval/invariants** — adversarial tests of the structural guarantees: hallucinated
  component ids can't enter the model, hostile LLM output can't change wave
  assignment, no component can be silently dropped, token budget can't be exceeded
- **integration** — the real HTTP app end-to-end against real Postgres/Redis

**The LLM boundary is mocked throughout.** A merge-blocking CI gate that can fail
because a model phrased something differently gets muted within a month; mocking makes
red always mean "our logic broke." Integration tests skip cleanly if Postgres/Redis
aren't running.

To run integration tests locally:

```bash
docker compose up -d db redis
```

---

## Configuration

All configuration is environment-based (`.env`); see `.env.example` for the full list.

In `APP_ENV=production` the app **refuses to boot** on unsafe configuration: a
`JWT_SECRET` under 32 bytes, a placeholder secret, or wildcard CORS. Failing to start
is better than serving traffic with forgeable tokens.

Key settings:

| Variable | Default | Notes |
|---|---|---|
| `LLM_CHEAP_MODEL` | `gpt-4o-mini` | Ingestion, question generation |
| `LLM_STRONG_MODEL` | `gpt-4o` | Planning, review, strategy |
| `LLM_CHEAP_TIER_MAX_RETRIES` | `1` | Then escalates to the strong tier |
| `SESSION_TOKEN_BUDGET` | `1000000` | Hard per-session cap |
| `MAX_COMPONENTS` / `MAX_DEPENDENCIES` | `50` / `200` | v1 scale envelope |
| `MAX_REFINE_ITERATIONS` | `2` | Unresolved findings then ship as Risks |
| `RATE_LIMIT_RPM` / `RATE_LIMIT_MESSAGES_RPM` | `30` / `10` | Per user, shared via Redis |

**Provider portability.** OpenAI is the only wired provider (per project decision),
but the gateway is provider-agnostic: adding Groq or Anthropic means writing one class
implementing `LLMProvider` in `app/llm/providers/` — no changes to the gateway, graph
nodes, or prompts.

**Observability.** Langfuse tracing activates when `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` are set, and is a silent no-op otherwise. If it's configured but
fails to initialize, `/health/ready` reports `tracing.active: false` with the reason —
a broken observability config shouldn't look identical to a working one.

---

## Self-hosted observability (optional)

`docker compose up` never starts Langfuse — it's gated behind a Compose profile so
the default quick start stays fast. To run a real, fully self-hosted Langfuse
(dedicated Postgres, ClickHouse, Redis, MinIO, `langfuse-web`/`langfuse-worker`)
alongside the rest of the stack:

```bash
docker compose --profile observability up -d
```

`.env.example` documents every `LANGFUSE_*` variable this needs — generate real
secrets before using it (the placeholders are not safe defaults). The
`LANGFUSE_INIT_*` variables create the org/project/user and activate the given API
key pair on first boot — there is no setup-wizard step to click through. Set
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` in `.env` to the same values as
`LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`SECRET_KEY` and `LANGFUSE_HOST=http://langfuse-web:3000`
to have the `api` container start tracing into it immediately; the web UI is at
`http://localhost:${LANGFUSE_WEB_HOST_PORT:-3001}`, logged in as
`LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`.

Adapted directly from Langfuse's own published `docker-compose.yml`, not
reconstructed from memory — see DECISIONS.md for why this was previously left out
and what changed.

---

## Scale envelope

v1 handles up to 50 components and 200 dependencies. Beyond that, `compute_sequence`
rejects the model with a message directing you to split the estate into
subsystem-scoped projects. This is an enforced cap, not a guideline — a genuinely
large enterprise estate should be planned as several scoped sessions.

---

## Known limitations

- **The LLM-as-judge quality score is itself unverified against real-world human
  agreement.** It's a real, tested, non-blocking signal (DECISIONS.md, "PRD-bump
  overrides") — but nobody has checked whether a high judge score actually
  correlates with what a human migration architect would consider a good critique.
  Treat it as a diagnostic heuristic, not a validated metric.
- **The refine loop re-runs the LLM critic and the judge each iteration**, which
  costs tokens. Both are bounded at `MAX_REFINE_ITERATIONS`, but a cheaper "only
  re-critique changed components" pass would be a reasonable optimization.
- **The conversational config paste-in is not a parser.** A pasted Terraform file
  is read by the LLM as prose, the same as if you'd typed a description of it —
  quality of the resulting model depends on how legible the LLM finds the raw
  config, same as any other free-text input. It is explicitly not the v2-scoped
  automated IaC/cloud-account discovery.
- **Raising the 50-component/200-dependency scale cap needs real infrastructure
  load-testing**, not just a config change. The deterministic core is now verified
  fast at the current cap (`tests/eval/test_performance_at_scale_cap.py`), but the
  cap itself wasn't raised — see DECISIONS.md for why that's a deliberate,
  separate decision.
