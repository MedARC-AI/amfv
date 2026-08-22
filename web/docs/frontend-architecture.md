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

The AMFV-specific `/api/v1/admin/users*`, ingest, and user review-history endpoints are still deferred. Basic user management uses the template `/api/v1/users` APIs. Do not add navigation or UI copy that implies the deferred AMFV admin features work until the backend endpoints are implemented.

## API Ownership

The backend is the source of truth for:

- dataset and eval-type scoping;
- invite-gated signup and AMFV roles;
- retrieval evidence span validation;
- fact-decomposition fact ordering and provenance validation;
- moderation status transitions;
- task generation;
- agreement and user metrics;
- export shapes.

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
