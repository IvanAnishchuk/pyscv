"""Regenerate requirements.txt and requirements-dev.txt from uv.lock.

Keeps committed requirements files in sync with the lockfile so that
pip-audit and SBOM generation work from stable, pinned input.

Usage:
    uv run python scripts/regen_requirements.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def export(extra_args: list[str], dest: Path) -> None:
    cmd = [
        "uv",
        "export",
        "--format",
        "requirements-txt",
        "--no-emit-project",
        *extra_args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=False)  # noqa: S603
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    dest.write_text(result.stdout, encoding="utf-8")
    print(f"  wrote {dest.relative_to(REPO_ROOT)}")


def main() -> None:
    print("Regenerating requirements files from uv.lock ...")
    export(["--no-dev"], REPO_ROOT / "requirements.txt")
    export([], REPO_ROOT / "requirements-dev.txt")
    print("Done.")


if __name__ == "__main__":
    main()
