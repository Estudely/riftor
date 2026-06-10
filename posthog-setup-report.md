<wizard-report>
# PostHog post-wizard report

The wizard has completed a deep integration of riftor's existing PostHog telemetry. The `Telemetry` class was refactored from the deprecated module-level API (`posthog.api_key = ...`) to the instance-based `Posthog()` constructor with `enable_exception_autocapture=True` and proper `atexit` shutdown. Keys are now resolved from environment variables (`POSTHOG_PROJECT_TOKEN`, `POSTHOG_HOST`) rather than the deleted `_telemetry_keys.py` baked-in file. Five new business-level event tracking methods were added to `Telemetry` and wired into the engagement tools via an optional `telemetry` field added to `ToolContext`. Tests were updated throughout for the new API.

| Event | Description | File |
|---|---|---|
| `session_start` | Fires when a TUI or headless session begins (version, model, theme, yolo) | `riftor/telemetry.py` |
| `session_end` | Fires when a session ends (duration, steps, tool calls) | `riftor/telemetry.py` |
| `tool_call` | Fires on every tool invocation (tool name, allowed, is_error) | `riftor/telemetry.py` |
| `model_call` | Fires after each LLM completion (model, tokens in/out) | `riftor/telemetry.py` |
| `finding_recorded` | Fires when the agent records a security finding (severity, has_cvss, has_confidence) | `riftor/tools/engagement.py` |
| `report_generated` | Fires when a pentest report is written to disk (format) | `riftor/tools/engagement.py` |
| `stage_advanced` | Fires when the RIFT methodology stage changes R→I→F→T (stage) | `riftor/tools/engagement.py` |
| `scan_imported` | Fires when recon scan output is parsed and imported (tool, services_added, findings_added) | `riftor/tools/engagement.py` |
| `scope_target_added` | Fires when new targets are added to the engagement scope (count) | `riftor/tools/engagement.py` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

- [Analytics basics (wizard) dashboard](https://us.posthog.com/project/464067/dashboard/1694068)
- [Daily riftor sessions](https://us.posthog.com/project/464067/insights/7kLH5BSS)
- [Tool calls per day](https://us.posthog.com/project/464067/insights/eGeJapkh)
- [Findings by severity](https://us.posthog.com/project/464067/insights/5NpIVYnu)
- [Reports generated over time](https://us.posthog.com/project/464067/insights/TCk3KpPH)
- [RIFT stage progression](https://us.posthog.com/project/464067/insights/v52zCNJi)

### Agent skill

We've left an agent skill folder in your project. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.

</wizard-report>
