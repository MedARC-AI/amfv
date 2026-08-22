# AMFV Web Frontend

The frontend is built with [Vite](https://vitejs.dev/), [React](https://reactjs.org/), [TypeScript](https://www.typescriptlang.org/), [TanStack Query](https://tanstack.com/query), [TanStack Router](https://tanstack.com/router) and [Tailwind CSS](https://tailwindcss.com/).

## Requirements

- [Bun](https://bun.sh/) for the documented local workflow.

The checked-in scripts use `bun` and `bunx`. Node/npm support can be added later with explicit equivalent commands.

## Quick Start

```bash
bun install
bun run dev
```

Then open `http://localhost:5173/`. The repository launch scripts use the
configured monorepo port (`24861` by default) instead.

Notice that this live server is not running inside Docker, it's for local development, and that is the recommended workflow. Once you are happy with your frontend, you can build the frontend Docker image and start it, to test it in a production-like environment. But building the image at every change will not be as productive as running the local development server with live reload.

Check the file `package.json` to see other available options.

## Generate Client

### Automatically

From the `web/` directory, run:

```bash
bash ./scripts/generate-client.sh
```

From the monorepo root, the equivalent is
`bash web/scripts/generate-client.sh`.

* The script writes `frontend/openapi.json`, regenerates `frontend/src/client/*`, and runs the generated-client smoke check.

### Manually

* Start the Docker Compose stack.

* Download the OpenAPI JSON file from `http://localhost/api/v1/openapi.json` and copy it to a new file `openapi.json` at the root of the `frontend` directory.

* To generate the frontend client, run:

```bash
bun run generate-client
```

* Commit the changes.

Notice that every time the backend changes the OpenAPI schema, you should run client generation again and commit the regenerated schema/client.

## Verification

For focused frontend-only changes, use targeted checks:

```bash
bun run check-client
bun run check
bun run test:unit
bun run build
```

For backend API contract changes, run the top-level generated-client script after
the backend change and before frontend edits:

```bash
bash ../scripts/generate-client.sh
```

`test:unit` runs the pure Bun suites, including code-point offsets and
multi-viewer selection state. It never substitutes for Playwright.

## Using a Remote API

If you want to use a remote API, you can set the environment variable `VITE_API_URL` to the URL of the remote API. For example, you can set it in the `frontend/.env` file:

```env
VITE_API_URL=https://api.my-domain.example.com
```

Then, when you run the frontend, it will use that URL as the base URL for the API.

## Code Structure

The frontend code is structured as follows:

* `frontend/src` - The main frontend code.
* `frontend/src/assets` - Static assets.
* `frontend/src/client` - The generated OpenAPI client.
* `frontend/src/components` -  The different components of the frontend.
* `frontend/src/hooks` - Custom hooks.
* `frontend/src/routes` - The different routes of the frontend which include the pages.

The active AMFV product shell uses:

* Home
* Review
* Create
* My Work
* Profile/settings
* Admin

Keep new interactive workflows in React and call `/api/v1` APIs instead of adding server-rendered pages.

## End-to-End Testing with Playwright

The frontend includes end-to-end tests using Playwright. From the `web/`
directory, run:

```bash
bash ./scripts/run-frontend-e2e.sh
```

Equivalently, run `bun run test:e2e` from `frontend/`. The harness creates a
disposable SQLite database, runs migrations and deterministic seed data twice,
starts the real backend, and lets Playwright start Vite. Tests run serially
because they deliberately mutate shared seeded workflow state. Focused arguments
are forwarded, for example `bun run test:e2e -- tests/create-retrieval.spec.ts`.
To debug interactively after starting your own backend/frontend servers, run:

```bash
bunx playwright test --ui
```

To update the tests, navigate to the tests directory and modify the existing test files or add new ones as needed.

For more information on writing and running Playwright tests, refer to the official [Playwright documentation](https://playwright.dev/docs/intro).
