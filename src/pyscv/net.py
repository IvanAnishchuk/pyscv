"""Shared networking primitives for pyscv."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx

ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "pypi.org",
        "test.pypi.org",
        "files.pythonhosted.org",
        "test-files.pythonhosted.org",
    }
)


def gh_api_headers() -> dict[str, str]:
    """Build GitHub API headers, including auth token from environment if available."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


API_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
MAX_REDIRECTS = 5


def validate_url(url: str) -> None:
    """Validate that a URL uses HTTPS and an allowed host."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        msg = f"Refusing non-HTTPS URL: {url}"
        raise ValueError(msg)
    if parsed.hostname not in ALLOWED_HOSTS:
        msg = f"Refusing URL from unexpected host {parsed.hostname}: {url}"
        raise ValueError(msg)


def safe_filename(name: str) -> str:
    """Validate filename has no path traversal components."""
    safe = PurePosixPath(name).name
    if safe != name or ".." in name or "/" in name or "\\" in name:
        msg = f"Refusing unsafe filename: {name!r}"
        raise ValueError(msg)
    return safe


def resolve_url(url: str, timeout: float = API_TIMEOUT) -> str:
    """Validate URL, follow redirect chain (each hop validated), return final URL."""
    validate_url(url)
    start_url = url
    redirects_followed = 0
    while True:
        resp = httpx.head(url, follow_redirects=False, timeout=timeout)
        if not resp.is_redirect:
            return url
        redirects_followed += 1
        if redirects_followed > MAX_REDIRECTS:
            msg = f"Too many redirects (>{MAX_REDIRECTS}) starting from {start_url}"
            raise ValueError(msg)
        location = resp.headers.get("location")
        if not location:
            msg = f"Redirect {resp.status_code} from {url} missing Location header"
            raise ValueError(msg)
        # urljoin handles both absolute and relative Location headers:
        # absolute → returned as-is, relative → resolved against current url
        url = urljoin(url, location)
        validate_url(url)


def atomic_download(url: str, dest: Path) -> None:
    """Download a file atomically — write to temp dir then replace.

    Validates URL and all redirect destinations before downloading.
    """
    final_url = resolve_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmpdir:
        tmp_file = Path(tmpdir) / dest.name
        with httpx.stream(
            "GET", final_url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=False
        ) as stream:
            stream.raise_for_status()
            if stream.is_redirect:
                msg = f"Unexpected redirect from {final_url}"
                raise ValueError(msg)
            with tmp_file.open("wb") as fh:
                for chunk in stream.iter_bytes():
                    fh.write(chunk)
        tmp_file.replace(dest)
