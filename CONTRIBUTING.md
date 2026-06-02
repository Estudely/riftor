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
Run the same checks CI runs:

```bash
uv run ruff check riftor dev      # lint
uv run python dev/smoke.py        # headless TUI + unit suites (must print all *_OK)
```

`dev/smoke.py` runs offline (no model/API key needed) — it exercises the TUI,
tools, scope engine, engagement store, CVSS, reports, sessions, and themes.

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
1. Bump `version` in `pyproject.toml` (and `riftor/__init__.py`).
2. Commit, then `git tag vX.Y.Z && git push origin main --tags`.
3. The `release` workflow builds and publishes to PyPI via trusted publishing.

## License
By contributing, you agree your contributions are licensed under the project's
[GPL-3.0](./LICENSE) license.
