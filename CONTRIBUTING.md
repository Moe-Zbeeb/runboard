# Contributing

Thanks for helping improve runboard. Bug reports, fixes, and focused features are all welcome.

## Setup

```bash
git clone https://github.com/Moe-Zbeeb/runboard && cd runboard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Trying changes end to end

```bash
runboard serve --dir /tmp/rb-runs              # terminal 1, open the printed local URL
python examples/quickstart.py --lr 1e-3        # terminal 2
python examples/quickstart.py --lr 3e-4        # terminal 3
```

The dashboard is served straight from `src/runboard/static/`, so with an editable install a browser
refresh picks up changes to `app.js` and `style.css`.

## Guidelines

- **No runtime dependencies.** The package must stay standard-library only. Test-only dependencies go
  in the `dev` extra.
- **Python 3.8+.** Avoid newer syntax (`match`, `X | Y` type unions, etc.). CI runs 3.8 through 3.13.
- **Logging must never raise into user code.** Any change to `client.py` must keep that guarantee, and
  should come with a test that simulates the failure.
- **Keep modules focused.** See [docs/architecture.md](docs/architecture.md) for what each one owns.
- Add or update tests for behavior changes, and update the README when user-facing behavior changes.

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and `__version__` in `src/runboard/__init__.py`, and update
   `CHANGELOG.md`.
2. Commit, then tag `vX.Y.Z` and push the tag.
3. Create a GitHub release from the tag. The `Publish to PyPI` workflow builds the package and uploads
   it through PyPI trusted publishing.
