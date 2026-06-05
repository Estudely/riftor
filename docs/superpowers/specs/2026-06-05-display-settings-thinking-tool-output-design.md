# Display settings: show/hide thinking + show/hide tool output

**Date:** 2026-06-05
**Status:** Approved (design)

## Goal

Add two operator-controllable display settings to the `/config` modal:

1. **Show thinking** — show or hide the model's reasoning (chain-of-thought) in the chat.
2. **Show tool output** — show or hide the *result block* of each tool call (the verbose output below the `⛏ toolname …` line).

Both default to **on**. A third supporting setting, **reasoning effort**, controls the thinking budget requested from the model.

## Background

riftor does not capture model reasoning today. `Provider.stream_turn`
(`riftor/agent/provider.py`) only reads `delta.content` and ignores
`delta.reasoning_content`. So "show/hide thinking" requires building the
reasoning capture pipeline first, then gating its display.

Tool output, by contrast, is always rendered today: `_show_tool_result`
(`riftor/tui/app.py`) mounts a `Static` for every result. "Show/hide tool
output" gates that one render path. The tool *call* line (`⛏ bash nmap …`)
stays visible regardless, so the operator always sees **what** the agent ran.

litellm normalizes reasoning across providers (verified against litellm docs):
- `reasoning_effort` (`none`/`low`/`medium`/`high`) enables thinking and is
  translated per-provider (Anthropic adaptive thinking, OpenAI reasoning,
  DeepSeek thinking, etc.). For Anthropic Claude 4.6/4.7, any value other than
  `none` enables adaptive thinking.
- Streaming exposes reasoning deltas on `delta.reasoning_content` (a string).
  This is the common path we will consume.

## Settings

| Setting | Config field | Type | Default | UI control |
|---|---|---|---|---|
| Show thinking | `show_thinking` | bool | `True` | Switch |
| Show tool output | `show_tool_output` | bool | `True` | Switch |
| Reasoning effort | `reasoning_effort` | str | `"medium"` | Select (`none`/`low`/`medium`/`high`) |

Behavior of `reasoning_effort` vs `show_thinking`:
- `show_thinking=False` → **do not request reasoning at all** (omit
  `reasoning_effort` from the litellm call, or send `"none"`). Saves tokens and
  guarantees no thinking arrives.
- `show_thinking=True` → request reasoning at the configured `reasoning_effort`
  (defaulting to `medium`). If `reasoning_effort` is `none`, treat it as "don't
  request" even though thinking is nominally on (defensive; the UI will keep
  effort ≥ low when thinking is on, but the provider must not crash on `none`).

## Components

### 1. Config layer — `riftor/config.py`

Add three fields to the `Config` pydantic model:

```python
show_thinking: bool = True
show_tool_output: bool = True
reasoning_effort: str = "medium"  # none | low | medium | high
```

Add three lines to `_to_toml()` so they round-trip:

```python
f"show_thinking = {str(self.show_thinking).lower()}",
f"show_tool_output = {str(self.show_tool_output).lower()}",
f'reasoning_effort = "{self.reasoning_effort}"',
```

Bad-config-degrades-to-defaults already covers parse safety — a malformed value
falls through to `detect_defaults()` and never crashes startup.

### 2. Reasoning pipeline — `riftor/agent/provider.py`

**`_kwargs`:** after building the base kwargs, conditionally request reasoning:

```python
if self.config.show_thinking and self.config.reasoning_effort != "none":
    kwargs["reasoning_effort"] = self.config.reasoning_effort
```

Do **not** send `reasoning_effort` in the offline-demo branch (it stays inert
since `mock_response` short-circuits the real call).

**`stream_turn`:** after the existing `delta.content` handling, capture
reasoning deltas and yield a new event type:

```python
reasoning = getattr(delta, "reasoning_content", None)
if reasoning:
    yield ("thinking", reasoning)
```

Reasoning is **display-only**: it is NOT accumulated into `text_parts` and NOT
written into `assistant_message`. This keeps conversation history clean and
avoids any provider replay issues with thinking blocks. The `("done", Turn)`
event is unchanged.

This makes the `stream_turn` contract three event kinds: `"text"`,
`"thinking"`, `"done"`. Both consumers (TUI + headless) are updated explicitly.

### 3. TUI rendering — `riftor/tui/app.py`

**Thinking (`_assistant_turn`):** handle the `"thinking"` event. On the first
thinking delta, lazily mount a dim-italic `Static` (class `thinking`) *above*
the assistant bubble, prefixed `💭 thinking`. Subsequent deltas append and
re-render (same ~0.08s throttle as text). Because the provider only emits
reasoning when `show_thinking` is on, no extra guard is strictly required — but
we still skip mounting if `config.show_thinking` is false (defensive, and keeps
the contract obvious).

Mounting order: the thinking block is mounted before the assistant bubble so
reasoning visually precedes the answer. The assistant `Markdown` bubble is
created lazily on the first `"text"` delta (currently it is created eagerly at
the top of `_assistant_turn`; this changes to lazy creation so thinking can sit
above it). If a turn produces only thinking and no text, the empty bubble is
removed as it is today.

**Tool output (`_show_tool_result`):** early-return before mounting when
`config.show_tool_output` is false:

```python
if not self.config.show_tool_output:
    # still register for /show N so the operator can reveal it on demand
    rid = len(self._tool_results) + 1
    self._tool_results[rid] = content
    return
```

The tool result still goes into `context` via `add_tool_result` in `_run_tool`
(the model needs it) — only the chat render is suppressed. Registering it in
`_tool_results` means `/show N` still reveals hidden output on demand. The
`_show_tool_call` line is untouched, so the `⛏ toolname preview` line always
shows.

**CSS (`riftor/tui/themes/rift.tcss`):** add a `.thinking` class, styled like
`.note` but italic and using `$dim`:

```css
.thinking {
    margin: 1 0 0 0;
    padding: 0 2;
    color: $dim;
    text-style: italic;
}
```

### 4. Config modal — `riftor/tui/config_screen.py`

Add a new **DISPLAY** section after **APPEARANCE** with three rows:

```python
yield Rule()
yield Label("DISPLAY", classes="config-section")
yield _row("Show thinking", Switch(value=self.config.show_thinking, id="cfg-show-thinking"))
yield _row("Show tool output", Switch(value=self.config.show_tool_output, id="cfg-show-tool-output"))
yield _row("Reasoning effort", Select(
    [(e, e) for e in ("none", "low", "medium", "high")],
    value=self.config.reasoning_effort if self.config.reasoning_effort in
        ("none", "low", "medium", "high") else "medium",
    allow_blank=False, id="cfg-reasoning-effort"))
```

In `on_button_pressed`, add to the `result` dict:

```python
"show_thinking": self.query_one("#cfg-show-thinking", Switch).value,
"show_tool_output": self.query_one("#cfg-show-tool-output", Switch).value,
"reasoning_effort": self.query_one("#cfg-reasoning-effort", Select).value,
```

### 5. Apply on save — `riftor/tui/app.py` `_open_config`

After the existing assignments, before `self.provider = Provider(self.config)`:

```python
self.config.show_thinking = result.get("show_thinking", self.config.show_thinking)
self.config.show_tool_output = result.get("show_tool_output", self.config.show_tool_output)
self.config.reasoning_effort = result.get("reasoning_effort", self.config.reasoning_effort)
```

`self.provider = Provider(self.config)` is already re-created here, so the new
`reasoning_effort`/`show_thinking` take effect on the next turn.

### 6. Headless — `riftor/headless.py`

Update the `stream_turn` consumer loop to handle the `"thinking"` event:
- When `cfg.show_thinking` is on, print reasoning deltas to **stderr** (matching
  the existing tool-line convention; stdout stays the clean answer stream).
- Suppress the tool-result handling/print path when `cfg.show_tool_output` is
  off (the result still goes into context via `add_tool_result`).

## Error handling & edge cases

- **Non-reasoning models:** never emit `reasoning_content` → thinking block
  never mounts; `reasoning_effort` is ignored by the provider. No error.
- **Provider rejects `reasoning_effort`:** litellm generally normalizes/drops
  it; if a provider still 400s, the existing `classify_error`/retry path
  surfaces it as a `ProviderError`. We pass the param only when thinking is on.
- **`reasoning_effort="none"` while `show_thinking=True`:** provider request
  omits the param (treated as "don't request"); no crash.
- **Offline/demo mode (`RIFTOR_DEMO_RESPONSE`):** `mock_response`
  short-circuits the real call, so no reasoning is produced — tests and
  `dev/smoke.py` stay offline and green.
- **Turn with only thinking, no text:** empty assistant bubble removed as today;
  thinking block remains visible.

## Testing

- **`tests/test_config.py`:** new fields default correctly
  (`show_thinking=True`, `show_tool_output=True`, `reasoning_effort="medium"`)
  and round-trip through `_to_toml()` → `Config.load()`.
- **Provider test (`tests/test_provider.py` or equivalent):** a mocked litellm
  stream whose deltas carry `reasoning_content` yields `("thinking", …)` events;
  the final `Turn.assistant_message` contains no reasoning. With
  `show_thinking=False`, `_kwargs` omits `reasoning_effort`; with it on,
  `_kwargs` includes the configured effort.
- **`dev/smoke.py`:** extend to assert the tool-result render is suppressed when
  `show_tool_output=False` (no `.tool-result` widget mounted) while the
  `.tool` call line is still present, and that the result still reaches context.

## Out of scope

- Live-collapse "thought for N tokens" summaries (chose simple dim-italic block).
- Persisting reasoning into session history / `assistant_message`.
- Per-message toggles or slash commands to flip these mid-stream (operator uses
  `/config`). `/show N` continues to reveal hidden tool output on demand.
- Completions files (`completions/`) and man page — no new CLI flags are added;
  these are runtime `/config` settings only.
