# riftor v4.1.0 — keyboard-first config & inline shell

## Highlights
- **Inline `/config` picker** — prompt-owned settings above the input (type to
  filter, `↑`/`↓` to move, `Enter` to change, `Esc` to back/close). Each valid
  choice applies and persists immediately — no Save/Cancel modal.
- **Provider → model flow** — pick a provider, then a curated or custom model;
  live discovery merges provider model lists with curated defaults and degrades
  cleanly when offline.
- **Inline `!` shell** — `$ command · exit N` (plus non-empty stdout/stderr)
  renders in the conversation. Output is not sent to the model.

## Removed
- **`ConfigScreen` modal** — the boxed `/config` form is gone
- **Permanent shell pane** (`#shell-pane` / `#shell-log`) and **`/clearlog`**

## Notes
- Credential edits stay provider-scoped; blank API-key replace preserves the
  stored key; clearing is a separate action. Codex shows a login status row
  instead of key/base editors.
- `show_tool_output` still controls agent tool results only — not direct `!`
  shell output.
- Advanced TOML-only fields (`worker_max_parallel`, `worker_timeout_s`, …)
  remain config-file only.
