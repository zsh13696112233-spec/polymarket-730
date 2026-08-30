# PolyCopy Repository Instructions

These instructions apply to the entire repository. If a nested directory later adds its own
`AGENTS.md` or `AGENTS.override.md`, follow the more specific file for work in that subtree.

## Project overview

PolyCopy is a local Polymarket on-chain monitoring and trading console. It detects whale activity,
tracks positions and signals, and supports previewed, explicitly confirmed live trading through a
separate execution wallet.

- The web application is a Next.js 16 / React 19 / TypeScript application under `app/`.
- The web build and local runtime use vinext, Vite, and a Cloudflare Worker entry point in
  `worker/index.ts`.
- The API is an asynchronous Python 3.12 FastAPI application under `backend/`.
- Persistence uses SQLAlchemy async sessions, SQLite by default, and Alembic migrations under
  `backend/alembic/versions/`.
- Frontend tests live in `tests/`; backend tests live in `backend/tests/`.
- Design and implementation notes live in `docs/`. Treat them as useful context, but verify current
  behavior against the code and tests.

## Toolchain and setup

- Use Node.js 22.13 or newer, `npm`, Python 3.12 or newer, and `uv`.
- Respect both lockfiles: `package-lock.json` and `uv.lock`.
- Install dependencies with:

  ```bash
  npm ci
  UV_CACHE_DIR=.uv-cache uv sync
  ```

- Copy `.env.example` to `.env` only when local runtime configuration is needed. Never overwrite an
  existing `.env` file.
- Start the normal local stack with `npm run dev:all`. The frontend listens on port 3000 and the API
  on port 8730 by default.
- Do not introduce another package manager, build system, formatter, or test runner unless the user
  explicitly requests it.
- Do not add or upgrade dependencies speculatively. If a dependency change is necessary, explain
  why and update the corresponding lockfile.

## Architecture and coding conventions

### Frontend

- Keep routes in `app/**/page.tsx`, shared UI behavior in `app/components/`, and global styling in
  `app/globals.css`.
- Follow the existing TypeScript and React style and let TypeScript, ESLint, and Vitest define the
  enforceable rules.
- Preserve the existing Chinese product language for user-facing copy unless the task explicitly
  changes the product language.
- Reuse established API helpers and shared types where they already fit. Keep frontend request and
  response types aligned with the FastAPI schemas.
- Preserve visibility-aware polling behavior: do not add overlapping requests or background polling
  while the document is hidden.
- When changing rendered pages or Worker behavior, remember that `npm run test:web` includes a
  production build and the rendered-HTML test in addition to unit tests and type checking.

### Backend

- Keep FastAPI wiring and HTTP concerns in `backend/main.py`, request/response contracts in
  `backend/schemas.py`, database models in `backend/models.py`, Polymarket API access in
  `backend/polymarket.py`, and trade submission logic in `backend/trading.py` and `backend/whale.py`.
- Use async I/O consistently. Do not add blocking network or database work to the event loop; follow
  existing `asyncio.to_thread` boundaries when a synchronous library must be called.
- Inject `Settings` and external clients in tests. Do not make unit tests depend on the live
  Polymarket APIs, SMTP servers, macOS Keychain, or a developer's local database.
- Use `Decimal`, not binary floating point, for prices, balances, order sizes, fees, and P&L. Preserve
  explicit rounding, tick-size, and minimum-order rules.
- Use `backend.time_utils.utcnow()` and preserve the repository's established UTC database timestamp
  convention. Do not casually mix timezone-aware and naive values.
- Keep API errors actionable without leaking credentials, signing material, or upstream secrets.
- Preserve the preview/confirm/execute boundary for live actions. Confirmation IDs must remain
  short-lived, single-use, validated against the previewed action, and protected by persistent
  idempotency where applicable.
- Preserve fail-closed behavior when credentials, authorization, balance, market data, or settlement
  state is missing or contradictory.

### Database migrations

- Make schema changes through a new Alembic revision. Update models, schemas, application logic, and
  tests together when the contract changes.
- Do not rewrite or renumber an existing migration that may already have been applied unless the user
  explicitly requests a migration-history repair.
- Preserve user data. Avoid destructive migrations; when one is genuinely required, state the data
  impact and provide a migration or recovery path before implementation.
- Exercise both fresh-database creation and upgrade-from-prior-schema behavior for migration changes.

## Safety and data integrity

- Treat all order placement, selling, redemption, allowance, wallet, SMTP, and scanning operations as
  potentially real. Do not trigger them against live services unless the user explicitly asks for
  that exact external action.
- For local runtime verification that does not require live behavior, disable it with
  `POLYMARKET_TRADING_ENABLED=0`, and disable background monitoring with
  `POLYMARKET_START_MONITOR=0` when appropriate.
- Never read, print, log, commit, or return private keys, Builder API secrets, passphrases, full signed
  payloads, SMTP authorization codes, or macOS Keychain contents.
- Keep private keys and Builder credentials in macOS Keychain. The database and API may store only
  non-secret Keychain service/account references.
- Never commit `.env`, database files, WAL files, caches, logs, or generated build output. The
  existing `.gitignore` is part of this safety boundary.
- Do not delete or rewrite the local `data/` database as a troubleshooting shortcut. Use temporary
  databases for tests and reproduction.
- Do not weaken live-trading guards, confirmation text checks, spending limits, exposure limits,
  slippage bounds, idempotency, or reconciliation behavior merely to make a test pass.

## Verification

Use the narrowest relevant checks while iterating, then run checks proportional to the final change.

- Frontend unit tests: `npm run test:web:unit`
- Type checking: `npm run typecheck`
- Backend tests: `UV_CACHE_DIR=.uv-cache uv run pytest backend/tests`
- A targeted backend test may be run by passing its file or node ID to `pytest`.
- Full test suite: `npm test`
- All configured lint and formatting checks: `npm run lint`

For a bug fix, add or update a regression test that fails for the original behavior. For an API or
schema change, verify both backend contract tests and the affected frontend tests. For a migration,
verify migration-specific tests as well as the application path that consumes the new schema.

Do not claim a check passed unless it was actually run. If a required check cannot run, report the
exact command, the failure or blocker, and what remains unverified.

## Change discipline

- Keep diffs focused. Do not modify generated output, dependency locks, migrations, or documentation
  unless the requested change requires it.
- Preserve currently supported behavior unless the task explicitly changes it.
- Do not resurrect the retired fixed-wallet polling or fixed-wallet auto-copy strategy. Future
  automation should consume the current on-chain monitoring signals and reuse the existing execution
  boundary.
- When behavior changes, update the closest relevant test and any README or design documentation that
  would otherwise become misleading.
- Before handoff, inspect `git diff` and `git status` so unrelated user changes are not included or
  overwritten.

---

# Behavioral guidelines to reduce common LLM coding mistakes

Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
