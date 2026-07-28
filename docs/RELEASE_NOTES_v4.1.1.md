# riftor v4.1.1 — Codex HTTP 400 diagnostics

## Fixes
- **Codex / ChatGPT subscription errors** — HTTP 400 responses from
  `chatgpt.com/backend-api/codex/responses` now surface the backend `detail`
  (instead of a bare `HTTP Error 400: Bad Request`).
- **Mid-stream failure classification** — litellm `MidStreamFallbackError` /
  `APIConnectionError` wrappers that carry an HTTP 400 classify as
  `validation` (not retryable `network`), so the TUI shows
  `rift collapsed [validation] — …` rather than a raw traceback dump.
