# Locked Decisions — supersedes Doc 2 §6 open questions for this build

Every `[DEFAULT]` in `03-SYSTEM-DESIGN.md` needed a real answer before code could be
written without guessing twice. These are the answers this codebase implements. If any
of these are wrong for your actual deployment, say so before relying on the behavior —
each one is a config value or a swappable adapter, not a rewrite.

| # | Question | Decision | Why |
|---|---|---|---|
| Q1 | Model / provider | **OpenAI only** (per your answer). Cheap tier = `gpt-4o-mini` (ingestion, gap→questions). Strong tier = `gpt-4o` (per-component planning, semantic review critique). Both configurable via env, no code change needed to swap models. | You confirmed you only have OpenAI keys. Gateway is still provider-agnostic (`app/llm/gateway.py` + `app/llm/providers/`) — a Groq or Anthropic adapter is a new file implementing `LLMProvider`, not a rewrite. |
| Q3 | Auth | **Real multi-user JWT auth**, not a single-user seam. Signup/login, bcrypt password hashing, access+refresh tokens, `user_id` foreign key on every session-scoped table. | PRD FR-A1 lists auth as a v1-day-one requirement, and you asked for production-ready, not PoC-grade. A "seam" that isn't wired to anything is dead code in a production build. |
| Redis for rate limiting | **In, for v1.** Docker Compose includes Redis. Per-user token-bucket counter lives in Redis, shared across API replicas. | My own review flagged the contradiction: stateless horizontally-scaled replicas + per-user rate limits + "Redis optional" cannot all be true at once. Without a shared counter, a user hitting the limit on replica A doubles their real limit by hitting replica B. Resolved by making the shared store mandatory, not optional. |
| SSE resume granularity | **Stage/node-level resume, not token-level.** `Last-Event-ID` maps to the last completed LangGraph superstep, persisted via `PostgresSaver`. A disconnect mid-token-stream resumes narration from the last completed node output, replayed verbatim from the checkpoint — it does not re-run the LLM call or fabricate partial text. | LangGraph checkpoints at node boundaries. Promising token-granularity resume would be a contract the implementation cannot keep. This is the achievable, honestly-documented version. |
| Validation Approach / Migration Roadmap (PoC deliverables 7 & 10) | **Given real typed fields**, not synthesized as export-time prose: `MigrationPlan.validation_summary` (cross-component validation narrative + strategy, computed from `component_plans[].validation_checks`) and `MigrationPlan.roadmap_items[]` (ordered work-breakdown entries with wave, component, owner placeholder, effort_estimate — derived from `waves[]` + `component_plans[]` by code, not re-generated as fresh prose). | These two PoC deliverables had no schema home; leaving them as "assembled at export time" would mean the Exporter free-hands prose, violating technique #12 (rendered from typed plan, never re-generated). |
| Golden-fixture determinism | **LLM boundary is mocked for the CI golden/invariant/rule-regression suite.** A separate, explicitly-labeled `eval/live_smoke` tier (not run in default CI) hits real OpenAI calls at `temperature=0` for spot-checking prompt drift. | Non-deterministic model output in a merge-blocking CI gate gets muted within a month. Mocking the boundary makes "red blocks merge" mean something for the 95% of logic that isn't the LLM call itself. |
| Retry policy for structured output | **Differentiated by tier**: cheap tier (`gpt-4o-mini`, runs on every discovery turn) escalates to the strong-tier model after **1** structured-output validation failure, not 3. Strong tier keeps the uniform max-3 retry. | The PRD's uniform max-3 retry doesn't account for weaker models failing structured-output validation more often on the highest-frequency, hallucination-containment-critical node (patch generation). Fast escalation there is cheaper than 3 wasted cheap-tier calls plus a worse first impression. |
| Scale envelope | **50 components / 200 dependencies, enforced, with a clear rejection message** pointing at subsystem-scoped re-scoping (per the Edge Cases section) rather than a silent failure. | Kept as designed — this is a real cap, not a placeholder. (Pitch-language correction — tightening the Executive Summary framing — is a docs change, not a code change, and is out of scope for this build.) |
| Langfuse self-hosting | **SDK integration, no-op if unset — plus a real self-hosted stack, now included.** See "PRD-bump overrides" below; this row's original text (self-host deliberately left out) was superseded in that later pass. | — |

## Not decided here (explicitly out of scope for this pass)

- Executive Summary pitch-language tightening (docs-only, not code).

## Frontend build (this pass)

The prior pass shipped backend-only, by agreed scope, with the API fully documented
at `/docs`. This pass adds `frontend/` — Next.js 14→ upgraded to 16.3.1 during the
build (see below), React Flow canvas, chat panel, plan viewer, findings panel,
export buttons, and an admin console for the FR-A5 user-provisioning endpoints.

- **SSE consumption**: the backend serves turn streaming over a **POST** endpoint
  (`sse-starlette` on `/sessions/{id}/messages`), not a GET `EventSource`. A browser
  `EventSource` can't send a POST body or a custom `Authorization` header anyway, so
  the frontend reads the streamed response body directly via `fetch` + a manual
  `ReadableStream` reader that parses the `event:`/`data:` SSE framing by hand
  (`frontend/lib/api.ts:streamMessage`). This matches what the backend actually
  serves — no separate short-lived stream-token endpoint was needed because the
  PRD's EventSource-auth concern doesn't apply to a POST-based stream.
- **Canvas layout is presentation-only.** `ArchitectureCanvas` positions nodes by
  wave index once a plan exists (mirroring the backend's authoritative
  `compute_sequence` order for visual reinforcement) and falls back to a simple
  client-side topological layering before that, purely for a left-to-right reading
  order. Neither path feeds back into any backend decision — the governing
  principle (LLM/UI narrates, code decides) extends to "the UI doesn't even get to
  decide how something is *drawn* as authoritative," it only renders what the
  backend already computed.
- **Text-equivalent view.** The canvas has a "View as text" toggle rendering the
  same components/dependencies as plain lists, addressing the accessibility NFR
  (every diagram needs a text-equivalent) inside the app itself, not just in
  exports.
- **`is_admin` is never embedded in the JWT.** A role change must take effect
  without waiting for a stale token to expire, so the frontend calls `GET /auth/me`
  (added this pass) to learn admin status live rather than trusting a token claim.

### Next.js version bump: 14 → 16.3.1

`next@14.2.35` (the version originally planned per the PRD's "Next.js 14" line
item) carries multiple unpatched high-severity advisories (Server Actions SSRF/DoS,
cache-poisoning, middleware bypass — see `npm audit`) with no patched 14.x or 15.x
release; the fix is only available from 16.x. Confirmed the peer dependency for
React is still `^18.2.0` (no React 19 migration forced), bumped, and verified a
clean `tsc --noEmit` and `next build` afterward — this codebase uses no Server
Actions, custom server, or i18n middleware, so the specific vulnerable surfaces
aren't in use either way, but shipping a dependency with known unpatched CVEs in
something described as "production-ready" isn't defensible when a compatible fix
exists. `eslint`/`eslint-config-next`/`postcss` were bumped alongside it for the
same reason (`npm audit`: 0 vulnerabilities after).

## Correction (this pass): self-service signup contradicted FR-A5

The backend build that produced everything above this line had shipped a public
`POST /auth/signup` endpoint — but PRD FR-A5 is explicit: *"The system SHALL provide
admin endpoints to provision, disable, and reset users; self-service registration
SHALL NOT exist in v1."* A working signup endpoint is not a smaller version of that
requirement, it's the opposite of it. Fixed by:

- Removing `/auth/signup` entirely.
- Adding `is_admin` / `is_active` columns (migration `0002`) and a real `/admin`
  router: `POST /admin/users` (create), `GET /admin/users` (list),
  `PATCH /admin/users/{id}/active` (disable/enable), `POST /admin/users/{id}/reset-password`
  (reset) — all gated by `require_admin`.
- `current_user` now rejects a disabled account on every request, not just at login —
  otherwise a disabled user's still-valid access token would keep working for up to
  `ACCESS_TOKEN_TTL_MINUTES` after being disabled, which isn't what "disable" should mean.
- Solving the bootstrap problem (with no signup, how does the *first* admin ever get
  created?) via `BOOTSTRAP_ADMIN_EMAIL`/`BOOTSTRAP_ADMIN_PASSWORD`: on startup, if
  both are set and no admin exists anywhere yet, one is created (or an existing
  matching-email user is promoted). Idempotent — once any admin exists, it's a
  silent no-op even if the env vars are still set, so it's safe to leave configured
  in a long-running deployment rather than something you must remember to unset.

## PRD-bump overrides (this pass): closing the 4 gaps flagged as "genuinely left undone"

Two of the four items previously described as gaps were, on re-reading the PRD
closely, not oversights — they were the PRD's own explicit v2 deferrals:
LLM-as-judge quality scoring is Decision Q7 ("evaluation is golden fixtures plus
plan invariants plus rule regression; **LLM-as-judge quality scoring deferred to
v2**"), and automated cloud/IaC discovery is an explicit Non-Goal ("No automated
discovery from cloud accounts, IaC repositories, diagrams, or monitoring systems in
v1 — roadmapped for v2"). The PRD's own Constraints section requires "a PRD version
bump and impact review of the affected FR items" to override a locked decision —
this section is that bump, triggered by an explicit instruction to close these gaps
now rather than in v2. Overriding a boundary silently, without saying so, would be
worse than leaving it alone; this record is what makes the override legitimate
rather than a quiet scope-creep.

### 1. LLM-as-judge review quality scoring (Q7, accelerated from v2)

A new, independent LLM call scores the semantic critic's own findings — never the
deterministic rules, which are already provably correct and don't need judging.
`app/orchestration/nodes/review.py:judge_review_node` runs after `llm_review`,
scored across four dimensions (relevance, specificity, actionability, context
awareness) plus an overall score and flagged issues, persisted per refine iteration
(`review_quality_records` table, migration `0003`) and served at
`GET /sessions/{id}/review-quality`.

- **Non-blocking by design.** A judge failure (`StructuredOutputError`) degrades to
  a silent no-op — it must never break the review stage or gate approval. An
  unproven judge is an observability signal, not a gate; PRD Decision Q9's
  refine-loop bound and Gate 2 are unaffected by this call either succeeding or
  failing.
- **Told explicitly not to reward redundancy.** The judge sees the rule findings
  already fired (RULE-001..007) alongside the critic's findings and is instructed
  to score LOW any critic finding that merely restates a rule finding — otherwise a
  judge could rubber-stamp a critic that pads its findings list with
  already-covered mechanical issues.
- **An empty critique can score HIGH.** Per the existing `semantic_review` prompt's
  own instruction ("if you find nothing of substance, return an empty findings
  list"), the judge is told a correctly-empty critique on a clean plan is a good
  outcome, not a lazy one — otherwise this would perversely incentivize the critic
  to manufacture findings to avoid a low judge score.
- **Never part of the migration deliverable.** Surfaced only via the API/UI as
  "AI critique quality," not folded into the exported Markdown/DOCX package — it's
  meta-commentary on the AI's own output, not something a migration team needs in
  their runbook.

### 2. Self-hosted Langfuse (previously deliberately excluded — now included, verified)

`docker-compose.yml` gained a full, genuine self-hosted Langfuse stack (dedicated
Postgres 17, ClickHouse 25.12, Redis 7, MinIO, `langfuse-web`/`langfuse-worker` v4)
under the `observability` Compose profile — **off by default**; `docker compose up`
never starts it. Enable with `docker compose --profile observability up -d`.

Adapted line-for-line from Langfuse's own published `docker-compose.yml`
(github.com/langfuse/langfuse), fetched and verified rather than reconstructed from
memory, specifically to avoid shipping an "approximately right" version of someone
else's deployment topology — the exact failure mode the original exclusion decision
was worried about. What changed since that decision: dedicated service names
(`langfuse-db`, `langfuse-clickhouse`, `langfuse-redis`, `langfuse-minio`) to avoid
colliding with the app's own `db`/`redis`; all host ports made configurable env vars
consistent with the rest of this file (defaults picked to avoid colliding with the
app's own ports); every credential replaced with a generated secret in `.env`,
documented placeholders in `.env.example`.

**Headless initialization makes this genuinely one-command, not "mostly automated
except for a manual setup-wizard step."** Langfuse's `LANGFUSE_INIT_ORG_ID`,
`LANGFUSE_INIT_PROJECT_ID`, `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`,
`LANGFUSE_INIT_PROJECT_SECRET_KEY`, `LANGFUSE_INIT_USER_EMAIL`,
`LANGFUSE_INIT_USER_PASSWORD` (verified via Langfuse's own docs: "Headless
Initialization") create the org/project/user and activate the given API key pair
on first boot, with no UI step — confirmed by setting `LANGFUSE_PUBLIC_KEY`/
`LANGFUSE_SECRET_KEY` in `.env` to the exact same values as
`LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`SECRET_KEY`, then verifying `/health/ready`
reports `tracing.active: true` against the freshly-started stack with zero manual
steps taken in the Langfuse UI.

Postgres/Redis/ClickHouse/MinIO are dedicated instances, deliberately not shared
with the app's own `db`/`redis` — Langfuse manages its own Prisma-migrated schema
and mixing it into the app's SQLAlchemy/Alembic-managed Postgres invites migration
collisions for no benefit.

### 3. Conversational config paste-in (Non-Goals boundary respected, not silently ignored)

The PRD's Non-Goal — no automated discovery from cloud accounts, IaC repos, or
monitoring systems in v1 — is kept exactly as written. What's added is narrower and
stays inside it: a user can paste or attach a text file (docker-compose.yml, a
Terraform plan summary, a README architecture section) and it goes into the *same*
`ingest_patches` conversational pipeline as anything typed by hand — same LLM call,
same `PatchValidator`, same audit trail. Nothing parses IaC syntax; nothing calls a
cloud API. This is a "don't make the user re-type a file they already have open"
convenience, not a scanner.

- Backend: `MessageRequest.message` max length raised from 10k → 50k characters
  (`app/api/routers/sessions.py`) to fit a real config file; still a hard cap, not
  unbounded, so one message can't blow past a sane prompt size for the cheap-tier
  ingestion model.
- Frontend: an "Attach" button in `ChatPanel` reads a file client-side
  (`.txt/.md/.yml/.yaml/.tf/.json/.env`) and inserts its text into the message box;
  a client-side length check warns before hitting the server's 422.

### 4. Scale-cap performance verification (not a cap increase)

The Performance NFR ("deterministic nodes... under 100ms each at the scale cap")
was listed in the design doc as a "design target (to be measured, not assumed)".
`tests/eval/test_performance_at_scale_cap.py` builds a synthetic model at exactly
the v1 cap (50 components / 200 dependencies) and asserts wall-clock time for gap
analysis, sequencing, incremental patch application, rules review, and coverage
checking each stay under 100ms — turning an assumed target into a measured,
CI-enforced one.

**The cap itself (`MAX_COMPONENTS`/`MAX_DEPENDENCIES`) was deliberately NOT
raised.** Raising it without real load-testing against actual LLM latency — which
nothing in this repo can honestly simulate, since the LLM boundary is mocked
everywhere else specifically for determinism — would be an unverified promise,
which is worse than leaving the documented cap alone. The deterministic core
comfortably clears its target at the current cap; that's what got verified. Whether
a higher cap is safe is a separate question that needs real infrastructure
benchmarking, not a number bump.
