# Frontend Architecture

AMFV Web is a React-first app backed by FastAPI JSON APIs. The frontend owns interaction state and presentation. The backend owns auth, permissions, validation, persistence, moderation, metrics, and export contracts.

## Stack

- Vite React TypeScript in `frontend/`
- TanStack Router for route structure
- TanStack Query for server state
- React Hook Form and Zod for complex forms
- Tailwind and local UI components
- FastAPI under `/api/v1`
- OpenAPI client generated into `frontend/src/client`

The old static/Jinja app is preserved only as the local `static-firstpass` branch reference. New product flows should be built against the React app and `/api/v1` routes.

## Product Shape

End users should see two main choices:

- Review
- Create

Each choice branches into Retrieval and Fact Decomposition. Admin users get richer surfaces for datasets, source documents, moderation, task generation, export, users, user metrics, agreement, and inter-user agreement.

The AMFV-specific `/api/v1/admin/users*`, legacy item ingest, and user
review-history endpoints are still deferred. The distinct generic document
JSONL import is live at `/api/v1/admin/documents/import`. Basic user management
uses the template `/api/v1/users` APIs. Do not add navigation or UI copy that
implies the deferred AMFV admin features work until the backend endpoints are
implemented.

## API Ownership

The backend is the source of truth for:

- dataset and eval-type scoping;
- invite-gated signup and AMFV roles;
- retrieval evidence span validation;
- fact-decomposition fact ordering and provenance validation;
- moderation status transitions;
- task generation;
- agreement and user metrics;
- bounded, paginated export shapes.

The frontend should not duplicate these rules. It should submit draft/review payloads, render returned validation flags, and refresh data after server mutations.

## Text Highlighting Contract

Retrieval and fact provenance highlighting submit spans as:

```json
{"chunk_id": 1, "start": 0, "end": 10, "text": "selected"}
```

Offsets are Python string/code-point offsets, not browser UTF-16 offsets. React selection code should map chunk text with `Array.from(chunkText)` before calculating offsets. The backend validates every span with `chunk.text[start:end] == text` and rejects stale or cross-dataset spans.

## Auth Notes

The app currently uses the FastAPI template password/JWT flow with invite-gated signup. Token expiry remains the template default of 8 days. The frontend keeps the template bearer-token storage for the first internal version.

Future hardening can move tokens to HttpOnly cookies and add session revocation if deployment risk changes. That is not required for the current internal eval-building workflow.

## Generated Client

Regenerate the OpenAPI client after backend API changes:

```bash
bash ./scripts/generate-client.sh
```

The script writes `frontend/openapi.json`, regenerates `frontend/src/client/*`, and runs the generated-client smoke check.

Review commands are authorized by each payload's literal `allowed_actions`;
the UI refuses unavailable actions. Server and transport errors are rendered
through bounded product messages rather than raw response bodies.

## Model decomposition corrections

The correction view keeps each original model claim and label immutable.
Reviewers can relabel, edit text and source spans, split a claim, remove an
extraction error, or restore the original. New claims start from selected source
text. Splits inherit source spans and labels; each part can then be edited.
Incidental and repeated claims remain available for review.

Editors stage changes until Apply changes. Cancel discards the staged edit.
Submission is disabled while an editor is open. All mutation controls are locked
while submitting and after success. A saved review opens with its final claims
and read-only controls. The API stores the complete final list with references
to original positions; the UI never overwrites imported model claims.
