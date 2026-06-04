# Contributing to riftor

Thanks for your interest in riftor. This is an early-stage, open-source
offensive-security agent — contributions, bug reports, and ideas are welcome.

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
unit tests (engagement, reports, permissions, config, agent, parsers, tools,
sessions); `dev/smoke.py` drives the real TUI headlessly end-to-end. Install the
pre-commit hooks to run these automatically: `make install-hooks`.

## Project layout
```
riftor/
  tui/           Textual app, widgets, themes, modals
  agent/         provider (litellm), context, session, prompts
  tools/         core tools + engagement tools (registry in __init__)
  engagement/    scope, sqlite state, CVSS, report rendering
  safety/        permission modal + audit log
dev/smoke.py     the test suite
```
A high-level roadmap lives in [`todo.md`](./todo.md).

## Conventions
- Python 3.11+, `ruff` (line length 100) for lint/format.
- Keep tools UI-agnostic: a `Tool.execute` returns a `ToolResult`; the app
  renders it. New tools go in `riftor/tools/` and are registered in
  `riftor/tools/__init__.py`.
- Add/extend a check in `dev/smoke.py` for new behavior where practical.
- Conventional-ish commit messages (`fix:`, `docs:`, `Phase N: …`).

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
