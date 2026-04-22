"""Download proof artifacts from GitHub, PyPI, and TestPyPI.

Downloads proof files and artifact copies for integrity checking.
Performs all transformations (attestation extraction, cosign bundle
restructuring) so verify scripts can be pure read-only.

Proof directory layout:
    proofs/{version}/
    ├── github/    — release proofs + attestation bundles + artifact copies
    ├── pypi/      — provenance + extracted attestations + artifact copies
    └── testpypi/  — same as pypi/
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console

from pyscv.config import PyscvConfig
from pyscv.download_artifacts import (
    fetch_gh_release_assets,
    fetch_pypi_release_files,
)
from pyscv.net import (
    API_TIMEOUT,
    atomic_download,
    gh_api_headers,
    resolve_url,
    safe_filename,
)

console = Console()

DIST_EXTENSIONS = (".whl", ".tar.gz")


# -- Helpers ---------------------------------------------------------------


def proofs_source_dir(config: PyscvConfig, version: str, source: str) -> Path:
    """Compute the target directory: proofs_dir / version / source."""
    if config.proofs_dir is None:
        msg = "proofs_dir is required — call validate_required() first"
        raise ValueError(msg)
    return config.proofs_dir / version / source


def _is_dist_file(name: str) -> bool:
    """Return True if filename is a distribution artifact."""
    return any(name.endswith(ext) for ext in DIST_EXTENSIONS)


# -- Source 1: GitHub Release proofs ---------------------------------------


def download_gh_release_proofs(
    config: PyscvConfig,
    version: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Download proof assets and artifact copies from GitHub Release.

    Uses the GitHub Releases API to fetch asset metadata, then downloads
    each file via httpx with URL validation.
    """
    gh_dir = proofs_source_dir(config, version, "github")
    tag = config.tag(version)

    try:
        assets = fetch_gh_release_assets(config, tag)
    except (httpx.HTTPError, ValueError) as exc:
        console.print(f"[red]ERROR: failed to fetch GitHub Release {tag}: {exc}[/]")
        return 1

    if not dry_run:
        gh_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0
    for asset in assets:
        try:
            name = safe_filename(asset.name)
        except ValueError as exc:
            console.print(f"  [red]FAIL[/] {asset.name}: {exc}")
            return 1

        dest = gh_dir / name

        if dry_run:
            if dest.exists() and not force:
                console.print(f"  [yellow]exists[/] {name}")
                skipped += 1
            else:
                console.print(f"  [green]download[/] {name}")
                downloaded += 1
            continue

        if dest.exists() and not force:
            if verbose:
                console.print(f"  [dim]skip {name} (exists)[/]")
            skipped += 1
            continue

        if verbose:
            console.print(f"  [dim]downloading {name}...[/]")
        try:
            atomic_download(asset.browser_download_url, dest)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            console.print(f"  [red]FAIL[/] {name}: {exc}")
            return 1
        console.print(f"  [green]OK[/] {name}")
        downloaded += 1

    action = "would be downloaded" if dry_run else "downloaded"
    summary = f"{downloaded} {action}"
    if skipped:
        summary += f", {skipped} skipped"
    console.print(f"  [bold]{summary}[/] from {tag}")
    return 0


# -- Source 2: GitHub Attestation API --------------------------------------


def _sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_gh_attestation(
    config: PyscvConfig,
    artifact_path: Path,
) -> list[dict] | None:
    """Fetch attestation bundles for an artifact from the GitHub Attestations API.

    Returns the list of attestation objects, or None if not available (404).
    """
    digest = _sha256_file(artifact_path)
    url = resolve_url(
        f"https://api.github.com/repos/{config.repo_slug}/attestations/sha256:{digest}"
    )
    resp = httpx.get(url, timeout=API_TIMEOUT, follow_redirects=False, headers=gh_api_headers())
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    if resp.is_redirect:
        msg = f"Unexpected redirect from {url}"
        raise ValueError(msg)
    data = resp.json()
    return data.get("attestations", [])


def fetch_gh_attestations(
    config: PyscvConfig,
    version: str,
    dist_filenames: list[str],
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Fetch GitHub attestation bundles for each dist file via REST API.

    Takes a list of dist filenames — uses the artifact copies
    in the github proofs directory to compute SHA256 digests.

    For each dist file:
    1. Compute SHA256 of artifact copy in proofs/github/
    2. Query ``GET /repos/{slug}/attestations/sha256:{digest}``
    3. Save raw JSON → {file}.gh-attestation.json
    4. Extract inner sigstore bundle → {file}.gh-attestation-bundle.json
    """
    gh_dir = proofs_source_dir(config, version, "github")
    if not dry_run:
        gh_dir.mkdir(parents=True, exist_ok=True)

    for name in dist_filenames:
        artifact_path = gh_dir / name
        att_file = gh_dir / f"{name}.gh-attestation.json"
        bundle_file = gh_dir / f"{name}.gh-attestation-bundle.json"

        if att_file.exists() and bundle_file.exists() and not force:
            if verbose:
                console.print(f"  [dim]skip {name} attestation (exists)[/]")
            continue

        if dry_run:
            console.print(f"  [green]fetch attestation[/] {name}")
            continue

        if not artifact_path.exists():
            console.print(f"  [red]FAIL[/] {name} — artifact not found in proofs dir")
            return 1

        if verbose:
            console.print(f"  [dim]fetching attestation for {name}...[/]")

        try:
            attestations = fetch_gh_attestation(config, artifact_path)
        except (httpx.HTTPError, ValueError) as exc:
            console.print(f"  [red]FAIL[/] {name} — attestation error: {exc}")
            return 1

        if not attestations:
            console.print(f"  [red]FAIL[/] {name} — no attestation available")
            return 1

        att_file.write_text(json.dumps(attestations, indent=2))
        console.print(f"  [green]OK[/] {name} attestation")

        # Extract inner sigstore bundle for cosign
        try:
            bundle = attestations[0]["bundle"]
            bundle_json = json.dumps(bundle)
        except (KeyError, IndexError, TypeError) as exc:
            console.print(f"  [red]FAIL[/] {name} bundle extraction — {exc}")
            return 1

        bundle_file.write_text(bundle_json)
        console.print(f"  [green]OK[/] {name} attestation bundle")

    return 0


# -- Source 3: PyPI / TestPyPI Integrity API -------------------------------


def fetch_pypi_provenance(
    config: PyscvConfig,
    version: str,
    filename: str,
    base_url: str,
) -> dict | None:
    """Fetch provenance from PyPI Integrity API.

    Returns the parsed JSON dict, or None if the provenance is not available (404).
    Raises on other HTTP errors.
    """
    url = f"{base_url}/integrity/{config.package_name}/{version}/{filename}/provenance"
    resolved = resolve_url(url)  # resolve_url validates internally
    resp = httpx.get(resolved, timeout=API_TIMEOUT, follow_redirects=False)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    if resp.is_redirect:
        msg = f"Unexpected redirect from {url}"
        raise ValueError(msg)
    return resp.json()


def _extract_cosign_bundle(att: dict) -> dict:
    """Restructure a PEP 740 attestation into a cosign-compatible bundle.

    Keys are required by PEP 740 — KeyError indicates a malformed attestation.
    """
    vm = att["verification_material"]
    return {
        "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        "verificationMaterial": {
            "certificate": {"rawBytes": vm["certificate"]},
            "tlogEntries": vm["transparency_entries"],
            "timestampVerificationData": {},
        },
        "dsseEnvelope": {
            "payload": att["envelope"]["statement"],
            "payloadType": "application/vnd.in-toto+json",
            "signatures": [{"sig": att["envelope"]["signature"]}],
        },
    }


def download_pypi_proofs(
    config: PyscvConfig,
    version: str,
    source_name: str,
    base_url: str,
    *,
    use_testpypi: bool = False,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Download PyPI provenance and extract all proof formats.

    For each dist file:
    1. Download artifact copy from PyPI for integrity checking
    2. Fetch provenance → {file}.provenance.json
    3. Extract PEP 740 attestation → {file}.publish.attestation
    4. Restructure into cosign bundle → {file}.cosign-bundle.json
    """
    pypi_dir = proofs_source_dir(config, version, source_name)
    if not dry_run:
        pypi_dir.mkdir(parents=True, exist_ok=True)

    # Fetch file list from the same index we're fetching proofs from
    pypi_config = config.model_copy(update={"use_testpypi": use_testpypi})
    try:
        files = fetch_pypi_release_files(pypi_config, version)
    except (httpx.HTTPError, ValueError) as exc:
        console.print(f"[red]ERROR: failed to fetch file list from {source_name}: {exc}[/]")
        return 1

    for file_info in files:
        try:
            filename = safe_filename(file_info.filename)
        except ValueError as exc:
            console.print(f"  [red]FAIL[/] {file_info.filename}: {exc}")
            return 1

        if not _is_dist_file(filename):
            continue

        # Download artifact copy for integrity checking
        artifact_dest = pypi_dir / filename
        if not artifact_dest.exists() or force:
            if dry_run:
                console.print(f"  [green]download[/] {filename}")
            else:
                if verbose:
                    console.print(f"  [dim]downloading {filename}...[/]")
                try:
                    atomic_download(file_info.url, artifact_dest)
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    console.print(f"  [red]FAIL[/] {filename}: {exc}")
                    return 1
                console.print(f"  [green]OK[/] {filename}")
        elif verbose:
            console.print(f"  [dim]skip {filename} (exists)[/]")

        # Fetch provenance and extract derived files
        prov_file = pypi_dir / f"{filename}.provenance.json"
        att_file = pypi_dir / f"{filename}.publish.attestation"
        cosign_file = pypi_dir / f"{filename}.cosign-bundle.json"
        if prov_file.exists() and att_file.exists() and cosign_file.exists() and not force:
            if verbose:
                console.print(f"  [dim]skip {filename} provenance (exists)[/]")
            continue

        if dry_run:
            console.print(f"  [green]fetch provenance[/] {filename}")
            continue

        if verbose:
            console.print(f"  [dim]fetching provenance for {filename}...[/]")
        try:
            prov = fetch_pypi_provenance(config, version, filename, base_url)
        except (httpx.HTTPError, ValueError) as exc:
            console.print(f"  [red]FAIL[/] {filename} provenance: {exc}")
            return 1

        if prov is None:
            console.print(f"  [red]FAIL[/] {filename} — no provenance available")
            return 1

        prov_file.write_text(json.dumps(prov, indent=2))
        console.print(f"  [green]OK[/] {filename} provenance")

        # Extract attestations from provenance
        bundles = prov.get("attestation_bundles", [])
        if not bundles:
            console.print(f"  [red]FAIL[/] {filename} — no attestation bundles in provenance")
            return 1

        # Process first bundle only — PyPI currently returns one bundle per artifact.
        # If this changes, we'll need indexed filenames.
        if len(bundles) > 1:
            console.print(
                f"  [yellow]WARNING[/] {filename} — {len(bundles)} bundles found, "
                "processing first only"
            )
        bundle_data = bundles[0]
        attestations = bundle_data.get("attestations", [])
        if not attestations:
            console.print(f"  [red]FAIL[/] {filename} — empty attestations in bundle")
            return 1
        att = attestations[0]

        # Extract individual PEP 740 attestation
        att_file.write_text(json.dumps(att))
        console.print(f"  [green]OK[/] {filename} .publish.attestation")

        # Restructure into cosign-compatible bundle
        try:
            cosign_bundle = _extract_cosign_bundle(att)
        except KeyError as exc:
            console.print(f"  [red]FAIL[/] {filename} cosign bundle — missing key: {exc}")
            return 1

        cosign_file.write_text(json.dumps(cosign_bundle, indent=2))
        console.print(f"  [green]OK[/] {filename} cosign bundle")

    return 0


# -- Orchestrator ----------------------------------------------------------


class ProofSource(StrEnum):
    github = "github"
    pypi = "pypi"
    testpypi = "testpypi"
    all = "all"


def download_proofs(
    config: PyscvConfig,
    version: str,
    source: ProofSource,
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Download proofs from the specified source(s).

    Returns 0 on success, 1 on any failure.
    """
    sources_to_fetch: list[ProofSource] = []
    if source == ProofSource.all:
        sources_to_fetch = [ProofSource.github, ProofSource.pypi, ProofSource.testpypi]
    else:
        sources_to_fetch = [source]

    for src in sources_to_fetch:
        if src == ProofSource.github:
            console.print(f"\n[bold]GitHub Release proofs[/] for {config.tag(version)}")
            code = download_gh_release_proofs(
                config, version, force=force, dry_run=dry_run, verbose=verbose
            )
            if code != 0:
                return code

            # Collect dist filenames from the github proofs dir for attestation lookup
            gh_dir = proofs_source_dir(config, version, "github")
            dist_filenames = (
                [f.name for f in sorted(gh_dir.iterdir()) if f.is_file() and _is_dist_file(f.name)]
                if gh_dir.is_dir()
                else []
            )

            console.print(f"\n[bold]GitHub attestations[/] for {config.tag(version)}")
            code = fetch_gh_attestations(
                config,
                version,
                dist_filenames,
                force=force,
                dry_run=dry_run,
                verbose=verbose,
            )
            if code != 0:
                return code

        elif src == ProofSource.pypi:
            console.print(f"\n[bold]PyPI proofs[/] for {config.package_name} {version}")
            code = download_pypi_proofs(
                config,
                version,
                "pypi",
                "https://pypi.org",
                use_testpypi=False,
                force=force,
                dry_run=dry_run,
                verbose=verbose,
            )
            if code != 0:
                return code

        elif src == ProofSource.testpypi:
            console.print(f"\n[bold]TestPyPI proofs[/] for {config.package_name} {version}")
            code = download_pypi_proofs(
                config,
                version,
                "testpypi",
                "https://test.pypi.org",
                use_testpypi=True,
                force=force,
                dry_run=dry_run,
                verbose=verbose,
            )
            if code != 0:
                return code

        else:
            msg = f"Unknown proof source: {src}"
            raise ValueError(msg)

    return 0


# -- CLI -------------------------------------------------------------------


app = typer.Typer(add_completion=False)


@app.command()
def main(
    version: Annotated[
        str | None,
        typer.Argument(help="Version to download proofs for. Default: from pyproject.toml."),
    ] = None,
    source: Annotated[
        ProofSource,
        typer.Option(help="Proof source (github, pypi, testpypi, all)."),
    ] = ProofSource.all,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing proof files."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would be downloaded."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show detailed progress."),
    ] = False,
    pyproject: Annotated[
        Path,
        typer.Option(help="Path to pyproject.toml."),
    ] = Path("pyproject.toml"),
) -> None:
    """Download proof artifacts from GitHub, PyPI, and TestPyPI."""
    try:
        config = PyscvConfig.from_pyproject(pyproject)
    except ValueError as exc:
        console.print(f"[red]ERROR: {exc}[/]")
        raise typer.Exit(1) from exc

    config = config.with_overrides(version=version)
    try:
        config.validate_required()
    except ValueError as exc:
        console.print(f"[red]ERROR: {exc}[/]")
        raise typer.Exit(1) from exc

    console.print(
        f"[bold]Downloading proofs for {config.package_name} {config.version}[/]"
        + (f" from [cyan]{source.value}[/]" if source != ProofSource.all else "")
        + (" [yellow](dry run)[/]" if dry_run else "")
    )

    code = download_proofs(
        config, config.version, source, force=force, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(code)
