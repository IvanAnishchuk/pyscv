"""Download distribution artifacts from GitHub Release or PyPI.

Usage:
    uv run python scripts/download_artifacts.py 0.4.2a10
    uv run python scripts/download_artifacts.py 0.4.2a10 --source pypi
    uv run python scripts/download_artifacts.py 0.4.2a10 --dry-run
"""

from pyscv.download_artifacts import app

if __name__ == "__main__":
    app()
