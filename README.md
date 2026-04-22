# pyscv

Python Supply-Chain Verification toolkit

## Installation

```bash
uv tool install pyscv
# or
pip install pyscv
```

## Usage

```bash
pyscv --help
```

## Development

```bash
git clone https://github.com/IvanAnishchuk/pyscv.git
cd pyscv
uv sync

# Run tests
uv run pytest

# Run lints
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run ty check

# Run full pre-commit suite
uv run pre-commit run --all-files

# Run supply-chain audit
uv run python scripts/audit.py
```

## License

CC0-1.0
