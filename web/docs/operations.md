# Web operations ownership

This inventory records how standalone-repository residue was translated during
the monorepo migration.

| Copied artifact or behavior | Monorepo owner |
| --- | --- |
| Backend/frontend `.env`, ports, CORS, and SQLite launch setup | `scripts/common.sh`, sourced by distinct development and production entry points |
| Backend migration and initial-data preparation | `app.scripts.bootstrap` through `scripts/backend*.sh` and `backend/scripts/prestart.sh` |
| Nested pre-commit hooks | Root `.pre-commit-config.yaml` with root-relative Ruff, ty, Bun, email, unit, and client-generation commands |
| Copied mypy hook | Deliberately removed; the migrated backend contract is checked by `ty` |
| Copied release-note and zizmor hooks | Deliberately removed because their referenced standalone scripts/configuration were not copied; root CI remains the workflow owner |
| `hooks/post_gen_project.py` template callback | Deleted after `rg` found no caller; it was scaffold-generation residue, not runtime behavior |
| MJML sources and built HTML | `web/package.json` `email:build` / `email:check`, with `mjml` locked in `web/bun.lock` and CI drift enforcement |
| Frontend pure tests | `bun run test:unit` |
| Real-backend Playwright suite | `bun run test:e2e` or `scripts/run-frontend-e2e.sh`; serial, migrated disposable SQLite, no private setup route |
| OpenAPI and generated TypeScript client | `scripts/generate-client.sh` plus a CI no-diff check |

`backend.sh` and `frontend.sh` retain production-style build/serve behavior;
their `*-dev.sh` counterparts retain reload/development behavior. Docker Compose
remains a separate deployment path and is configuration-checked in CI.

Response-header policy, cookie/CSRF/OIDC auth, serialized production SQLite
writes, startup-migration removal, and readiness revision gating are explicitly
post-migration deployment work. They depend on the eventual proxy, HTTPS,
caching, download, and hosting topology.
