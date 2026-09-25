# Repository guidance

This file is the shared working guide for coding agents and contributors. Keep
it aligned with the code and current docs; `pyproject.toml` is the version
source of truth.

## Product and scope

Riftor is a Python terminal agent for authorized security testing. The current
methodology is an OWASP/PTES checklist, not the retired RIFT stages. The
website is maintained in the separate `Estudely/riftor-website` repository.
The mesh collaboration project is parked; leave mesh code, its docs, and its
branch untouched unless the user explicitly asks to resume it.

## Development

- Python 3.11+; use `uv` and the committed `uv.lock`.
- Install development dependencies with `uv sync --extra dev`.
- Useful checks: `uv run ruff check riftor dev tests`, `uv run pyright riftor`,
  `uv run pytest`, `uv run python dev/smoke.py`, and `uv build`.
- CI runs lint, type checking, unit tests, and the headless UI smoke check on
  Python 3.11 and 3.12, plus a Docker build/run check.
- Update the lockfile through uv when changing dependencies. Prefer a targeted
  `uv lock --upgrade-package <name>` for security or maintenance updates.
- Report which checks were run and their outcomes; do not claim unrun checks
  passed.

## Architecture map

- `riftor/__main__.py`: CLI entry point and mode dispatch.
- `riftor/tui/`: Textual UI, slash commands, session interaction, themes, and
  widgets.
- `riftor/headless.py`: non-interactive runner. Keep its safety behavior aligned
  with the TUI where applicable.
- `riftor/agent/`: provider abstraction, prompts/context, session persistence,
  anti-loop/circuit controls, and worker execution.
- `riftor/tools/`: agent-callable tools. Register built-ins in
  `riftor/tools/__init__.py`; keep tool execution independent of UI rendering.
- `riftor/engagement/`: scope, SQLite state, methodology, findings, reporting,
  lessons, memory, and engagement helpers.
- `riftor/safety/`: permission decisions and the local audit log.
- `riftor/skills/` and `riftor/workers/`: bundled Markdown content loaded by the
  product at runtime. Treat these as product assets, not disposable docs.
- `tests/`: focused unit and integration coverage; `dev/smoke.py` exercises the
  real Textual app without a model API call.

## Safety and security invariants

- Preserve scope enforcement, permission checks, deny rules, and audit logging
  when changing tools or agent loops. Headless runs have no operator prompt, so
  dangerous actions require explicit configured permission.
- Full-access/YOLO mode bypasses safeguards. Never widen its behavior or enable
  it implicitly.
- Treat shell, plugin, and configured MCP execution as code running with the
  user's OS permissions. Keep operator authorization and trust boundaries clear.
- Do not add telemetry or phone-home behavior.
- Keep credentials out of logs and examples. Redact common secret-bearing
  previews and keep local audit files owner-only (`0600`).
- Check dependency advisories when updating dependencies; keep `uv.lock`
  consistent with `pyproject.toml` and use patched releases.
- GitHub Actions that execute downloaded tools must pin versions and verify
  checksums where available. Grant write permissions only to jobs that need to
  publish repository changes.

## Documentation and generated files

- Keep README claims, CLI completions, `docs/riftor.1`,
  `docs/configuration.md`, and release notes consistent with shipped behavior.
- `demo.tape` generates `demo.gif` through `.github/workflows/demo.yml`; update
  the tape when UI commands or product flows change.
- Versioned release notes are historical records. Avoid adding replacement
  roadmaps or stale implementation plans; use current issues for future work.
- `CLAUDE.md` points Claude Code to this file. Put shared instructions here
  rather than maintaining duplicate agent guides.

## Releases

`pyproject.toml` owns the version. Update it and `uv.lock` together, merge the
version change to `main`, then tag that merged commit as `vX.Y.Z`. The release
workflow verifies the tag/version match and publishes through PyPI Trusted
Publishing.
