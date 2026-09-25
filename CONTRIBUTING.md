# Contributing to riftor

Thanks for your interest in riftor. Contributions, bug reports, and ideas are
welcome.

## Ground rules
- **Authorized use only.** riftor is for security testing you are explicitly
  permitted to perform. Don't file issues or PRs that target third parties, add
  malware, or are designed primarily to cause harm.
- Be respectful and constructive.

## Dev setup
Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/Estudely/riftor && cd riftor
uv sync --extra dev
uv run riftor            # launch the TUI (needs an API key, e.g. ANTHROPIC_API_KEY)
```

## Before you open a PR
Run the same checks CI runs (or just `make check`):

```bash
uv run ruff check riftor dev tests   # lint
uv run pyright riftor                # type check
uv run pytest                        # unit suite (tests/)
uv run python dev/smoke.py           # headless TUI integration (prints all *_OK)
```

Everything runs **offline** — no model/API key needed. `tests/` holds focused
unit and integration tests; `dev/smoke.py` supplements them by driving the
real TUI headlessly end-to-end. Install the pre-commit hooks to run these
automatically: `make install-hooks`.

## Project layout
```
riftor/
  tui/           Textual app, commands, widgets, themes
  agent/         providers, context, sessions, workers
  tools/         core and engagement tools (registry in __init__)
  engagement/    scope, SQLite state, methodology, reporting
  safety/        permissions and audit log
tests/           focused unit and integration tests
dev/smoke.py     headless end-to-end TUI smoke check
```
For current behavior, see the [README](./README.md),
[`docs/configuration.md`](./docs/configuration.md), and the versioned release
notes under [`docs/`](./docs/).

## Conventions
- Python 3.11+, `ruff` (line length 100) for lint/format.
- Keep tools UI-agnostic: a `Tool.execute` returns a `ToolResult`; the app
  renders it. New tools go in `riftor/tools/` and are registered in
  `riftor/tools/__init__.py`.
- Add/extend a check in `dev/smoke.py` for new behavior where practical.
- Use descriptive commit prefixes such as `fix:`, `feat:`, `docs:`, and
  `chore:`.

## Releases (maintainers)
`pyproject.toml` is the single source of truth for the version — `riftor
--version` reads it from the installed package metadata, so there's nothing to
bump in `riftor/__init__.py`.

`main` is protected (changes land via PR), and the `release` workflow fires on a
`v*` tag and **verifies the tag matches the `pyproject.toml` version**. So land
the bump on `main` *first*, then tag the merged commit:

1. `uv version X.Y.Z` — bumps `pyproject.toml` and `uv.lock` together.
2. Open a PR with that bump, get CI green, and merge it to `main`.
3. Tag the merged commit on `main` and push **only the tag**:
   ```bash
   git checkout main && git pull
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
4. The `release` workflow then builds, publishes to PyPI via trusted publishing,
   and creates the GitHub Release. PyPI rejects duplicate versions, so a given
   `X.Y.Z` can only be published once.

> Do **not** push the tag before the bump is merged to `main`: the workflow
> would publish to PyPI from a commit that isn't on `main`, leaving `main`'s
> version out of sync with what's on PyPI.

## License
By contributing, you agree your contributions are licensed under the project's
[GPL-3.0](./LICENSE) license.
