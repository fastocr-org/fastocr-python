# Contributing

Thanks for helping improve `fastocr-sdk`. Bug reports, fixes and documentation
improvements are welcome.

## How this repository works

This repository is exported from FastOCR's private monorepo. Pull requests are
welcome and are reviewed here, but accepted changes are re-applied upstream and
come back through the next export, so the commit that lands on `main` may differ
from your branch. Open an issue first for anything larger than a small fix.

## Set up

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Python 3.9 or newer is supported.

## Check your change

```bash
pytest
ruff check src tests
mypy src
```

The tests run against mocked HTTP and a local fake server, so no API key is needed.
`tests/test_live_smoke.py` runs only when `FASTOCR_LIVE_API_KEY` is set.

## Pull requests

- Keep the change small and focused. Add or update a test for any behavior change.
- Add a line to `CHANGELOG.md` (under the next version) for any user-visible change.
- Do not commit API keys or real documents.
- The sync and async clients must behave identically; change both.
