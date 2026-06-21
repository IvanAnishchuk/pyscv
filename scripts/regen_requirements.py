"""Export requirements from uv.lock -- the single export definition.

uv.lock is the source of truth; requirements*.txt are generated on demand and
never committed. Two modes:

    # stream ONE set to stdout (the `regen | ...` pipe; default prod-only)
    uv run python scripts/regen_requirements.py --stdout [--include-dev]

    # write BOTH files into a directory (build dir / release artifact)
    uv run python scripts/regen_requirements.py --output-dir DIR

No args: writes both files into .reports/requirements/ (git-ignored).
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".reports" / "requirements"

logger = logging.getLogger(__name__)


def export(*, include_dev: bool) -> str:
    """Return uv-exported requirements text for one dependency set."""
    cmd = ["uv", "export", "--format", "requirements-txt", "--no-emit-project"]
    if not include_dev:
        cmd.append("--no-dev")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=False)  # noqa: S603
    if result.returncode != 0:
        logger.error(result.stderr)
        sys.exit(result.returncode)
    return result.stdout


def write_files(output_dir: Path) -> None:
    """Write both prod and prod+dev requirements files into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "requirements.txt").write_text(export(include_dev=False), encoding="utf-8")
    (output_dir / "requirements-dev.txt").write_text(export(include_dev=True), encoding="utf-8")
    logger.info("  wrote requirements.txt, requirements-dev.txt to %s", output_dir)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Export requirements from uv.lock.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdout", action="store_true", help="write one set to stdout (pipe)")
    mode.add_argument("--output-dir", type=Path, help="write both files into this directory")
    parser.add_argument(
        "--include-dev", action="store_true", help="(stdout mode) include dev dependencies"
    )
    args = parser.parse_args(argv)
    if args.include_dev and not args.stdout:
        parser.error("--include-dev only applies in --stdout mode")

    if args.stdout:
        sys.stdout.write(export(include_dev=args.include_dev))
    else:
        write_files(args.output_dir or DEFAULT_OUTPUT_DIR)


if __name__ == "__main__":
    main()
