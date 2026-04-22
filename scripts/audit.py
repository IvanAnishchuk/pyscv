"""Run supply-chain audits locally (same checks as CI).

Fails with a non-zero exit code on any check failure.

Usage:
    uv run python scripts/audit.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()
REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / ".reports" / "audit"

PROD_REQ = REPO_ROOT / "requirements.txt"
DEV_REQ = REPO_ROOT / "requirements-dev.txt"

TOTAL_STEPS = 6


def step(n: int, msg: str) -> None:
    console.print()
    console.print(f"[bold blue]==>[/] [bold]{n}/{TOTAL_STEPS}: {msg}[/]")


def ok(msg: str) -> None:
    console.print(f"   [bold green]ok[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"   [bold yellow]!![/] {msg}")


def fail(msg: str) -> None:
    console.print(f"   [bold red]FAIL[/] {msg}")
    sys.exit(1)


def file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_capture(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def load_sbom_components(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    return len(data.get("components", []))


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    step(1, "uv.lock in sync with pyproject.toml")
    code, out = run_capture(["uv", "lock", "--check"])
    if code == 0:
        ok("uv.lock is up to date")
    else:
        console.print(out)
        fail("uv.lock is out of date -- run: uv run python scripts/regen_requirements.py")

    step(2, "requirements*.txt in sync with uv.lock")
    prod_before = file_sha256(PROD_REQ)
    dev_before = file_sha256(DEV_REQ)
    code, out = run_capture([sys.executable, str(REPO_ROOT / "scripts" / "regen_requirements.py")])
    if code != 0:
        console.print(out)
        fail("Failed to regenerate requirements files")
    prod_after = file_sha256(PROD_REQ)
    dev_after = file_sha256(DEV_REQ)
    stale = False
    if prod_before != prod_after:
        warn(f"requirements.txt was stale ({prod_before[:12]} -> {prod_after[:12]})")
        stale = True
    if dev_before != dev_after:
        warn(f"requirements-dev.txt was stale ({dev_before[:12]} -> {dev_after[:12]})")
        stale = True
    if stale:
        fail("Files were regenerated. Review and commit them.")
    ok("requirements.txt and requirements-dev.txt are current")

    step(3, "pip-audit on requirements.txt (prod)")
    log = REPORTS_DIR / "pip-audit.log"
    code, out = run_capture(
        ["uv", "run", "pip-audit", "--strict", "--desc", "--requirement", str(PROD_REQ)]
    )
    log.write_text(out, encoding="utf-8")
    if code == 0:
        ok("No known vulnerabilities in prod dependencies")
    else:
        console.print(out)
        fail(f"pip-audit found vulnerabilities in prod -- see {log.relative_to(REPO_ROOT)}")

    step(4, "pip-audit on requirements-dev.txt (prod + dev)")
    log_dev = REPORTS_DIR / "pip-audit-dev.log"
    code, out = run_capture(
        ["uv", "run", "pip-audit", "--strict", "--desc", "--requirement", str(DEV_REQ)]
    )
    log_dev.write_text(out, encoding="utf-8")
    if code == 0:
        ok("No known vulnerabilities in dev dependencies")
    else:
        console.print(out)
        fail(f"pip-audit found vulnerabilities in dev -- see {log_dev.relative_to(REPO_ROOT)}")

    step(5, "CycloneDX SBOM (prod)")
    sbom = REPORTS_DIR / "sbom.cdx.json"
    code, out = run_capture(
        [
            "uv",
            "tool",
            "run",
            "--from",
            "cyclonedx-bom",
            "cyclonedx-py",
            "requirements",
            str(PROD_REQ),
            "--output-format",
            "json",
            "--output-file",
            str(sbom),
        ]
    )
    if code != 0:
        console.print(out)
        fail("cyclonedx-py failed for prod")
    components = load_sbom_components(sbom)
    ok(f"SBOM (prod) with {components} components -> {sbom.relative_to(REPO_ROOT)}")

    step(6, "CycloneDX SBOM (prod + dev)")
    sbom_dev = REPORTS_DIR / "sbom-dev.cdx.json"
    code, out = run_capture(
        [
            "uv",
            "tool",
            "run",
            "--from",
            "cyclonedx-bom",
            "cyclonedx-py",
            "requirements",
            str(DEV_REQ),
            "--output-format",
            "json",
            "--output-file",
            str(sbom_dev),
        ]
    )
    if code != 0:
        console.print(out)
        fail("cyclonedx-py failed for dev")
    components = load_sbom_components(sbom_dev)
    ok(f"SBOM (dev) with {components} components -> {sbom_dev.relative_to(REPO_ROOT)}")

    console.print()
    console.print("[bold green]All audits passed.[/]")
    console.print(f"Reports written to {REPORTS_DIR.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
