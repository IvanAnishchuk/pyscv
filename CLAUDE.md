# CLAUDE.md

## Project Overview

pyscv -- Python Supply-Chain Verification toolkit

CLI tool built with typer + rich. Source layout under `src/pyscv/`.

## Commands

```bash
# Install dependencies
uv sync

# Run the CLI
uv run pyscv --help

# Run tests with coverage
uv run pytest

# Lint + format
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/

# Type check
uv run ty check

# Full pre-commit suite
uv run pre-commit run --all-files

# Supply-chain audit (pip-audit + SBOM)
uv run python scripts/audit.py
```

## Conventions

- Python 3.13+, src layout, hatchling build backend
- Ruff for linting (line-length 100, security rules enabled) and formatting
- ty for type checking
- pytest with coverage (threshold in pyproject.toml)
- Conventional commits enforced by pre-commit hook
- All CI checks must pass before merge (see .github/workflows/ci.yml)
