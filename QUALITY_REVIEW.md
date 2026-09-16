# Fact decomposition and website quality review

**Current status: all six in-scope fixes implemented and verified.** See implementation results at the end. Earlier findings and limitations are retained as review history.

## Scope and method

- Review requested: structural simplicity, maintainability, overtesting, and unjustified release gates using quality-review.
- Base: local `origin/main`; head at start: `b96bc0d` (`codex/decomposition-eval`). Includes staged/unstaged tracked changes and untracked product component `GradingInstructions.tsx`.
- User-confirmed scope: fact decomposition generation, import/export, authoring and grading website flows, their tests, and shared website checks that affect them. Retrieval implementation and retrieval-specific tests are excluded because they are in progress.
- The original base diff contains 338 files; that inventory does not define the narrowed review scope.
- Local research/reference copies, data, and editor configuration are context, not proposed product code. Generated clients/locks are checked as contracts rather than hand-authored architecture.
- The review phase left implementation untouched. The user subsequently authorized the six in-scope fixes; implementation results are recorded below.
- Read root/web instructions, STYLE.md, quality-review backend/frontend references, component READMEs, frontend architecture, operations docs, CI and hooks.

## Coverage ledger

- [x] Scope, repository conventions, build/test entry points.
- [x] Decomposition generation and import contracts.
- [x] Fact decomposition review/authoring lifecycle and persistence.
- [x] Fact decomposition frontend state and selection ownership.
- [x] Tests: meaningful coverage versus redundant/implementation assertions.
- [x] CI/release gates and targeted verification.

## Findings

Initial checkpoint: no confirmed findings. The final prioritized findings appear below; interim notes are retained to document the review.

## Verification and limitations

Initial checkpoint only. See final verification below for completed checks and remaining limits.

## Checkpoint 1

- Focused generation/import/grading tests: **105 passed in 6.87s**.
- Confirmed advisory candidates: migration drift duplicated in CI and pytest; bootstrap tests assert mocked plumbing; model browser journey repeats API-only import assertions and combines unrelated workflows; test-only fact-authoring commit wrappers.
- Structural candidate under investigation: FACT_DECOMP artifact claim/span/generation contracts copied into producer, importer, and stored metadata. Distinct process boundaries justify validation, but not necessarily independent definitions. Need concrete drift before assigning blocking priority.
- Fact-authoring recovery receipts have real callers and meaningful failure tests; do not recommend removing these mechanisms merely for size.
- Browser review currently source-based; no browser execution yet.

## Checkpoint 2

- Scope correction: retrieval observations are excluded from findings and the verdict at the user’s request.
- Confirmed duplicated execution: `web/frontend/package.json` check already runs unit tests, then CI and pre-commit run them again. CI migration drift duplicates `test_alembic_drift.py`.
- Confirmed false-confidence unit surface: `responseSpanFromCodeUnitRange` is called only by its unit test; real DOM selection uses `sourceOffset` and `responseSpanFromCodePointRange` instead.
- Confirmed CI coverage gap: PydanticAI is only in the datasets optional extra; CI installs default dev dependencies, while agent/run/runtime test modules use importorskip. Existing local environment did run these successfully.
- Frontend unit execution unavailable: `bun run test:unit` returned exit 127 (`bun: command not found`). Browser/build verification not performed. Do not interpret source inspection as browser verification.
- Full backend suite now running to check adjacent workflows.

# Review findings (before implementation)

**Review verdict before implementation: approve with advisory improvements within the narrowed scope.** Six P2 findings remain; no supported P0 or P1 findings remain in scope. The earlier blocking verdict concerned retrieval and is withdrawn from this review. Frontend execution remains unverified because Bun is unavailable. No additional supported in-scope P2 findings are omitted.

## 1. P2 — Run each identical check once per CI/hook invocation

**Location:** [pytest.yml:164](/workspaces/amfv/.github/workflows/pytest.yml:164), [package.json:10](/workspaces/amfv/web/frontend/package.json:10), and [.pre-commit-config.yaml:37](/workspaces/amfv/.pre-commit-config.yaml:37).

`web`'s `check` delegates to the frontend `check`, which already runs `bun run test:unit`. CI then runs the same unit command again, and the frontend-unit hook repeats work performed by frontend-check for source changes. The CI migration-drift step at line 143 also repeats the fresh upgrade plus `command.check` covered by `web/backend/tests/test_alembic_drift.py` in the workspace pytest job. Frontend check/build both invoke the same TypeScript check too.

These repetitions add no different environment, input, or assertion. They make the check graph harder to understand and spend time on identical gates. In contrast, building Compose and running a browser suite exercise distinct deployment and interaction boundaries; neither should be deleted just because both run the application. No claim is made about configured GitHub required checks, which were not inspected.

**Remedy:** choose one owner for unit tests, type checking, and migration drift in each invocation path. Keep standalone developer commands if useful, but avoid composing them twice. Keep schema/client drift, migration-data preservation, and representative deployment/browser checks.

**Acceptance:** tracing CI and hooks shows one invocation per equivalent check, with all unique behavioral/artifact assertions retained. No new gate is necessary.

## 2. P2 — Remove the unused selection API being tested as if it were the browser path

**Location:** [SelectableClaimResponse.tsx:70](/workspaces/amfv/web/frontend/src/components/annotation/SelectableClaimResponse.tsx:70) and [SelectableClaimResponse.test.ts:9](/workspaces/amfv/web/frontend/src/components/annotation/SelectableClaimResponse.test.ts:9).

The emoji-offset test calls `responseSpanFromCodeUnitRange`, but repository search finds no production caller. Real selection goes through `selectionSpan -> sourceOffset -> responseSpanFromCodePointRange`; the sensitive DOM/UTF-16 conversion happens inside `sourceOffset`. The test-only exported wrapper could continue passing if the real conversion regressed. Canonical offset helpers already have separate pure tests.

**Remedy:** remove the unused wrapper and its redundant offset test. Preserve normalization tests and the existing browser test that selects response text after an emoji and checks exported code-point spans. If adding focused coverage, exercise the actual DOM selection path (including selection across highlighted segments), or extract a conversion genuinely shared by production and its test.

**Acceptance:** breaking the conversion used by `sourceOffset` fails a real-path regression test; no exported helper exists solely to give a unit test something to call. Do not replace the browser interaction with a mock.

## 3. P2 — Decouple the oversized browser journey and drop API-only repetitions

**Location:** [review-fact-decomposition.spec.ts:105](/workspaces/amfv/web/frontend/tests/review-fact-decomposition.spec.ts:105), ending at line 514.

One test combines import dry-run/replay checks, model highlighting, persistent instruction visibility, grading toggles, collapse/visibility, human-claim creation/removal, failed save/retry, reload, a second zero-claim task, and admin export/download. The import assertions issue HTTP requests directly and repeat `test_fact_decomp_import.py`; they do not exercise an import UI. A failure in early presentation assertions prevents all later save/reload/export coverage from running, making diagnosis and focused execution unnecessarily expensive.

**Remedy:** keep a compact real-browser grading/save/reload journey; give the zero-claim case and export/download their own independently seeded cases. Move repeated dry-run/idempotency assertions out of this browser path and retain their API tests. Keep meaningful browser assertions for DOM selection, toggle transitions, recovery, persisted state, and downloads. Do not split every assertion into a new browser test or introduce a fixture framework.

**Acceptance:** each bounded browser scenario runs alone using its own case IDs, and an early highlighting failure no longer suppresses unrelated export verification. The browser still proves the user interaction, and API tests remain authoritative for import replay.

## 4. P2 — Install the optional dependency needed by existing CI regression tests

**Location:** [pytest.yml:83](/workspaces/amfv/.github/workflows/pytest.yml:83), [datasets/pyproject.toml:18](/workspaces/amfv/datasets/pyproject.toml:18), and [test_run.py:11](/workspaces/amfv/datasets/test/decomposition_eval/test_run.py:11).

CI uses `uv sync --group dev`; PydanticAI is only declared in the datasets `decomposition-eval` extra. `test_agent.py`, `test_run.py`, and `test_runtime.py` skip their entire modules when it is absent. Thus the tests for retry behavior, bounded execution, cancellation, atomic output, and provider configuration are absent from a clean default CI run. Local verification happened to have the extra installed. `uv sync --dev --dry-run` independently confirmed that the default sync would uninstall `pydantic-ai-slim==2.33.0`.

**Remedy:** install the datasets decomposition-eval extra in the existing workspace test job. Retain optional installation for ordinary users; do not add a separate matrix or another release gate just to run the tests already intended here.

**Acceptance:** a fresh CI environment collects and runs all three modules, with no dependency-related skips, without model-network access or provider credentials.

## 5. P2 — Stop preserving production plumbing solely for tests

**Location:** [create.py:507](/workspaces/amfv/web/backend/app/api/routes/create.py:507), [authoring_fact_decomp.py:129](/workspaces/amfv/web/backend/app/services/authoring_fact_decomp.py:129), and [test_bootstrap.py:8](/workspaces/amfv/web/backend/tests/scripts/test_bootstrap.py:8).

`_commit_fact_decomp_save` explicitly exists to preserve a test seam. It delegates to `commit_fact_decomp_save`, which only calls `session.commit()`. This creates two names with no additional transaction policy. Nearby `ordered_facts` only copies a list. The two bootstrap tests replace the relevant dependencies and merely assert that `exec(select(1))` or `init_db(session)` was called; they do not verify readiness failure/retry or seeded state.

**Remedy:** call the real commit boundary directly and inject failure at that boundary in the existing rollback test. Preserve the rollback/receipt assertions. Remove the unnecessary `ordered_facts` list-copy wrapper after confirming its callers need no separate copy. Remove the bootstrap mock-call tests or replace them only with a needed behavior assertion not already exercised by bootstrap integration. Do not remove meaningful transaction tests to simplify mocking.

**Acceptance:** fact-authoring rollback leaves no item/receipt and replay remains idempotent. A harmless internal function rename should not require changes to plumbing-only tests. External callers are not visible in this checkout; confirm them before removing an API treated as public outside the application.

## 6. P2 — Give decomposition contract primitives a single owner

**Location:** [fact_decomp_import.py:53](/workspaces/amfv/web/backend/app/services/fact_decomp_import.py:53) and [fact_decomp_review.py:87](/workspaces/amfv/web/backend/app/services/fact_decomp_review.py:87); compare `datasets/amfv_datasets/decomposition_eval/models.py`.

The same span bounds, claim text/order constraints, labels, generator fields, generation settings, identifiers, and identity logic are separately defined in producer models, import models, and stored-correction models. Within the web backend alone, `ImportSpan`/`ImportClaim` and `StoredCorrectionSpan`/`StoredCorrectionClaim` reproduce the same structure and validators. The definitions have already diverged: importer generation settings are a closed typed model with numeric limits, while stored generation settings accept an arbitrary bounded dictionary of scalar values. Producer and importer strictness/defaults also differ.

Validation at file and persistence boundaries is appropriate; independently maintaining copies of the same primitives is the avoidable part. This is advisory because current valid producer/import/review integration passes and no normal-path data loss from these differences was demonstrated.

**Remedy:** at minimum share the web claim/span/generator primitives in a neutral contract module, retaining distinct artifact, storage, and review envelopes. If sharing with the producer, place only lightweight contract types in an existing suitable shared package or module; do not make the web backend depend on the model runtime or introduce a generic schema framework. Consolidate repeated primitive tests and keep one producer-to-import-to-export test for the actual boundary.

**Acceptance:** one definition governs common label/span/generation constraints; exact text, Unicode offsets, zero claims, optional query, identity/replay, and provenance round-trip behavior remain unchanged. Any intentional boundary differences are explicit rather than accidental copies.

## What should stay

- Authoring idempotency receipts, stale-revision protection, and failed-commit/lost-response tests protect real recoverability requirements.
- Migration tests with populated legacy data protect different transitions; they are not redundant with a fresh-schema drift check.
- Import/export boundary tests protect exact prompt text, source offsets, stable identity, and provenance.
- Pure normalization tests and real DOM/browser selection tests cover different surfaces.
- Template-derived auth/user patterns and generated clients are not findings merely because they are verbose or inherited.

## Final verification and scope limits

- `uv run pytest datasets/test/decomposition_eval web/backend/tests/test_fact_decomp_review.py web/backend/tests/test_rubrics.py web/backend/tests/api/routes/test_fact_decomp_import.py web/backend/tests/api/routes/test_fact_decomp_model_eval_api.py web/backend/tests/api/routes/test_fact_decomp_review_api.py -q`: **105 passed in 6.87s**.
- `uv run pytest web/backend/tests -q`: **228 passed in 38.31s**; 83 SQLite datetime-adapter deprecation warnings from migration tests. These runs overlap and must not be reported as 333 unique tests.
- `uv sync --dev --dry-run`: confirms default sync removes the optional PydanticAI installation; no environment changes made by this command.
- `git diff --check`: passed.
- Frontend unit command attempted but unavailable (`bun: command not found`, exit 127). No frontend build, browser run, screenshots, or live accessibility/responsive verification was performed.
- In-scope review covers fact decomposition generation, import/export, grading, authoring recovery, frontend selection/create/review ownership, tests, and shared website CI/hooks. Earlier retrieval inspection is excluded from findings and approval. The full backend test result above is retained as verification history, not as a retrieval quality judgment. Generated assets, historical migration internals, template UI primitives, and inherited auth tests were not exhaustively audited line by line.
- Scope-only revision: removed the retrieval finding and assignment-wrapper recommendation; revised verdict and renumbered the six remaining advisory findings. No tests rerun for this report-only change.
- Only this Markdown report was changed by the review; existing implementation changes were preserved. No fixes, new tests, commits, or external actions were performed.

## Implementation progress

User authorized the six in-scope fixes. Retrieval remains excluded.

- Removed duplicate unit-test hook and CI migration drift step. CI lint/email, unit tests, and typed build now each run once.
- Root development dependencies now include the datasets model-runtime extra; ordinary package installations remain optional. Lockfile updated.
- Removed unused selection conversion wrapper/test; keeping real DOM selection and persisted-offset coverage.
- Removed double commit wrappers and list-copy helper; rollback test now fails the actual Session commit. Removed bootstrap mock-call-only tests.
- Shared web artifact/storage claim, span, generation and provenance definitions in `app/fact_decomp_contract.py`; public review envelopes stay distinct. Extended the existing producer/import/export regression to preserve nonempty generation settings.
- Browser grading, zero-claim review, and export/download now use independent datasets and cases; removed repeated import dry-run/replay assertions from browser setup.
- Found Bun at `/home/vscode/.bun/bin/bun`; frontend verification is now possible. Python type checking passed; focused tests and frontend validation in progress.

### Validation checkpoint

- Combined decomposition/backend suite: **281 passed**, no skipped model-runtime modules; 83 existing SQLite datetime deprecation warnings.
- Frontend check: TypeScript and Biome passed; **24 unit tests passed**. Production build passed.
- Regenerated OpenAPI/client and passed client check. Ruff check/format and Python type checking passed.
- Independent browser cases initially passed (**4 scenarios plus authentication**). Final create/review suite is running after strengthening source selection.
- Temporarily replaced the actual DOM code-point conversion with UTF-16 length. The browser test failed on persisted source offset/text (13 instead of 12; missing initial `s`). Restored the correct production conversion immediately. This proves the retained test detects the real regression; the mutation is not part of the change.
- Locked default dev-sync dry run retains PydanticAI after the dependency fix. No new CI job or release gate added.

## Implementation results

All six advisory findings are addressed:

1. CI runs email/lint, frontend units, and the typed frontend build once each; the redundant unit hook and CI migration drift invocation are removed. The existing pytest drift test remains.
2. Removed the unused selection wrapper/test. The real-browser test now selects across highlight segments starting after an emoji within the same DOM text node, and verifies exact persisted source text/offsets. A deliberate wrong conversion failed that assertion; the correct conversion is restored and passing.
3. Grading/save/reload, zero-claim review, and admin download have separate browser scenarios and independent dataset/case setup. API-only dry-run/replay repetitions are removed. Fixed the authored browser test’s stale heading selector to match the existing Ordered Facts heading.
4. Root dev dependencies select the existing datasets decomposition-eval extra. Existing CI tests now install their runtime through the normal dev sync; no extra job/matrix/gate.
5. Removed the two commit wrappers, ordered-facts list-copy helper, and bootstrap mock-call tests. The rollback regression patches the actual Session commit and keeps its database/receipt assertions.
6. Web importer and persisted metadata share claim/span/generation/provenance definitions in `web/backend/app/fact_decomp_contract.py`. Public schemas share label/limit constants. Producer models remain independent of web deployment/runtime dependencies, with the existing producer-to-export integration test now verifying all nonempty generation settings and exact prompt text.

Final checks:

- `uv run pytest datasets/test/decomposition_eval web/backend/tests -q`: **281 passed** (39.72s), no skips. Existing SQLite datetime deprecation warnings remain.
- Frontend `bun run check`: TypeScript/Biome passed, **24 unit tests passed**.
- Frontend `bun run build`: passed.
- Real backend Playwright create/review suite: **12 passed** (11 scenarios plus authentication, 19.2s).
- Zero-claim and export cases also passed alone on a fresh harness: **3 passed** (2 scenarios plus authentication, 4.4s). These are repeat verification, not additional unique tests.
- Generated OpenAPI/client check, email artifact check, Ruff lint/format, Python type check, CI/hook YAML parsing, locked dev-sync resolution, and `git diff --check`: passed.
- The previous missing-Bun limitation is resolved by using the installed `/home/vscode/.bun/bin`. The original full Docker CI workflow was not executed locally; the actual browser harness and build were run. No visual redesign or retrieval changes were made.

The shared storage contract now enforces the same closed generation settings accepted at import; valid imported metadata is preserved. Handwritten metadata that bypassed the importer is not treated as a supported alternate contract. Existing user changes were preserved. No commit was created.
