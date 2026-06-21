# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Initial project scaffold.

### Changed

- Opted the repo into the cadence handoff convention and refreshed a stale
  CLAUDE.md workflow reference.
- `uv.lock` is now the single source of truth for dependencies;
  `requirements*.txt` are generated on demand (never committed).
  `scripts/regen_requirements.py` gains `--stdout` / `--output-dir` modes and
  defaults to writing into the git-ignored `.reports/requirements/`.
  `scripts/audit.py` exports requirements into a tempdir instead of reading
  committed files. The release SBOM is generated through the helper (prod-only).
- Branch protection and merge policy in `.github/settings.yml`: merge commits
  only (squash and rebase disabled), and an explicit `restrictions: null` so the
  Settings app actually applies the protection block.

### Removed

- Committed `requirements.txt` / `requirements-dev.txt` and the
  `regen-requirements` pre-commit hook.
