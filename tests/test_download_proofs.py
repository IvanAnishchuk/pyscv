"""Tests for pyscv.download_proofs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from pyscv.config import PyscvConfig
from pyscv.download_artifacts import GhReleaseAsset, PypiFileInfo
from pyscv.download_proofs import (
    ProofSource,
    _extract_cosign_bundle,
    _is_dist_file,
    app,
    download_gh_release_proofs,
    download_proofs,
    download_pypi_proofs,
    fetch_gh_attestation,
    fetch_gh_attestations,
    fetch_pypi_provenance,
    proofs_source_dir,
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
        proofs_dir=tmp_path / "proofs",
    )


@pytest.fixture()
def dist_files(config: PyscvConfig) -> list[Path]:
    """Create fake dist files in dist_dir."""
    if config.dist_dir is None:
        msg = "dist_dir is required in test config"
        raise ValueError(msg)
    config.dist_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for name in ["testpkg-1.0.0.whl", "testpkg-1.0.0.tar.gz"]:
        f = config.dist_dir / name
        f.write_bytes(b"fake-dist-content")
        files.append(f)
    return files


@pytest.fixture()
def gh_assets() -> list[GhReleaseAsset]:
    return [
        GhReleaseAsset(name="testpkg-1.0.0.whl", browser_download_url="https://github.com/whl"),
        GhReleaseAsset(
            name="testpkg-1.0.0.tar.gz", browser_download_url="https://github.com/sdist"
        ),
        GhReleaseAsset(name="SHA256SUMS.txt", browser_download_url="https://github.com/sums"),
        GhReleaseAsset(
            name="testpkg-1.0.0.whl.sigstore.json",
            browser_download_url="https://github.com/sig",
        ),
    ]


@pytest.fixture()
def no_network_gh(monkeypatch, gh_assets):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_release_assets", lambda *_a, **_kw: gh_assets
    )


@pytest.fixture()
def fake_download(monkeypatch):
    """Replace atomic_download: records calls, creates empty dest files."""
    calls: list[tuple[str, Path]] = []

    def _download(url: str, dest: Path) -> None:
        calls.append((url, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake")

    monkeypatch.setattr("pyscv.download_proofs.atomic_download", _download)
    return calls


@pytest.fixture()
def no_resolve(monkeypatch):
    monkeypatch.setattr("pyscv.download_proofs.resolve_url", lambda url, **_kw: url)


@pytest.fixture()
def pypi_files() -> list[PypiFileInfo]:
    return [
        PypiFileInfo(filename="testpkg-1.0.0.whl", url="https://files.pythonhosted.org/whl"),
        PypiFileInfo(filename="testpkg-1.0.0.tar.gz", url="https://files.pythonhosted.org/sdist"),
    ]


@pytest.fixture()
def no_network_pypi(monkeypatch, pypi_files):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_release_files", lambda *_a, **_kw: pypi_files
    )


# -- proofs_source_dir -----------------------------------------------------


def test_proofs_source_dir(config):
    result = proofs_source_dir(config, "1.0.0", "github")
    assert result == config.proofs_dir / "1.0.0" / "github"


def test_proofs_source_dir_raises_if_proofs_dir_none():
    cfg = PyscvConfig(package_name="pkg", version="1.0", repo_slug="o/r")
    with pytest.raises(ValueError, match="proofs_dir is required"):
        proofs_source_dir(cfg, "1.0", "github")


# -- _is_dist_file ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pkg.whl", True),
        ("pkg.tar.gz", True),
        ("SHA256SUMS.txt", False),
        ("pkg.sigstore.json", False),
    ],
)
def test_is_dist_file(name, expected):
    assert _is_dist_file(name) is expected


# -- download_gh_release_proofs --------------------------------------------


def test_gh_release_proofs_dry_run(config, no_network_gh, fake_download):
    code = download_gh_release_proofs(config, "1.0.0", dry_run=True)
    assert code == 0
    # No files created in dry run
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    assert not gh_dir.exists()


def test_gh_release_proofs_downloads_all(config, no_network_gh, fake_download):
    code = download_gh_release_proofs(config, "1.0.0")
    assert code == 0
    assert len(fake_download) == 4  # 2 dist + SHA256SUMS + sigstore


def test_gh_release_proofs_skips_existing(config, no_network_gh, fake_download):
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    gh_dir.mkdir(parents=True)
    (gh_dir / "SHA256SUMS.txt").write_bytes(b"old")
    code = download_gh_release_proofs(config, "1.0.0")
    assert code == 0
    assert len(fake_download) == 3  # skipped SHA256SUMS.txt


def test_gh_release_proofs_force_overwrites(config, no_network_gh, fake_download):
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    gh_dir.mkdir(parents=True)
    (gh_dir / "SHA256SUMS.txt").write_bytes(b"old")
    code = download_gh_release_proofs(config, "1.0.0", force=True)
    assert code == 0
    assert len(fake_download) == 4


def test_gh_release_proofs_dry_run_shows_exists(config, no_network_gh, fake_download):
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    gh_dir.mkdir(parents=True)
    (gh_dir / "SHA256SUMS.txt").write_bytes(b"old")
    code = download_gh_release_proofs(config, "1.0.0", dry_run=True)
    assert code == 0


def test_gh_release_proofs_verbose(config, no_network_gh, fake_download):
    # Existing file + verbose shows skip message
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    gh_dir.mkdir(parents=True)
    (gh_dir / "SHA256SUMS.txt").write_bytes(b"old")
    code = download_gh_release_proofs(config, "1.0.0", verbose=True)
    assert code == 0


def test_gh_release_proofs_fetch_error(config, monkeypatch):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=404)
        )

    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_release_assets", explode)
    assert download_gh_release_proofs(config, "1.0.0") == 1


def test_gh_release_proofs_download_error(config, no_network_gh, monkeypatch):
    def failing_download(_url, _dest):
        raise httpx.HTTPStatusError(
            "fail", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_proofs.atomic_download", failing_download)
    assert download_gh_release_proofs(config, "1.0.0") == 1


def test_gh_release_proofs_unsafe_filename(config, monkeypatch, fake_download):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_release_assets",
        lambda *_a, **_kw: [
            GhReleaseAsset(name="../evil.whl", browser_download_url="https://github.com/x"),
        ],
    )
    assert download_gh_release_proofs(config, "1.0.0") == 1


# -- fetch_gh_attestation (single artifact) --------------------------------


def test_fetch_gh_attestation_returns_list(config, tmp_path, monkeypatch, no_resolve):
    artifact = tmp_path / "fake.whl"
    artifact.write_bytes(b"content")

    attestations = [{"bundle": {"key": "val"}}]
    resp = MagicMock()
    resp.status_code = 200
    resp.is_redirect = False
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"attestations": attestations}
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)

    result = fetch_gh_attestation(config, artifact)
    assert result == attestations


def test_fetch_gh_attestation_returns_none_on_404(config, tmp_path, monkeypatch, no_resolve):
    artifact = tmp_path / "fake.whl"
    artifact.write_bytes(b"content")

    resp = MagicMock()
    resp.status_code = 404
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)

    result = fetch_gh_attestation(config, artifact)
    assert result is None


def test_fetch_gh_attestation_rejects_redirect(config, tmp_path, monkeypatch, no_resolve):
    artifact = tmp_path / "fake.whl"
    artifact.write_bytes(b"content")

    resp = MagicMock()
    resp.status_code = 200
    resp.is_redirect = True
    resp.raise_for_status = MagicMock()
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)

    with pytest.raises(ValueError, match="Unexpected redirect"):
        fetch_gh_attestation(config, artifact)


def test_fetch_gh_attestation_raises_on_error(config, tmp_path, monkeypatch, no_resolve):
    artifact = tmp_path / "fake.whl"
    artifact.write_bytes(b"content")

    resp = MagicMock()
    resp.status_code = 500
    resp.is_redirect = False
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock(status_code=500)
    )
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)

    with pytest.raises(httpx.HTTPStatusError):
        fetch_gh_attestation(config, artifact)


# -- fetch_gh_attestations (orchestrator) ----------------------------------


def _make_gh_dir_with_artifacts(config):
    """Create the github proofs dir with fake artifact copies."""
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    gh_dir.mkdir(parents=True)
    (gh_dir / "testpkg-1.0.0.whl").write_bytes(b"fake")
    (gh_dir / "testpkg-1.0.0.tar.gz").write_bytes(b"fake")
    return ["testpkg-1.0.0.whl", "testpkg-1.0.0.tar.gz"]


def test_gh_attestations_saves_and_extracts(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)
    attestations = [{"bundle": {"key": "value"}}]
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation", lambda *_a, **_kw: attestations
    )
    code = fetch_gh_attestations(config, "1.0.0", filenames)
    assert code == 0

    gh_dir = config.proofs_dir / "1.0.0" / "github"
    att_file = gh_dir / "testpkg-1.0.0.whl.gh-attestation.json"
    bundle_file = gh_dir / "testpkg-1.0.0.whl.gh-attestation-bundle.json"
    assert att_file.exists()
    assert bundle_file.exists()
    assert json.loads(bundle_file.read_text()) == {"key": "value"}


def test_gh_attestations_api_error_fails(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)

    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError("err", request=MagicMock(), response=MagicMock(status_code=500))

    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_attestation", explode)
    code = fetch_gh_attestations(config, "1.0.0", filenames)
    assert code == 1


def test_gh_attestations_none_result_fails(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)
    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_attestation", lambda *_a, **_kw: None)
    code = fetch_gh_attestations(config, "1.0.0", filenames)
    assert code == 1


def test_gh_attestations_empty_list_fails(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)
    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_attestation", lambda *_a, **_kw: [])
    code = fetch_gh_attestations(config, "1.0.0", filenames)
    assert code == 1


def test_gh_attestations_missing_bundle_key(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation",
        lambda *_a, **_kw: [{"no_bundle": True}],
    )
    code = fetch_gh_attestations(config, "1.0.0", filenames)
    assert code == 1  # missing bundle key is a hard failure


def test_gh_attestations_dry_run(config):
    filenames = ["testpkg-1.0.0.whl", "testpkg-1.0.0.tar.gz"]
    code = fetch_gh_attestations(config, "1.0.0", filenames, dry_run=True)
    assert code == 0
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    assert not gh_dir.exists()


def test_gh_attestations_skip_existing(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    # Both att_file AND bundle_file must exist to skip
    (gh_dir / "testpkg-1.0.0.whl.gh-attestation.json").write_text("[]")
    (gh_dir / "testpkg-1.0.0.whl.gh-attestation-bundle.json").write_text("{}")
    (gh_dir / "testpkg-1.0.0.tar.gz.gh-attestation.json").write_text("[]")
    (gh_dir / "testpkg-1.0.0.tar.gz.gh-attestation-bundle.json").write_text("{}")

    call_count = {"n": 0}

    def counting_fetch(*_a, **_kw):
        call_count["n"] += 1
        return [{"bundle": {}}]

    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_attestation", counting_fetch)
    code = fetch_gh_attestations(config, "1.0.0", filenames)
    assert code == 0
    assert call_count["n"] == 0  # both skipped


def test_gh_attestations_skip_existing_verbose(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    (gh_dir / "testpkg-1.0.0.whl.gh-attestation.json").write_text("[]")
    (gh_dir / "testpkg-1.0.0.whl.gh-attestation-bundle.json").write_text("{}")
    (gh_dir / "testpkg-1.0.0.tar.gz.gh-attestation.json").write_text("[]")
    (gh_dir / "testpkg-1.0.0.tar.gz.gh-attestation-bundle.json").write_text("{}")
    code = fetch_gh_attestations(config, "1.0.0", filenames, verbose=True)
    assert code == 0


def test_gh_attestations_artifact_not_downloaded_fails(config):
    """When artifact copy doesn't exist in proofs dir, fail."""
    filenames = ["testpkg-1.0.0.whl"]
    gh_dir = config.proofs_dir / "1.0.0" / "github"
    gh_dir.mkdir(parents=True)
    code = fetch_gh_attestations(config, "1.0.0", filenames)
    assert code == 1


def test_gh_attestations_verbose(config, monkeypatch):
    filenames = _make_gh_dir_with_artifacts(config)
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation",
        lambda *_a, **_kw: [{"bundle": {"key": "value"}}],
    )
    code = fetch_gh_attestations(config, "1.0.0", filenames, verbose=True)
    assert code == 0


def test_pypi_provenance_rejects_redirect(monkeypatch, config, no_resolve):
    resp = MagicMock()
    resp.status_code = 200
    resp.is_redirect = True
    resp.raise_for_status = MagicMock()
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)
    with pytest.raises(ValueError, match="Unexpected redirect"):
        fetch_pypi_provenance(config, "1.0.0", "pkg.whl", "https://pypi.org")


def test_pypi_proofs_skips_non_dist_files(config, monkeypatch, fake_download, no_resolve):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_release_files",
        lambda *_a, **_kw: [
            PypiFileInfo(filename="testpkg-1.0.0.whl", url="https://files.pythonhosted.org/whl"),
            PypiFileInfo(filename="metadata.json", url="https://files.pythonhosted.org/meta"),
        ],
    )
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 0
    # Only the whl should be downloaded, not metadata.json
    assert len(fake_download) == 1


def test_pypi_provenance_returns_dict(monkeypatch, config, no_resolve):
    resp = MagicMock()
    resp.status_code = 200
    resp.is_redirect = False
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"attestation_bundles": []}
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)

    result = fetch_pypi_provenance(config, "1.0.0", "pkg.whl", "https://pypi.org")
    assert result == {"attestation_bundles": []}


def test_pypi_provenance_returns_none_on_404(monkeypatch, config, no_resolve):
    resp = MagicMock()
    resp.status_code = 404
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)

    result = fetch_pypi_provenance(config, "1.0.0", "pkg.whl", "https://pypi.org")
    assert result is None


def test_pypi_provenance_raises_on_other_error(monkeypatch, config, no_resolve):
    resp = MagicMock()
    resp.status_code = 500
    resp.is_redirect = False
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock(status_code=500)
    )
    monkeypatch.setattr("pyscv.download_proofs.httpx.get", lambda *_a, **_kw: resp)

    with pytest.raises(httpx.HTTPStatusError):
        fetch_pypi_provenance(config, "1.0.0", "pkg.whl", "https://pypi.org")


# -- _extract_cosign_bundle ------------------------------------------------


def test_extract_cosign_bundle_structure():
    att = {
        "verification_material": {
            "certificate": "cert_bytes",
            "transparency_entries": [{"entry": 1}],
        },
        "envelope": {
            "statement": "payload_data",
            "signature": "sig_data",
        },
    }
    bundle = _extract_cosign_bundle(att)
    assert bundle["mediaType"] == "application/vnd.dev.sigstore.bundle.v0.3+json"
    assert bundle["verificationMaterial"]["certificate"]["rawBytes"] == "cert_bytes"
    assert bundle["verificationMaterial"]["tlogEntries"] == [{"entry": 1}]
    assert bundle["dsseEnvelope"]["payload"] == "payload_data"
    assert bundle["dsseEnvelope"]["signatures"][0]["sig"] == "sig_data"


def test_extract_cosign_bundle_missing_key():
    with pytest.raises(KeyError):
        _extract_cosign_bundle({"verification_material": {}})


# -- download_pypi_proofs --------------------------------------------------


def _make_provenance(*, with_bundles: bool = True) -> dict:
    """Create a fake PyPI provenance response."""
    if not with_bundles:
        return {"attestation_bundles": []}
    return {
        "attestation_bundles": [
            {
                "attestations": [
                    {
                        "verification_material": {
                            "certificate": "cert",
                            "transparency_entries": [{"e": 1}],
                        },
                        "envelope": {
                            "statement": "stmt",
                            "signature": "sig",
                        },
                    }
                ]
            }
        ]
    }


def test_pypi_proofs_full_pipeline(config, no_network_pypi, fake_download, monkeypatch, no_resolve):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 0

    pypi_dir = config.proofs_dir / "1.0.0" / "pypi"
    # Artifact copies downloaded
    assert len(fake_download) == 2

    # Provenance files created
    assert (pypi_dir / "testpkg-1.0.0.whl.provenance.json").exists()
    assert (pypi_dir / "testpkg-1.0.0.whl.publish.attestation").exists()
    assert (pypi_dir / "testpkg-1.0.0.whl.cosign-bundle.json").exists()


def test_pypi_proofs_warns_on_multiple_bundles(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve, capsys
):
    """Multiple bundles triggers a warning but still succeeds (first bundle used)."""
    multi_bundle_prov = {
        "attestation_bundles": [
            _make_provenance()["attestation_bundles"][0],
            _make_provenance()["attestation_bundles"][0],
        ]
    }
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: multi_bundle_prov,
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 0


def test_pypi_proofs_no_provenance_fails(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: None,
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 1


def test_pypi_proofs_empty_bundles_fails(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(with_bundles=False),
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 1


def test_pypi_proofs_dry_run(config, no_network_pypi, monkeypatch, no_resolve):
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org", dry_run=True)
    assert code == 0
    pypi_dir = config.proofs_dir / "1.0.0" / "pypi"
    assert not pypi_dir.exists()


def test_pypi_proofs_verbose(config, no_network_pypi, fake_download, monkeypatch, no_resolve):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org", verbose=True)
    assert code == 0


def test_pypi_proofs_skip_existing_provenance(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    pypi_dir = config.proofs_dir / "1.0.0" / "pypi"
    pypi_dir.mkdir(parents=True)
    # All three derived files must exist to skip
    for name in ["testpkg-1.0.0.whl", "testpkg-1.0.0.tar.gz"]:
        (pypi_dir / f"{name}.provenance.json").write_text("{}")
        (pypi_dir / f"{name}.publish.attestation").write_text("{}")
        (pypi_dir / f"{name}.cosign-bundle.json").write_text("{}")
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org", verbose=True)
    assert code == 0


def test_pypi_proofs_skip_existing_artifact(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    pypi_dir = config.proofs_dir / "1.0.0" / "pypi"
    pypi_dir.mkdir(parents=True)
    (pypi_dir / "testpkg-1.0.0.whl").write_bytes(b"existing")
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org", verbose=True)
    assert code == 0


def test_pypi_proofs_skip_existing_artifact_non_verbose(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    pypi_dir = config.proofs_dir / "1.0.0" / "pypi"
    pypi_dir.mkdir(parents=True)
    (pypi_dir / "testpkg-1.0.0.whl").write_bytes(b"existing")
    (pypi_dir / "testpkg-1.0.0.tar.gz").write_bytes(b"existing")
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 0


def test_pypi_proofs_skip_existing_provenance_non_verbose(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    pypi_dir = config.proofs_dir / "1.0.0" / "pypi"
    pypi_dir.mkdir(parents=True)
    for name in ["testpkg-1.0.0.whl", "testpkg-1.0.0.tar.gz"]:
        (pypi_dir / f"{name}.provenance.json").write_text("{}")
        (pypi_dir / f"{name}.publish.attestation").write_text("{}")
        (pypi_dir / f"{name}.cosign-bundle.json").write_text("{}")
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 0


def test_pypi_proofs_cosign_bundle_missing_key(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    """Malformed attestation with missing verification_material keys."""
    bad_prov = {
        "attestation_bundles": [
            {
                "attestations": [
                    {
                        "verification_material": {},  # missing certificate, transparency_entries
                        "envelope": {"statement": "s", "signature": "sig"},
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: bad_prov,
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 1  # malformed attestation is a hard failure


def test_pypi_proofs_empty_attestations_list(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    prov = {"attestation_bundles": [{"attestations": []}]}
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: prov,
    )
    code = download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org")
    assert code == 1  # empty attestations is a hard failure


def test_pypi_proofs_artifact_download_error(config, no_network_pypi, monkeypatch, no_resolve):
    def failing_download(_url, _dest):
        raise httpx.HTTPStatusError(
            "fail", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_proofs.atomic_download", failing_download)
    assert download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org") == 1


def test_pypi_proofs_unsafe_filename(config, monkeypatch, no_resolve):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_release_files",
        lambda *_a, **_kw: [PypiFileInfo(filename="../evil.whl", url="https://pypi.org/x")],
    )
    assert download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org") == 1


def test_pypi_proofs_fetch_error(config, monkeypatch):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_proofs.fetch_pypi_release_files", explode)
    assert download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org") == 1


def test_pypi_proofs_provenance_fetch_error(
    config, no_network_pypi, fake_download, monkeypatch, no_resolve
):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_proofs.fetch_pypi_provenance", explode)
    assert download_pypi_proofs(config, "1.0.0", "pypi", "https://pypi.org") == 1


# -- download_proofs orchestrator ------------------------------------------


def test_download_proofs_github_only(config, monkeypatch, no_network_gh, fake_download):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation",
        lambda *_a, **_kw: [{"bundle": {"key": "val"}}],
    )
    code = download_proofs(config, "1.0.0", ProofSource.github)
    assert code == 0


def test_download_proofs_pypi_only(config, monkeypatch, no_network_pypi, fake_download, no_resolve):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_proofs(config, "1.0.0", ProofSource.pypi)
    assert code == 0


def test_download_proofs_testpypi_only(
    config, monkeypatch, no_network_pypi, fake_download, no_resolve
):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_proofs(config, "1.0.0", ProofSource.testpypi)
    assert code == 0


def test_download_proofs_unknown_source(config):
    with pytest.raises(ValueError, match="Unknown proof source"):
        download_proofs(config, "1.0.0", "bogus")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_download_proofs_fails_on_attestation_error(
    config, monkeypatch, no_network_gh, fake_download
):
    """If attestation step fails, orchestrator returns 1."""

    def fail_attestations(*_a, **_kw):
        return 1

    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_attestations", fail_attestations)
    assert download_proofs(config, "1.0.0", ProofSource.github) == 1


def test_download_proofs_fails_on_pypi_error(config, monkeypatch):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_proofs.fetch_pypi_release_files", explode)
    assert download_proofs(config, "1.0.0", ProofSource.pypi) == 1


def test_download_proofs_fails_on_testpypi_error(config, monkeypatch):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=500)
        )

    monkeypatch.setattr("pyscv.download_proofs.fetch_pypi_release_files", explode)
    assert download_proofs(config, "1.0.0", ProofSource.testpypi) == 1


def test_download_proofs_fails_on_gh_error(config, monkeypatch):
    def explode(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=404)
        )

    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_release_assets", explode)
    assert download_proofs(config, "1.0.0", ProofSource.github) == 1


def test_download_proofs_all_sources(
    config, monkeypatch, no_network_gh, fake_download, no_network_pypi, no_resolve
):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation",
        lambda *_a, **_kw: [{"bundle": {"key": "val"}}],
    )
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: _make_provenance(),
    )
    code = download_proofs(config, "1.0.0", ProofSource.all)
    assert code == 0


# -- CLI -------------------------------------------------------------------


@pytest.fixture()
def cli_pyproject(tmp_path: Path) -> Path:
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


def test_cli_dry_run(cli_runner, cli_pyproject, monkeypatch):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_release_assets",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation",
        lambda *_a, **_kw: [{"bundle": {"key": "val"}}],
    )
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_release_files",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_pypi_provenance",
        lambda *_a, **_kw: None,
    )
    result = cli_runner.invoke(app, ["1.0.0", "--dry-run", "--pyproject", str(cli_pyproject)])
    assert result.exit_code == 0


def test_cli_source_github(cli_runner, cli_pyproject, monkeypatch):
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_release_assets",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation",
        lambda *_a, **_kw: [{"bundle": {"key": "val"}}],
    )
    result = cli_runner.invoke(
        app, ["1.0.0", "--source", "github", "--dry-run", "--pyproject", str(cli_pyproject)]
    )
    assert result.exit_code == 0


def test_cli_missing_pyproject(cli_runner, tmp_path):
    result = cli_runner.invoke(app, ["1.0.0", "--pyproject", str(tmp_path / "nope.toml")])
    assert result.exit_code == 1
    assert "cannot read" in result.output


def test_cli_missing_version(cli_runner, tmp_path):
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
    captured = {}

    def fake_fetch(config, tag):
        captured["tag"] = tag
        return []

    monkeypatch.setattr("pyscv.download_proofs.fetch_gh_release_assets", fake_fetch)
    monkeypatch.setattr(
        "pyscv.download_proofs.fetch_gh_attestation",
        lambda *_a, **_kw: [{"bundle": {"key": "val"}}],
    )
    monkeypatch.setattr("pyscv.download_proofs.fetch_pypi_release_files", lambda *_a, **_kw: [])
    result = cli_runner.invoke(app, ["--dry-run", "--pyproject", str(cli_pyproject)])
    assert result.exit_code == 0
    assert captured["tag"] == "v1.0.0"
