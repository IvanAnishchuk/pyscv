"""Download proof artifacts from GitHub, PyPI, and TestPyPI.

Usage:
    uv run python scripts/download_proofs.py 0.4.2a10
    uv run python scripts/download_proofs.py 0.4.2a10 --source github
    uv run python scripts/download_proofs.py 0.4.2a10 --dry-run
"""

from pyscv.download_proofs import app

if __name__ == "__main__":
    app()
