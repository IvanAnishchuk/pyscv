# Copilot instructions for pyscv

## Project

Pure Python CLI tool (Python 3.13+) using uv, hatchling, typer, rich.
Source layout under `src/pyscv/`.

## Conventions

- **Commits:** Conventional Commits format required (`feat:`, `fix:`, `chore:`, etc.)
- **Formatting:** ruff (line length 100, target py313)
- **Type checking:** ty
- **Testing:** pytest with coverage floor (see pyproject.toml)
- **Package manager:** uv (never raw pip)
- No checked-in shell scripts or Makefiles -- prefer Python scripts for tooling

## Version locations

When bumping version, update ALL of:
- `pyproject.toml`
- `src/pyscv/__init__.py`

## Security

- All subprocess calls use list args (no shell=True)
- Exception catches should be as narrow as possible
- URLs must be validated before fetching
- Every noqa comment must document why it is necessary
- Supply-chain: sigstore, PEP 740 attestations on every release

## Workflow

- Never push to main directly -- always use PRs
- Run `uv run pre-commit run --all-files` before pushing
- All CI checks must pass before merge

## Changelog

- Every PR must have a corresponding `CHANGELOG.md` entry under `[Unreleased]`
- When reviewing, check that user-visible changes have changelog entries

## Review process

**When asked to "review", only review.** Do not create commits, push
changes, or apply fixes. The goal of a review is to provide feedback,
not to modify the code. If you find issues, report them as review
comments -- never fix them on behalf of the author.

When reviewing PRs:
- Triage every comment, including low-confidence hidden ones
- For actionable findings: report them as review comments (with code suggestions where applicable), or create a GitHub issue and link it
- Never dismiss comments without explicit owner confirmation
- Reply with linked issue number or reason before resolving conversations
- After changes are made, re-review before approving
- Verify that CHANGELOG.md is updated for user-visible changes
