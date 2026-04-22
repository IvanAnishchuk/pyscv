"""Tests for pyscv.net — shared networking primitives."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from pyscv.net import (
    atomic_download,
    gh_api_headers,
    resolve_url,
    safe_filename,
    validate_url,
)

# -- validate_url ----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/file.whl",
        "https://objects.githubusercontent.com/file.whl",
        "https://pypi.org/file.whl",
        "https://test.pypi.org/file.whl",
        "https://files.pythonhosted.org/file.whl",
    ],
)
def test_validate_url_allows_known_hosts(url):
    validate_url(url)  # should not raise


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("http://github.com/file.whl", "non-HTTPS"),
        ("https://evil.com/file.whl", "unexpected host"),
        ("ftp://pypi.org/file.whl", "non-HTTPS"),
    ],
)
def test_validate_url_rejects_bad_urls(url, match):
    with pytest.raises(ValueError, match=match):
        validate_url(url)


# -- safe_filename ---------------------------------------------------------


def test_safe_filename_accepts_normal():
    assert safe_filename("pkg-1.0.whl") == "pkg-1.0.whl"


@pytest.mark.parametrize("name", ["../evil.whl", "sub/dir.whl", "..\\evil.whl"])
def test_safe_filename_rejects_traversal(name):
    with pytest.raises(ValueError, match="unsafe filename"):
        safe_filename(name)


# -- resolve_url -----------------------------------------------------------


def test_resolve_url_no_redirect(monkeypatch):
    resp = MagicMock()
    resp.is_redirect = False
    monkeypatch.setattr("pyscv.net.httpx.head", lambda *_a, **_kw: resp)
    assert resolve_url("https://github.com/file.whl") == "https://github.com/file.whl"


def test_resolve_url_follows_valid_redirect(monkeypatch):
    call_count = {"n": 0}

    def fake_head(url, **kw):
        call_count["n"] += 1
        resp = MagicMock()
        if call_count["n"] == 1:
            resp.is_redirect = True
            resp.headers = {"location": "https://release-assets.githubusercontent.com/file"}
        else:
            resp.is_redirect = False
        return resp

    monkeypatch.setattr("pyscv.net.httpx.head", fake_head)
    result = resolve_url("https://github.com/file.whl")
    assert result == "https://release-assets.githubusercontent.com/file"


def test_resolve_url_rejects_bad_redirect(monkeypatch):
    resp = MagicMock()
    resp.is_redirect = True
    resp.headers = {"location": "https://evil.com/steal"}
    monkeypatch.setattr("pyscv.net.httpx.head", lambda *_a, **_kw: resp)
    with pytest.raises(ValueError, match="unexpected host"):
        resolve_url("https://github.com/file.whl")


def test_resolve_url_too_many_redirects(monkeypatch):
    resp = MagicMock()
    resp.is_redirect = True
    resp.headers = {"location": "https://github.com/next"}
    monkeypatch.setattr("pyscv.net.httpx.head", lambda *_a, **_kw: resp)
    with pytest.raises(ValueError, match="Too many redirects"):
        resolve_url("https://github.com/start")


def test_resolve_url_missing_location_header(monkeypatch):
    resp = MagicMock()
    resp.is_redirect = True
    resp.status_code = 302
    resp.headers = {}
    monkeypatch.setattr("pyscv.net.httpx.head", lambda *_a, **_kw: resp)
    with pytest.raises(ValueError, match="missing Location header"):
        resolve_url("https://github.com/file.whl")


# -- atomic_download -------------------------------------------------------


@pytest.fixture()
def no_redirects(monkeypatch):
    """Mock resolve_url to return the URL unchanged (no redirects)."""
    monkeypatch.setattr("pyscv.net.resolve_url", lambda url, **_kw: url)


def _make_mock_stream(chunks: list[bytes]):
    """Create a mock context manager for httpx.stream."""
    stream = MagicMock()
    stream.raise_for_status = MagicMock()
    stream.is_redirect = False
    stream.iter_bytes.return_value = chunks
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    return stream


def test_atomic_download_writes_complete_file(tmp_path, monkeypatch, no_redirects):
    dest = tmp_path / "output.whl"
    monkeypatch.setattr(
        "pyscv.net.httpx.stream",
        lambda *_a, **_kw: _make_mock_stream([b"chunk1", b"chunk2"]),
    )
    atomic_download("https://github.com/f.whl", dest)
    assert dest.read_bytes() == b"chunk1chunk2"


def test_atomic_download_no_partial_on_status_error(tmp_path, monkeypatch, no_redirects):
    dest = tmp_path / "output.whl"
    stream = MagicMock()
    stream.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock(status_code=500)
    )
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("pyscv.net.httpx.stream", lambda *_a, **_kw: stream)
    with pytest.raises(httpx.HTTPStatusError):
        atomic_download("https://github.com/f.whl", dest)
    assert not dest.exists()


def test_atomic_download_no_partial_on_stream_error(tmp_path, monkeypatch, no_redirects):
    """If iter_bytes fails mid-write, dest must not exist."""
    dest = tmp_path / "output.whl"

    def exploding_iter():
        yield b"partial"
        raise ConnectionError

    stream = MagicMock()
    stream.raise_for_status = MagicMock()
    stream.is_redirect = False
    stream.iter_bytes = exploding_iter
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("pyscv.net.httpx.stream", lambda *_a, **_kw: stream)
    with pytest.raises(ConnectionError):
        atomic_download("https://github.com/f.whl", dest)
    assert not dest.exists()


def test_atomic_download_rejects_unexpected_redirect(tmp_path, monkeypatch, no_redirects):
    dest = tmp_path / "output.whl"
    stream = MagicMock()
    stream.raise_for_status = MagicMock()
    stream.is_redirect = True
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("pyscv.net.httpx.stream", lambda *_a, **_kw: stream)
    with pytest.raises(ValueError, match="Unexpected redirect"):
        atomic_download("https://github.com/f.whl", dest)
    assert not dest.exists()


# -- gh_api_headers --------------------------------------------------------


def test_gh_api_headers_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    headers = gh_api_headers()
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/vnd.github+json"


def test_gh_api_headers_with_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    headers = gh_api_headers()
    assert headers["Authorization"] == "Bearer ghp_test123"


def test_gh_api_headers_with_gh_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_fallback")
    headers = gh_api_headers()
    assert headers["Authorization"] == "Bearer ghp_fallback"
