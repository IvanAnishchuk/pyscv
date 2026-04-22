"""Download distribution artifacts from GitHub Release or PyPI.

Downloads only distribution files (.whl, .tar.gz) to dist/.
Does not touch proofs/ or perform any transformations.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import BaseModel
from rich.console import Console

from pyscv.config import PyscvConfig
from pyscv.net import (
    API_TIMEOUT,
    atomic_download,
    gh_api_headers,
    resolve_url,
    safe_filename,
)

console = Console()

DEFAULT_EXTENSIONS = (".whl", ".tar.gz")


# -- API response models ---------------------------------------------------


class GhReleaseAsset(BaseModel):
    name: str
    browser_download_url: str


class PypiFileInfo(BaseModel):
    filename: str
    url: str


# -- Fetch helpers ---------------------------------------------------------


def fetch_gh_release_assets(config: PyscvConfig, tag: str) -> list[GhReleaseAsset]:
    """Fetch asset list from GitHub Releases API."""
    url = resolve_url(f"https://api.github.com/repos/{config.repo_slug}/releases/tags/{tag}")
    resp = httpx.get(url, timeout=API_TIMEOUT, follow_redirects=False, headers=gh_api_headers())
    resp.raise_for_status()
    if resp.is_redirect:
        msg = f"Unexpected redirect from {url}"
        raise ValueError(msg)
    raw_assets = resp.json().get("assets", [])
    return [GhReleaseAsset.model_validate(a) for a in raw_assets]


def fetch_pypi_release_files(config: PyscvConfig, version: str) -> list[PypiFileInfo]:
    """Fetch file list from PyPI JSON API."""
    url = resolve_url(f"{config.pypi_base_url}/pypi/{config.package_name}/{version}/json")
    resp = httpx.get(url, timeout=API_TIMEOUT, follow_redirects=False)
    resp.raise_for_status()
    if resp.is_redirect:
        msg = f"Unexpected redirect from {url}"
        raise ValueError(msg)
    raw_files = resp.json().get("urls", [])
    return [PypiFileInfo.model_validate(f) for f in raw_files]


# -- Source downloaders ----------------------------------------------------


def download_from_gh(
    config: PyscvConfig,
    version: str,
    extensions: tuple[str, ...],
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Download artifacts from GitHub Release via API + httpx."""
    if config.dist_dir is None:
        msg = "dist_dir is required — call validate_required() first"
        raise ValueError(msg)
    tag = config.tag(version)
    try:
        assets = fetch_gh_release_assets(config, tag)
    except (httpx.HTTPError, ValueError) as exc:
        console.print(f"[red]ERROR: failed to fetch GitHub Release {tag}: {exc}[/]")
        return 1

    if not dry_run:
        config.dist_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    for asset in assets:
        try:
            name = safe_filename(asset.name)
        except ValueError as exc:
            console.print(f"  [red]FAIL[/] {asset.name}: {exc}")
            return 1
        if not any(name.endswith(ext) for ext in extensions):
            if verbose:
                console.print(f"  [dim]skip {name} (not a dist file)[/]")
            continue

        dest = config.dist_dir / name

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
    console.print(f"\n[bold]{summary}[/] from {tag}")
    return 0


def download_from_pypi(
    config: PyscvConfig,
    version: str,
    extensions: tuple[str, ...],
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Download artifacts from PyPI (or TestPyPI if configured)."""
    if config.dist_dir is None:
        msg = "dist_dir is required — call validate_required() first"
        raise ValueError(msg)
    try:
        files = fetch_pypi_release_files(config, version)
    except (httpx.HTTPError, ValueError) as exc:
        console.print(f"[red]ERROR: failed to fetch from {config.pypi_label}: {exc}[/]")
        return 1

    if not dry_run:
        config.dist_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    for file_info in files:
        try:
            filename = safe_filename(file_info.filename)
        except ValueError as exc:
            console.print(f"  [red]FAIL[/] {file_info.filename}: {exc}")
            return 1
        if not any(filename.endswith(ext) for ext in extensions):
            if verbose:
                console.print(f"  [dim]skip {filename} (not matching extensions)[/]")
            continue

        dest = config.dist_dir / filename

        if dry_run:
            if dest.exists() and not force:
                console.print(f"  [yellow]exists[/] {filename}")
                skipped += 1
            else:
                console.print(f"  [green]download[/] {filename}")
                downloaded += 1
            continue

        if dest.exists() and not force:
            if verbose:
                console.print(f"  [dim]skip {filename} (exists)[/]")
            skipped += 1
            continue

        if verbose:
            console.print(f"  [dim]downloading {filename}...[/]")
        try:
            atomic_download(file_info.url, dest)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            console.print(f"  [red]FAIL[/] {filename}: {exc}")
            return 1
        console.print(f"  [green]OK[/] {filename}")
        downloaded += 1

    action = "would be downloaded" if dry_run else "downloaded"
    summary = f"{downloaded} {action}"
    if skipped:
        summary += f", {skipped} skipped"
    console.print(f"\n[bold]{summary}[/] from {config.pypi_label}")
    return 0


# -- CLI -------------------------------------------------------------------


class Source(StrEnum):
    gh = "gh"
    pypi = "pypi"


app = typer.Typer(add_completion=False)


@app.command()
def main(
    version: Annotated[
        str | None,
        typer.Argument(help="Version to download (e.g. 0.4.2a10). Default: from pyproject.toml."),
    ] = None,
    source: Annotated[
        Source,
        typer.Option(help="Download source."),
    ] = Source.gh,
    ext: Annotated[
        list[str] | None,
        typer.Option(help="File extensions to download (repeatable)."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing files."),
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
    """Download distribution artifacts from GitHub Release or PyPI."""
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

    extensions = tuple(ext) if ext else DEFAULT_EXTENSIONS

    if config.use_testpypi and source == Source.pypi:
        console.print(
            "[bold yellow]WARNING: using TestPyPI (use-testpypi = true in pyproject.toml)[/]"
        )

    source_label = config.pypi_label if source == Source.pypi else source.value
    console.print(
        f"[bold]Downloading {config.package_name} {config.version}[/] from [cyan]{source_label}[/]"
        + (" [yellow](dry run)[/]" if dry_run else "")
    )

    if source == Source.gh:
        code = download_from_gh(
            config, config.version, extensions, force=force, dry_run=dry_run, verbose=verbose
        )
    else:
        code = download_from_pypi(
            config, config.version, extensions, force=force, dry_run=dry_run, verbose=verbose
        )

    raise typer.Exit(code)
