"""Run supply-chain audits locally (same checks as CI).

Fails with a non-zero exit code on any check failure.

Usage:
    uv run python scripts/audit.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console

console = Console()
REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / ".reports" / "audit"

TOTAL_STEPS = 4

# Load the sibling export helper in-process (scripts/ is not a package).
_regen_spec = importlib.util.spec_from_file_location(
    "regen_requirements", REPO_ROOT / "scripts" / "regen_requirements.py"
)
if _regen_spec is None or _regen_spec.loader is None:
    raise ImportError(name="regen_requirements")
regen = importlib.util.module_from_spec(_regen_spec)
_regen_spec.loader.exec_module(regen)


def step(n: int, msg: str) -> None:
    console.print()
    console.print(f"[bold blue]==>[/] [bold]{n}/{TOTAL_STEPS}: {msg}[/]")


def ok(msg: str) -> None:
    console.print(f"   [bold green]ok[/] {msg}")


def fail(msg: str) -> None:
    console.print(f"   [bold red]FAIL[/] {msg}")
    sys.exit(1)


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


def export_requirements(tmpdir: Path, *, include_dev: bool) -> Path:
    """Export one requirements set from uv.lock into tmpdir, return its path."""
    name = "requirements-dev.txt" if include_dev else "requirements.txt"
    dest = tmpdir / name
    dest.write_text(regen.export(include_dev=include_dev), encoding="utf-8")
    return dest


def check_lock_in_sync() -> None:
    step(1, "uv.lock in sync with pyproject.toml")
    code, out = run_capture(["uv", "lock", "--check"])
    if code == 0:
        ok("uv.lock is up to date")
    else:
        console.print(out)
        fail("uv.lock is out of date -- run: uv lock")


def pip_audit(step_n: int, req: Path, scope: str, logname: str) -> None:
    step(step_n, f"pip-audit on {req.name} ({scope})")
    log = REPORTS_DIR / logname
    code, out = run_capture(
        ["uv", "run", "pip-audit", "--strict", "--desc", "--requirement", str(req)]
    )
    log.write_text(out, encoding="utf-8")
    if code == 0:
        ok(f"No known vulnerabilities in {scope} dependencies")
    else:
        console.print(out)
        fail(f"pip-audit found vulnerabilities in {scope} -- see {log.relative_to(REPO_ROOT)}")


def generate_sbom(step_n: int, req: Path, scope: str, outname: str) -> None:
    step(step_n, f"CycloneDX SBOM ({scope})")
    sbom = REPORTS_DIR / outname
    code, out = run_capture(
        [
            "uv",
            "tool",
            "run",
            "--from",
            "cyclonedx-bom",
            "cyclonedx-py",
            "requirements",
            str(req),
            "--output-format",
            "json",
            "--output-file",
            str(sbom),
        ]
    )
    if code != 0:
        console.print(out)
        fail(f"cyclonedx-py failed for {scope}")
    components = load_sbom_components(sbom)
    ok(f"SBOM ({scope}) with {components} components -> {sbom.relative_to(REPO_ROOT)}")


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    check_lock_in_sync()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        prod_req = export_requirements(tmp, include_dev=False)
        dev_req = export_requirements(tmp, include_dev=True)
        pip_audit(2, prod_req, "prod", "pip-audit.log")
        pip_audit(3, dev_req, "prod + dev", "pip-audit-dev.log")
        generate_sbom(4, prod_req, "prod", "sbom.cdx.json")
    console.print()
    console.print("[bold green]All audits passed.[/]")
    console.print(f"Reports written to {REPORTS_DIR.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
