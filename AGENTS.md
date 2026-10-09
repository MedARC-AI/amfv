# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.13 `uv` workspace for the MedARC Agentic Medical Fact Verifier. Runtime workspace packages live in `datasets/`, `decomposer/`, `search/`, and `verifier/`, each with an `amfv_*` import package and local `pyproject.toml`. `baseline/` and `training/` are independent packages excluded from the root workspace. Keep tests beside their component in `<component>/test/`; the root pytest config already targets `datasets/test`, `decomposer/test`, `search/test`, and `verifier/test`. Read component READMEs before changing behavior.

## Build, Test, and Development Commands

- `uv sync --dev`: install root development dependencies and workspace packages.
- `uv run pytest`: run the root test suite across workspace components.
- `uv run pytest decomposer/test`: run tests for one component.
- `uv run ruff check .`: lint imports, docstrings, pyupgrade rules, and Python errors.
- `uv run ruff format .`: format Python files using Ruff defaults.
- `uv build --package amfv-decomposer`: build a workspace package when packaging changes need validation.

For independent packages, run commands from that directory, for example `cd baseline && uv sync --dev && uv run pytest`.

## Coding Style & Naming Conventions

Follow `STYLE.md` and `ruff.toml`. Ruff enforces 120-character lines, Google-style docstrings, import sorting, pycodestyle, pyflakes, and pyupgrade rules. Use `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_CASE` for constants, and `_leading_underscore` for private helpers. Public modules should declare `__all__`; package `__init__.py` files should stay focused on re-exports. Prefer modern annotations such as `str | None` and `list[Claim]`.

## Testing Guidelines

Use pytest. Name test files `test_*.py` under the owning component's `test/` directory. Parametrize related cases with readable `ids`, centralize shared fixtures or skip logic, and add focused tests for any public API or pipeline behavior you change. There is no stated coverage threshold yet; prioritize meaningful assertions over broad smoke tests.

## Commit & Pull Request Guidelines

Use descriptive branch names without a `codex/` prefix, for example `cco-scraper-update`.

The existing history uses short, imperative, lowercase commit subjects such as `setup ruff linting and github ci`. Keep commits scoped to one logical change. Pull requests should describe the changed component, summarize behavior and tests run, link any issue or experiment context, and include screenshots only when user-facing rendered output changes.
