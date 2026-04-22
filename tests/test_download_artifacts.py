"""Tests for pyscv.download_artifacts and pyscv.config."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from pyscv.config import PyscvConfig
from pyscv.download_artifacts import (
    GhReleaseAsset,
    PypiFileInfo,
    app,
    download_from_gh,
    download_from_pypi,
    fetch_gh_release_assets,
    fetch_pypi_release_files,
)

# -- Fixtures --------------------------------------------------------------


@pytest.fixture()
def config(tmp_path: Path) -> PyscvConfig:
    return PyscvConfig(
        package_name="testpkg",
        version="1.0.0",
        repo_slug="owner/repo",
        tag_prefix="v",
        dist_dir=tmp_path / "dist",
    )


@pytest.fixture()
def gh_assets() -> list[GhReleaseAsset]:
    return [
        GhReleaseAsset(
            name="pkg-1.0.0.whl", browser_download_url="https://github.com/pkg-1.0.0.whl"
        ),
        GhReleaseAsset(
            name="pkg-1.0.0.tar.gz", browser_download_url="https://github.com/pkg-1.0.0.tar.gz"
        ),
        GhReleaseAsset(
            name="pkg-1.0.0-SHA256SUMS.txt", browser_download_url="https://github.com/sums"
        ),
        GhReleaseAsset(
            name="pkg-1.0.0.whl.sigstore.json", browser_download_url="https://github.com/sig"
        ),
    ]


@pytest.fixture()
def pypi_files() -> list[PypiFileInfo]:
    return [
        PypiFileInfo(filename="pkg-1.0.0.whl", url="https://files.pythonhosted.org/pkg-1.0.0.whl"),
        PypiFileInfo(
            filename="pkg-1.0.0.tar.gz", url="https://files.pythonhosted.org/pkg-1.0.0.tar.gz"
        ),
    ]


@pytest.fixture()
def no_network_gh(monkeypatch, gh_assets):
    """Replace fetch_gh_release_assets with a fake returning gh_assets."""
    monkeypatch.setattr(
        "pyscv.download_artifacts.fetch_gh_release_assets",
        lambda *_a, **_kw: gh_assets,
    )


@pytest.fixture()
def no_network_pypi(monkeypatch, pypi_files):
    """Replace fetch_pypi_release_files with a fake returning pypi_files."""
    monkeypatch.setattr(
        "pyscv.download_artifacts.fetch_pypi_release_files",
        lambda *_a, **_kw: pypi_files,
    )


@pytest.fixture()
def fake_download(monkeypatch):
    """Replace atomic_download: records calls, creates empty dest files."""
    calls: list[tuple[str, Path]] = []

    def _download(url: str, dest: Path) -> None:
        calls.append((url, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake")

    monkeypatch.setattr("pyscv.download_artifacts.atomic_download", _download)
    return calls


# -- PyscvConfig.from_pyproject --------------------------------------------


@pytest.fixture()
def minimal_pyproject(tmp_path: Path) -> Path:
    """Write a minimal pyproject.toml and return its path."""
    p = tmp_path / "pyproject.toml"
    p.write_text(
        '[project]\nname = "mypkg"\nversion = "2.3.4"\n'
        "[project.urls]\n"
        '[tool.pyscv]\nrepo-slug = "me/mypkg"\ntag-prefix = "release-"\n'
        'use-testpypi = true\ndist-dir = "dist"\nproofs-dir = "proofs"\n'
    )
    return p


@pytest.fixture()
def bare_pyproject(tmp_path: Path) -> Path:
    """pyproject.toml with no [tool.pyscv] section."""
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "bare"\nversion = "0.1"\n')
    return p


def test_from_pyproject_parses_all_fields(minimal_pyproject):
    cfg = PyscvConfig.from_pyproject(minimal_pyproject)
    assert cfg.package_name == "mypkg"
    assert cfg.version == "2.3.4"
    assert cfg.repo_slug == "me/mypkg"
    assert cfg.tag_prefix == "release-"
    assert cfg.use_testpypi is True
    assert cfg.dist_dir == minimal_pyproject.resolve().parent / "dist"
    assert cfg.proofs_dir == minimal_pyproject.resolve().parent / "proofs"


def test_from_pyproject_without_pyscv_uses_project_fallbacks(bare_pyproject):
    """No [tool.pyscv] — package_name comes from [project], repo_slug empty."""
    cfg = PyscvConfig.from_pyproject(bare_pyproject)
    assert cfg.package_name == "bare"
    assert cfg.repo_slug == ""
    with pytest.raises(ValueError, match="missing required fields"):
        cfg.validate_required()


@pytest.mark.parametrize(
    ("use_testpypi", "expected_url", "expected_label"),
    [
        (False, "https://pypi.org", "PyPI"),
        (True, "https://test.pypi.org", "TestPyPI"),
    ],
)
def test_pypi_url_and_label(use_testpypi, expected_url, expected_label):
    cfg = PyscvConfig(package_name="x", version="1", repo_slug="o/r", use_testpypi=use_testpypi)
    assert cfg.pypi_base_url == expected_url
    assert cfg.pypi_label == expected_label


@pytest.mark.parametrize(
    ("prefix", "version", "override", "expected"),
    [
        ("v", "1.0", None, "v1.0"),
        ("v", "1.0", "2.0", "v2.0"),
        ("release-", "3.0a1", None, "release-3.0a1"),
    ],
)
def test_tag_formatting(prefix, version, override, expected):
    cfg = PyscvConfig(package_name="x", version=version, repo_slug="o/r", tag_prefix=prefix)
    assert cfg.tag(override) == expected


# -- fetch_gh_release_assets -----------------------------------------------


def _mock_response(json_data: dict) -> MagicMock:
    """Create a mock httpx response (no redirect)."""
    resp = MagicMock()
    resp.is_redirect = False
    resp.json.return_value = json_data
    return resp


@pytest.fixture()
def no_resolve(monkeypatch):
    """Mock resolve_url to return the URL unchanged (no HEAD requests)."""
    monkeypatch.setattr("pyscv.download_artifacts.resolve_url", lambda url, **_kw: url)


def test_fetch_gh_builds_correct_url(monkeypatch, config, no_resolve):
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return _mock_response({"assets": []})

    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", fake_get)
    fetch_gh_release_assets(config, "v1.0.0")
    assert captured["url"] == "https://api.github.com/repos/owner/repo/releases/tags/v1.0.0"


def test_fetch_gh_returns_assets(monkeypatch, config, no_resolve):
    resp = _mock_response(
        {
            "assets": [
                {"name": "a.whl", "browser_download_url": "https://github.com/a.whl"},
                {"name": "b.tar.gz", "browser_download_url": "https://github.com/b.tar.gz"},
            ]
        }
    )
    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", lambda *a, **kw: resp)
    assets = fetch_gh_release_assets(config, "v1")
    assert len(assets) == 2
    assert assets[0].name == "a.whl"


def test_fetch_gh_propagates_http_error(monkeypatch, config, no_resolve):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=404)
        )

    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", explode)
    with pytest.raises(httpx.HTTPStatusError):
        fetch_gh_release_assets(config, "v99")


# -- fetch_pypi_release_files ----------------------------------------------


def test_fetch_pypi_uses_configured_base_url(monkeypatch, no_resolve):
    cfg = PyscvConfig(package_name="mypkg", version="1", repo_slug="o/r", use_testpypi=True)
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return _mock_response({"urls": []})

    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", fake_get)
    fetch_pypi_release_files(cfg, "1.0")
    assert captured["url"] == "https://test.pypi.org/pypi/mypkg/1.0/json"


def test_fetch_pypi_returns_files(monkeypatch, config, no_resolve):
    resp = _mock_response(
        {
            "urls": [
                {"filename": "x.whl", "url": "https://files.pythonhosted.org/x.whl"},
                {"filename": "x.tar.gz", "url": "https://files.pythonhosted.org/x.tar.gz"},
            ]
        }
    )
    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", lambda *a, **kw: resp)
    files = fetch_pypi_release_files(config, "1")
    assert len(files) == 2
    assert files[0].filename == "x.whl"


def test_fetch_pypi_propagates_http_error(monkeypatch, config, no_resolve):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=404)
        )

    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", explode)
    with pytest.raises(httpx.HTTPStatusError):
        fetch_pypi_release_files(config, "99")


def test_fetch_gh_rejects_unexpected_redirect(monkeypatch, config, no_resolve):
    resp = MagicMock()
    resp.is_redirect = True
    resp.raise_for_status = MagicMock()
    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", lambda *_a, **_kw: resp)
    with pytest.raises(ValueError, match="Unexpected redirect"):
        fetch_gh_release_assets(config, "v1")


def test_fetch_pypi_rejects_unexpected_redirect(monkeypatch, config, no_resolve):
    resp = MagicMock()
    resp.is_redirect = True
    resp.raise_for_status = MagicMock()
    monkeypatch.setattr("pyscv.download_artifacts.httpx.get", lambda *_a, **_kw: resp)
    with pytest.raises(ValueError, match="Unexpected redirect"):
        fetch_pypi_release_files(config, "1.0")


# -- download_from_gh -----------------------------------------------------


def test_gh_dry_run_creates_nothing(config, no_network_gh):
    assert download_from_gh(config, "1.0.0", (".whl", ".tar.gz"), dry_run=True) == 0
    assert not config.dist_dir.exists()


@pytest.mark.parametrize(
    ("extensions", "expected_count"),
    [
        ((".whl",), 1),
        ((".tar.gz",), 1),
        ((".whl", ".tar.gz"), 2),
        ((".zip",), 0),
    ],
)
def test_gh_filters_by_extension(config, no_network_gh, fake_download, extensions, expected_count):
    assert download_from_gh(config, "1.0.0", extensions) == 0
    assert len(fake_download) == expected_count


def test_gh_skips_existing_without_force(config, no_network_gh, fake_download):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_gh(config, "1.0.0", (".whl", ".tar.gz")) == 0
    assert len(fake_download) == 1
    assert fake_download[0][1].name == "pkg-1.0.0.tar.gz"


def test_gh_force_overwrites_existing(config, no_network_gh, fake_download):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_gh(config, "1.0.0", (".whl", ".tar.gz"), force=True) == 0
    assert len(fake_download) == 2


def test_gh_verbose_logs_skips_and_progress(config, no_network_gh, fake_download):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_gh(config, "1.0.0", (".whl", ".tar.gz"), verbose=True) == 0
    assert len(fake_download) == 1  # tar.gz only, whl skipped


def test_gh_download_failure_returns_1(config, no_network_gh, monkeypatch):
    def failing_download(_url, _dest):
        raise httpx.HTTPStatusError(
            "fail", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_artifacts.atomic_download", failing_download)
    assert download_from_gh(config, "1.0.0", (".whl",)) == 1


def test_gh_dry_run_shows_exists_status(config, no_network_gh):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_gh(config, "1.0.0", (".whl", ".tar.gz"), dry_run=True) == 0


def test_gh_returns_1_on_fetch_error(monkeypatch, config):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "nope", request=MagicMock(), response=MagicMock(status_code=404)
        )

    monkeypatch.setattr("pyscv.download_artifacts.fetch_gh_release_assets", explode)
    assert download_from_gh(config, "99", (".whl",)) == 1


# -- download_from_pypi ---------------------------------------------------


def test_pypi_dry_run_creates_nothing(config, no_network_pypi):
    assert download_from_pypi(config, "1.0.0", (".whl", ".tar.gz"), dry_run=True) == 0
    assert not config.dist_dir.exists()


@pytest.mark.parametrize(
    ("extensions", "expected_count"),
    [
        ((".whl",), 1),
        ((".tar.gz",), 1),
        ((".whl", ".tar.gz"), 2),
        ((".zip",), 0),
    ],
)
def test_pypi_filters_by_extension(
    config, no_network_pypi, fake_download, extensions, expected_count
):
    assert download_from_pypi(config, "1.0.0", extensions) == 0
    assert len(fake_download) == expected_count


def test_pypi_skips_existing_without_force(config, no_network_pypi, fake_download):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_pypi(config, "1.0.0", (".whl", ".tar.gz")) == 0
    assert len(fake_download) == 1


def test_pypi_verbose_logs_skips_and_progress(config, no_network_pypi, fake_download):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_pypi(config, "1.0.0", (".whl", ".tar.gz"), verbose=True) == 0
    assert len(fake_download) == 1


def test_pypi_verbose_logs_non_matching_extensions(config, no_network_pypi, fake_download):
    assert download_from_pypi(config, "1.0.0", (".zip",), verbose=True) == 0
    assert len(fake_download) == 0


def test_pypi_force_overwrites(config, no_network_pypi, fake_download):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_pypi(config, "1.0.0", (".whl", ".tar.gz"), force=True) == 0
    assert len(fake_download) == 2


def test_pypi_download_failure_returns_1(config, no_network_pypi, monkeypatch):
    def failing_download(_url, _dest):
        raise httpx.HTTPStatusError(
            "fail", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_artifacts.atomic_download", failing_download)
    assert download_from_pypi(config, "1.0.0", (".whl",)) == 1


def test_pypi_dry_run_shows_exists_status(config, no_network_pypi):
    config.dist_dir.mkdir(parents=True)
    (config.dist_dir / "pkg-1.0.0.whl").write_bytes(b"old")
    assert download_from_pypi(config, "1.0.0", (".whl", ".tar.gz"), dry_run=True) == 0


def test_pypi_returns_1_on_fetch_error(monkeypatch, config):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "nope", request=MagicMock(), response=MagicMock(status_code=404)
        )

    monkeypatch.setattr("pyscv.download_artifacts.fetch_pypi_release_files", explode)
    assert download_from_pypi(config, "99", (".whl",)) == 1


def test_gh_unsafe_filename_returns_1(config, monkeypatch, fake_download):
    monkeypatch.setattr(
        "pyscv.download_artifacts.fetch_gh_release_assets",
        lambda *_a, **_kw: [
            GhReleaseAsset(name="../evil.whl", browser_download_url="https://github.com/x"),
        ],
    )
    assert download_from_gh(config, "1.0.0", (".whl",)) == 1


def test_pypi_unsafe_filename_returns_1(config, monkeypatch, fake_download):
    monkeypatch.setattr(
        "pyscv.download_artifacts.fetch_pypi_release_files",
        lambda *_a, **_kw: [
            PypiFileInfo(filename="../evil.whl", url="https://pypi.org/x"),
        ],
    )
    assert download_from_pypi(config, "1.0.0", (".whl",)) == 1


def test_gh_raises_if_dist_dir_is_none():
    cfg = PyscvConfig(package_name="pkg", version="1.0", repo_slug="o/r")
    with pytest.raises(ValueError, match="dist_dir is required"):
        download_from_gh(cfg, "1.0", (".whl",))


def test_pypi_raises_if_dist_dir_is_none():
    cfg = PyscvConfig(package_name="pkg", version="1.0", repo_slug="o/r")
    with pytest.raises(ValueError, match="dist_dir is required"):
        download_from_pypi(cfg, "1.0", (".whl",))


# (URL validation, safe_filename, resolve_url, and atomic_download tests
# are in tests/test_net.py — they test pyscv.net primitives directly.)


# -- Config error handling -------------------------------------------------


def test_from_pyproject_missing_file(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        PyscvConfig.from_pyproject(tmp_path / "nonexistent.toml")


def test_from_pyproject_invalid_toml(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text("this is not valid toml [[[")
    with pytest.raises(ValueError, match="not valid TOML"):
        PyscvConfig.from_pyproject(p)


def test_from_pyproject_invalid_pyscv_values(tmp_path):
    """Bad field types in [tool.pyscv] raise ValueError."""
    p = tmp_path / "pyproject.toml"
    p.write_text("""\
[project]
name = "pkg"
version = "1.0"

[tool.pyscv]
repo-slug = "o/r"
use-testpypi = "not-a-bool"
""")
    with pytest.raises(ValueError, match="invalid"):
        PyscvConfig.from_pyproject(p)


def test_validate_required_catches_missing_fields(tmp_path):
    """No [project] and no [tool.pyscv] — validate_required catches it."""
    p = tmp_path / "pyproject.toml"
    p.write_text("[tool]\nfoo = 1\n")
    config = PyscvConfig.from_pyproject(p)
    assert config.package_name == ""
    with pytest.raises(ValueError, match="missing required fields"):
        config.validate_required()


def test_with_overrides_fills_version():
    cfg = PyscvConfig(package_name="pkg", repo_slug="o/r")
    assert cfg.version == ""
    updated = cfg.with_overrides(version="1.2.3")
    assert updated.version == "1.2.3"


def test_with_overrides_skips_none():
    cfg = PyscvConfig(package_name="pkg", version="1.0", repo_slug="o/r")
    same = cfg.with_overrides(version=None)
    assert same.version == "1.0"  # None doesn't override


def test_with_overrides_allows_falsy():
    cfg = PyscvConfig(package_name="pkg", version="1.0", repo_slug="o/r", use_testpypi=True)
    updated = cfg.with_overrides(use_testpypi=False)
    assert updated.use_testpypi is False  # False is not None, so it overrides


def test_gh_returns_1_on_value_error(monkeypatch, config):
    def explode(*_a, **_kw):
        raise ValueError

    monkeypatch.setattr("pyscv.download_artifacts.fetch_gh_release_assets", explode)
    assert download_from_gh(config, "1.0.0", (".whl",)) == 1


def test_pypi_returns_1_on_value_error(monkeypatch, config):
    def explode(*_a, **_kw):
        raise ValueError

    monkeypatch.setattr("pyscv.download_artifacts.fetch_pypi_release_files", explode)
    assert download_from_pypi(config, "1.0.0", (".whl",)) == 1


# -- CLI (typer app) -------------------------------------------------------


@pytest.fixture()
def cli_pyproject(tmp_path: Path) -> Path:
    """Write a valid pyproject.toml for CLI tests."""
    p = tmp_path / "pyproject.toml"
    p.write_text("""\
[project]
name = "clipkg"
version = "1.0.0"

[tool.pyscv]
repo-slug = "owner/clipkg"
dist-dir = "dist"
proofs-dir = "proofs"
""")
    return p


@pytest.fixture()
def cli_runner():
    from typer.testing import CliRunner

    return CliRunner()


def test_cli_gh_dry_run(cli_runner, cli_pyproject, monkeypatch):
    monkeypatch.setattr(
        "pyscv.download_artifacts.fetch_gh_release_assets",
        lambda *_a, **_kw: [
            GhReleaseAsset(name="clipkg-1.0.0.whl", browser_download_url="https://github.com/x"),
        ],
    )
    result = cli_runner.invoke(app, ["1.0.0", "--dry-run", "--pyproject", str(cli_pyproject)])
    assert result.exit_code == 0
    assert "clipkg-1.0.0.whl" in result.output


def test_cli_pypi_dry_run_with_testpypi_warning(cli_runner, tmp_path, monkeypatch):
    p = tmp_path / "pyproject.toml"
    p.write_text("""\
[project]
name = "clipkg"
version = "1.0.0"

[tool.pyscv]
repo-slug = "owner/clipkg"
use-testpypi = true
dist-dir = "dist"
proofs-dir = "proofs"
""")
    monkeypatch.setattr(
        "pyscv.download_artifacts.fetch_pypi_release_files",
        lambda *_a, **_kw: [],
    )
    result = cli_runner.invoke(
        app, ["1.0.0", "--source", "pypi", "--dry-run", "--pyproject", str(p)]
    )
    assert result.exit_code == 0
    assert "TestPyPI" in result.output
    assert "WARNING" in result.output


def test_cli_no_testpypi_warning_for_gh(cli_runner, tmp_path, monkeypatch):
    p = tmp_path / "pyproject.toml"
    p.write_text("""\
[project]
name = "clipkg"
version = "1.0.0"

[tool.pyscv]
repo-slug = "owner/clipkg"
use-testpypi = true
dist-dir = "dist"
proofs-dir = "proofs"
""")
    monkeypatch.setattr(
        "pyscv.download_artifacts.fetch_gh_release_assets",
        lambda *_a, **_kw: [],
    )
    result = cli_runner.invoke(app, ["1.0.0", "--dry-run", "--pyproject", str(p)])
    assert result.exit_code == 0
    assert "WARNING" not in result.output


def test_cli_missing_pyproject(cli_runner, tmp_path):
    result = cli_runner.invoke(app, ["1.0.0", "--pyproject", str(tmp_path / "nope.toml")])
    assert result.exit_code == 1
    assert "cannot read" in result.output


def test_cli_missing_version_fails_validation(cli_runner, tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text("""\
[project]
name = "clipkg"

[tool.pyscv]
repo-slug = "owner/clipkg"
dist-dir = "dist"
proofs-dir = "proofs"
""")
    result = cli_runner.invoke(app, ["--pyproject", str(p)])
    assert result.exit_code == 1
    assert "missing required fields" in result.output


def test_cli_default_version_from_pyproject(cli_runner, cli_pyproject, monkeypatch):
    """When no version argument is given, use version from pyproject.toml."""
    captured = {}

    def fake_fetch(config, tag):
        captured["tag"] = tag
        return []

    monkeypatch.setattr("pyscv.download_artifacts.fetch_gh_release_assets", fake_fetch)
    result = cli_runner.invoke(app, ["--dry-run", "--pyproject", str(cli_pyproject)])
    assert result.exit_code == 0
    assert captured["tag"] == "v1.0.0"
    assert "1.0.0" in result.output


def test_cli_version_override(cli_runner, cli_pyproject, monkeypatch):
    captured = {}

    def fake_fetch(config, tag):
        captured["tag"] = tag
        return []

    monkeypatch.setattr("pyscv.download_artifacts.fetch_gh_release_assets", fake_fetch)
    result = cli_runner.invoke(app, ["9.9.9", "--dry-run", "--pyproject", str(cli_pyproject)])
    assert result.exit_code == 0
    assert captured["tag"] == "v9.9.9"
    assert "9.9.9" in result.output
