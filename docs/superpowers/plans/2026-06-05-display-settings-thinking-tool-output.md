# Display Settings: show/hide thinking + tool output — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two `/config` display settings — show/hide model thinking (with a reasoning-effort control) and show/hide tool-call output — defaulting both on.

**Architecture:** Three new `Config` fields (`show_thinking`, `show_tool_output`, `reasoning_effort`) round-trip to TOML. The provider gains a `"thinking"` stream event by reading litellm's `delta.reasoning_content` and conditionally requests reasoning via `reasoning_effort`. The TUI renders thinking as a dim-italic block above the answer and suppresses the tool-result render (not the call line, not the context entry) when configured. Headless prints thinking to stderr. All changes preserve the offline-by-design test path.

**Tech Stack:** Python 3.11+, pydantic, litellm (lazy), Textual, pytest (`asyncio_mode=auto`).

---

## File Structure

- `riftor/config.py` — add 3 fields + 3 `_to_toml()` lines. (Config model.)
- `riftor/agent/provider.py` — `_kwargs` requests reasoning; `stream_turn` yields `("thinking", str)`. (Provider streaming contract.)
- `riftor/tui/app.py` — render thinking in `_assistant_turn`; gate result render in `_show_tool_result`; apply 3 fields in `_open_config`. (TUI agent loop + rendering.)
- `riftor/tui/config_screen.py` — DISPLAY section with 3 widgets + result dict keys. (Config modal.)
- `riftor/tui/themes/rift.tcss` — `.thinking` CSS class. (Styling.)
- `riftor/headless.py` — handle `"thinking"` event (print to stderr). (Headless loop.)
- `tests/test_config.py` — defaults + round-trip. `tests/test_provider.py` — reasoning event + `_kwargs`. `tests/test_config_screen.py` — DISPLAY widgets render + save. `dev/smoke.py` — tool-output suppression.

Note on tool output in headless: `_run_tool_headless` never prints the tool *result* (only the `⛏` call line); results go to context only. So `show_tool_output` has nothing to suppress in headless — it is a TUI-only render gate. Headless only needs the thinking event.

---

## Task 1: Config fields + TOML round-trip

**Files:**
- Modify: `riftor/config.py` (Config model ~line 57-58; `_to_toml()` ~line 206-222)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_display_settings_default_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    # defaults
    fresh = Config()
    assert fresh.show_thinking is True
    assert fresh.show_tool_output is True
    assert fresh.reasoning_effort == "medium"
    # round-trip non-default values
    Config(show_thinking=False, show_tool_output=False, reasoning_effort="high").save()
    loaded = Config.load()
    assert loaded.show_thinking is False
    assert loaded.show_tool_output is False
    assert loaded.reasoning_effort == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_display_settings_default_and_roundtrip -v`
Expected: FAIL — `Config()` has no attribute `show_thinking` (pydantic ignores unknown kwargs / AttributeError on access).

- [ ] **Step 3: Add the fields**

In `riftor/config.py`, in the `Config` class, immediately after the `lore: bool = True` line (~line 58), add:

```python
    # Display: reasoning + tool-output visibility (runtime, via /config).
    show_thinking: bool = True
    show_tool_output: bool = True
    reasoning_effort: str = "medium"  # none | low | medium | high
```

- [ ] **Step 4: Add the TOML serialization**

In `riftor/config.py`, in `_to_toml()`, inside the `lines += [ ... ]` block, after the `f"lore = {str(self.lore).lower()}",` line (~line 210), add:

```python
            f"show_thinking = {str(self.show_thinking).lower()}",
            f"show_tool_output = {str(self.show_tool_output).lower()}",
            f'reasoning_effort = "{self.reasoning_effort}"',
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_display_settings_default_and_roundtrip -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add riftor/config.py tests/test_config.py
git commit -m "feat(config): show_thinking, show_tool_output, reasoning_effort fields"
```

---

## Task 2: Provider — request reasoning in `_kwargs`

**Files:**
- Modify: `riftor/agent/provider.py` (`_kwargs`, ~line 135-158)
- Test: `tests/test_provider.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_provider.py`:

```python
def test_kwargs_includes_reasoning_effort_when_thinking_on(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="sk-demo",
                 show_thinking=True, reasoning_effort="high")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert kw["reasoning_effort"] == "high"


def test_kwargs_omits_reasoning_effort_when_thinking_off(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="sk-demo",
                 show_thinking=False, reasoning_effort="high")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in kw


def test_kwargs_omits_reasoning_effort_when_none(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="sk-demo",
                 show_thinking=True, reasoning_effort="none")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in kw
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provider.py -k reasoning_effort -v`
Expected: FAIL — `reasoning_effort` key absent (first test) / KeyError or assertion mismatch.

- [ ] **Step 3: Implement in `_kwargs`**

In `riftor/agent/provider.py`, inside `_kwargs`, after the `api_key`/`api_base` block and BEFORE the `demo = os.environ.get("RIFTOR_DEMO_RESPONSE")` line (~line 152), add:

```python
        # Reasoning: ask the model to think only when the operator wants it shown.
        # litellm normalizes ``reasoning_effort`` across providers and drops it for
        # models that don't support it (drop_params=True). "none" => don't request.
        if self.config.show_thinking and self.config.reasoning_effort != "none":
            kwargs["reasoning_effort"] = self.config.reasoning_effort
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_provider.py -k reasoning_effort -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add riftor/agent/provider.py tests/test_provider.py
git commit -m "feat(provider): request reasoning_effort when show_thinking is on"
```

---

## Task 3: Provider — emit `"thinking"` events from the stream

**Files:**
- Modify: `riftor/agent/provider.py` (`stream_turn`, ~line 206-209)
- Test: `tests/test_provider.py`

- [ ] **Step 1: Write the failing test**

This test drives `stream_turn` with a hand-built fake litellm stream so it stays offline (no `mock_response`, no network). Add to `tests/test_provider.py`:

```python
@pytest.mark.asyncio
async def test_stream_turn_yields_thinking_and_excludes_it_from_message(monkeypatch):
    # Fake litellm streaming chunks: reasoning_content deltas, then content.
    class _Fn:
        def __init__(self): self.name = None; self.arguments = None
    class _Delta:
        def __init__(self, content=None, reasoning_content=None):
            self.content = content
            self.reasoning_content = reasoning_content
            self.tool_calls = None
    class _Choice:
        def __init__(self, delta): self.delta = delta
    class _Chunk:
        def __init__(self, delta): self.choices = [_Choice(delta)]; self.usage = None

    async def _fake_stream():
        yield _Chunk(_Delta(reasoning_content="let me "))
        yield _Chunk(_Delta(reasoning_content="think"))
        yield _Chunk(_Delta(content="the answer"))

    async def _fake_acompletion(self, **kwargs):
        return _fake_stream()

    monkeypatch.setattr(prov.Provider, "_acompletion", _fake_acompletion)
    p = Provider_for_test()

    thinking, text, turn = [], [], None
    async for event, payload in p.stream_turn([{"role": "user", "content": "go"}]):
        if event == "thinking":
            thinking.append(str(payload))
        elif event == "text":
            text.append(str(payload))
        elif event == "done":
            turn = payload

    assert "".join(thinking) == "let me think"
    assert "".join(text) == "the answer"
    assert turn is not None
    # reasoning is display-only: never persisted into the assistant message
    assert turn.assistant_message["content"] == "the answer"
    assert "reasoning_content" not in turn.assistant_message
    assert "let me think" not in (turn.assistant_message.get("content") or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_provider.py::test_stream_turn_yields_thinking_and_excludes_it_from_message -v`
Expected: FAIL — no `"thinking"` events emitted, so `"".join(thinking)` is `""`.

- [ ] **Step 3: Emit the thinking event**

In `riftor/agent/provider.py`, in `stream_turn`, immediately after the existing `content` block (the `if content:` ... `yield ("text", content)` lines, ~line 206-209) and before the `for tc in getattr(delta, "tool_calls", None) or []:` loop, add:

```python
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                # Display-only: surface the model's thinking to the UI but never
                # accumulate it into the assistant message (keeps history clean,
                # avoids provider replay issues with thinking blocks).
                yield ("thinking", reasoning)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_provider.py::test_stream_turn_yields_thinking_and_excludes_it_from_message -v`
Expected: PASS

- [ ] **Step 5: Run the full provider suite (no regressions)**

Run: `uv run pytest tests/test_provider.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add riftor/agent/provider.py tests/test_provider.py
git commit -m "feat(provider): emit display-only thinking events from stream_turn"
```

---

## Task 4: TUI — gate tool-result rendering on `show_tool_output`

**Files:**
- Modify: `riftor/tui/app.py` (`_show_tool_result`, ~line 1380-1389)
- Test: `dev/smoke.py`

- [ ] **Step 1: Add the smoke assertion (the failing check)**

In `dev/smoke.py`, inside `main()`, AFTER the scope-enforcement block (after the `assert any(... "out of scope" ...)` block, ~line 94) and BEFORE the Phase 7b dispatch block (the `import os` at ~line 101), insert:

```python
        # show_tool_output gate: when off, the result block is NOT rendered, but
        # the ⛏ call line still is, and the result still reaches the model context.
        from textual.widgets import Static as _Static
        app.config.show_tool_output = False
        app.permissions.allow_for_session("bash")
        before_results = len([w for w in app.query(_Static)
                              if "tool-result" in (w.classes or set())])
        await app._show_tool_result("SECRET-OUTPUT-LINE", is_error=False)
        after_results = len([w for w in app.query(_Static)
                             if "tool-result" in (w.classes or set())])
        assert after_results == before_results, "tool-result must not render when hidden"
        # still revealable on demand via /show
        assert any("SECRET-OUTPUT-LINE" in v for v in app._tool_results.values()), \
            "hidden result must still be registered for /show"
        app.config.show_tool_output = True
```

- [ ] **Step 2: Run smoke to verify it fails**

Run: `uv run python dev/smoke.py`
Expected: FAIL — `AssertionError: tool-result must not render when hidden` (current `_show_tool_result` always mounts).

- [ ] **Step 3: Implement the gate**

In `riftor/tui/app.py`, replace the body of `_show_tool_result` (currently lines ~1380-1389) with:

```python
    async def _show_tool_result(self, content: str, is_error: bool = False) -> None:
        max_lines = self.config.result_preview_lines
        lines = content.splitlines() or [""]
        if not self.config.show_tool_output:
            # Hidden by /config: don't render, but still register the full result
            # so the operator can reveal it on demand with /show N. The call line
            # (⛏ toolname …) is mounted separately and stays visible.
            rid = len(self._tool_results) + 1
            self._tool_results[rid] = content
            return
        shown = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            rid = len(self._tool_results) + 1
            self._tool_results[rid] = content
            shown += f"\n…(+{len(lines) - max_lines} more lines · /show {rid})"
        classes = "tool-result error" if is_error else "tool-result"
        await self._mount(Static(Text(shown), classes=classes))
```

- [ ] **Step 4: Run smoke to verify it passes**

Run: `uv run python dev/smoke.py`
Expected: prints `SMOKE OK` (and the other `*_OK` lines).

- [ ] **Step 5: Commit**

```bash
git add riftor/tui/app.py dev/smoke.py
git commit -m "feat(tui): hide tool-result block when show_tool_output is off"
```

---

## Task 5: TUI — render thinking in `_assistant_turn`

**Files:**
- Modify: `riftor/tui/app.py` (`_assistant_turn`, ~line 1248-1277)
- Modify: `riftor/tui/themes/rift.tcss` (add `.thinking`, after `.note` ~line 42)

- [ ] **Step 1: Add the `.thinking` CSS class**

In `riftor/tui/themes/rift.tcss`, immediately after the `.note { ... }` block (ends ~line 42), add:

```css
.thinking {
    margin: 1 0 0 0;
    padding: 0 2;
    color: $dim;
    text-style: italic;
}
```

- [ ] **Step 2: Rewrite `_assistant_turn` to handle thinking**

In `riftor/tui/app.py`, replace the whole `_assistant_turn` method (currently ~line 1248-1277) with the version below. Changes: the assistant `Markdown` bubble is created lazily on the first `"text"` delta (so a thinking block can sit above it); a dim-italic `Static` thinking block is created lazily on the first `"thinking"` delta when `show_thinking` is on.

```python
    async def _assistant_turn(self) -> Turn:
        self.context.repair()

        p = self._pal()
        thinking_block: Static | None = None
        thinking_buf: list[str] = []
        bubble: Markdown | None = None
        buffer: list[str] = []
        last_render = 0.0
        last_think_render = 0.0
        turn: Turn | None = None

        async for event, payload in self.provider.stream_turn(
            self.context.messages, self.tool_schemas
        ):
            if event == "thinking":
                if not self.config.show_thinking:
                    continue
                thinking_buf.append(str(payload))
                if thinking_block is None:
                    thinking_block = Static(Text(""), classes="thinking")
                    await self._mount(thinking_block)
                now = time.monotonic()
                if now - last_think_render > 0.08:
                    thinking_block.update(
                        Text("💭 " + "".join(thinking_buf), style=f"italic {p['dim']}")
                    )
                    self._scroll_if_following()
                    last_think_render = now
            elif event == "text":
                buffer.append(str(payload))
                if bubble is None:
                    bubble = Markdown("", classes="assistant")
                    await self._mount(bubble)
                now = time.monotonic()
                if now - last_render > 0.08:
                    await bubble.update("".join(buffer))
                    self._scroll_if_following()
                    last_render = now
            elif event == "done":
                turn = payload  # type: ignore[assignment]

        # finalize the thinking block (flush any buffered tail)
        if thinking_block is not None:
            thinking_block.update(
                Text("💭 " + "".join(thinking_buf), style=f"italic {p['dim']}")
            )

        text = "".join(buffer).strip()
        if text:
            if bubble is None:
                bubble = Markdown("", classes="assistant")
                await self._mount(bubble)
            await bubble.update(text)
            self._last_output = text
        elif bubble is not None:
            await bubble.remove()
        self._scroll_if_following()
        return turn or Turn(text=text, assistant_message={"role": "assistant", "content": text})
```

- [ ] **Step 3: Verify imports exist**

Confirm `Static`, `Markdown`, `Text`, and `time` are already imported in `riftor/tui/app.py` (they are used elsewhere in the file). Run:

```bash
grep -n "from textual.widgets import\|from rich.text import Text\|^import time" riftor/tui/app.py | head
```
Expected: `Static` and `Markdown` appear in a `textual.widgets` import, `Text` is imported from `rich.text`, and `import time` is present. If any is missing, add it. (No code change expected — these are pre-existing.)

- [ ] **Step 4: Run smoke + the app test suite**

Run: `uv run python dev/smoke.py && uv run pytest tests/ -q`
Expected: `SMOKE OK` printed; all tests pass. (Smoke's `hello` message streams via the demo path which emits no thinking, so the thinking branch stays dormant and nothing breaks.)

- [ ] **Step 5: Commit**

```bash
git add riftor/tui/app.py riftor/tui/themes/rift.tcss
git commit -m "feat(tui): render model thinking as a dim-italic block above the answer"
```

---

## Task 6: Config modal — DISPLAY section

**Files:**
- Modify: `riftor/tui/config_screen.py` (`compose` ~line 128-132; `on_button_pressed` result dict ~line 241-255)
- Test: `tests/test_config_screen.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_config_screen.py`:

(a) Update the existing `test_config_modal_renders_all_fields`: add the three new ids to the field list and bump the counts. Change the field-list loop to include:

```python
                ("#cfg-theme", Select), ("#cfg-lore", Switch),
                ("#cfg-show-thinking", Switch), ("#cfg-show-tool-output", Switch),
                ("#cfg-reasoning-effort", Select),
```

and change the two count assertions to:

```python
            # five grouped section headers (MODEL / GENERATION / WORKERS / APPEARANCE / DISPLAY)
            assert len(list(screen.query(".config-section"))) == 5
            # +3 field rows for the DISPLAY section => 15 + 3 = 18
            assert len(list(screen.query(".field-label"))) == 18
```

(b) Add a new save test:

```python
@pytest.mark.asyncio
async def test_display_settings_save():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="ollama_chat/x", api_base="http://localhost:11434")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#cfg-show-thinking", Switch).value = False
            screen.query_one("#cfg-show-tool-output", Switch).value = False
            screen.query_one("#cfg-reasoning-effort", Select).value = "high"
            screen.query_one("#save").press()
            await pilot.pause()
            assert app.config.show_thinking is False
            assert app.config.show_tool_output is False
            assert app.config.reasoning_effort == "high"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_screen.py::test_display_settings_save tests/test_config_screen.py::test_config_modal_renders_all_fields -v`
Expected: FAIL — `#cfg-show-thinking` not found (NoMatches) / section count is 4 not 5.

- [ ] **Step 3: Add the DISPLAY section to `compose`**

In `riftor/tui/config_screen.py`, in `compose`, after the APPEARANCE block (the `yield _row("Lore", Switch(...))` line, ~line 132) and still inside the `with VerticalScroll(id="config-body"):` block, add:

```python
                yield Rule()
                yield Label("DISPLAY", classes="config-section")
                yield _row("Show thinking", Switch(
                    value=self.config.show_thinking, id="cfg-show-thinking"))
                yield _row("Show tool output", Switch(
                    value=self.config.show_tool_output, id="cfg-show-tool-output"))
                _effort = self.config.reasoning_effort \
                    if self.config.reasoning_effort in ("none", "low", "medium", "high") \
                    else "medium"
                yield _row("Reasoning effort", Select(
                    [(e, e) for e in ("none", "low", "medium", "high")],
                    value=_effort, allow_blank=False, id="cfg-reasoning-effort"))
```

- [ ] **Step 4: Add the keys to the result dict in `on_button_pressed`**

In `riftor/tui/config_screen.py`, in `on_button_pressed`, inside the `result: dict = { ... }` literal (~line 241-255), add these keys (e.g. after the `"lore": ...` entry):

```python
            "show_thinking": self.query_one("#cfg-show-thinking", Switch).value,
            "show_tool_output": self.query_one("#cfg-show-tool-output", Switch).value,
            "reasoning_effort": self.query_one("#cfg-reasoning-effort", Select).value,
```

- [ ] **Step 5: Apply the values in `_open_config`**

In `riftor/tui/app.py`, in `_open_config`, after the `self.config.lore = result["lore"]` line (~line 570), add:

```python
        self.config.show_thinking = result.get("show_thinking", self.config.show_thinking)
        self.config.show_tool_output = result.get("show_tool_output", self.config.show_tool_output)
        self.config.reasoning_effort = result.get("reasoning_effort", self.config.reasoning_effort)
```

(`self.provider = Provider(self.config)` later in this method already rebuilds the provider, so the new `reasoning_effort` takes effect next turn.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_config_screen.py -v`
Expected: PASS (all, including the updated render test and new save test).

- [ ] **Step 7: Commit**

```bash
git add riftor/tui/config_screen.py riftor/tui/app.py tests/test_config_screen.py
git commit -m "feat(config-screen): DISPLAY section for thinking + tool-output toggles"
```

---

## Task 7: Headless — surface thinking on stderr

**Files:**
- Modify: `riftor/headless.py` (stream consumer loop, ~line 109-115)

- [ ] **Step 1: Handle the thinking event**

In `riftor/headless.py`, in the `async for event, payload in provider.stream_turn(...)` loop, add a branch for `"thinking"`. Replace the loop body (~line 109-115) with:

```python
            async for event, payload in provider.stream_turn(context.messages, schemas):
                if event == "thinking":
                    if cfg.show_thinking:
                        # reasoning goes to stderr so stdout stays the clean answer
                        sys.stderr.write(str(payload))
                        sys.stderr.flush()
                elif event == "text":
                    sys.stdout.write(str(payload))
                    sys.stdout.flush()
                    text_parts.append(str(payload))
                elif event == "done":
                    turn = payload  # type: ignore[assignment]  # ("done", Turn)
```

- [ ] **Step 2: Verify it imports/runs (offline)**

Headless reasoning can't be exercised offline (the demo path emits no `reasoning_content`), so verify the module still imports and the loop is well-formed by running a headless invocation through the demo hook:

```bash
RIFTOR_DEMO_RESPONSE="recon plan ready" uv run riftor -p "plan a recon" 2>/dev/null
```
Expected: prints `recon plan ready` to stdout, exits 0, no traceback. (The thinking branch is dormant; this confirms no syntax/flow regression.)

- [ ] **Step 3: Commit**

```bash
git add riftor/headless.py
git commit -m "feat(headless): print model thinking to stderr when show_thinking is on"
```

---

## Task 8: Full CI gate

**Files:** none (verification only)

- [ ] **Step 1: Lint**

Run: `uv run ruff check riftor dev tests`
Expected: no errors. (If `ruff` flags an unused import or line length in edited files, fix inline and re-run.)

- [ ] **Step 2: Type check**

Run: `uv run pyright riftor`
Expected: 0 errors. (Watch the `thinking_block: Static | None` / `bubble: Markdown | None` annotations in Task 5 — they were added to satisfy the optional-then-assigned pattern.)

- [ ] **Step 3: Unit tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Smoke**

Run: `uv run python dev/smoke.py`
Expected: `SMOKE OK` and all `*_OK` lines.

- [ ] **Step 5: One-shot all gates**

Run: `make check`
Expected: lint → typecheck → test → smoke all green.

- [ ] **Step 6: Final commit (if anything was fixed in this task)**

```bash
git add -A
git commit -m "chore: pass lint/typecheck/test/smoke for display settings"
```

---

## Self-Review Notes

- **Spec coverage:** `show_thinking` (Tasks 1,2,3,5,6,7), `show_tool_output` (Tasks 1,4,6), `reasoning_effort` (Tasks 1,2,6) — all three settings covered with config + provider + TUI + headless + tests. DISPLAY modal section (Task 6). `.thinking` CSS (Task 5). `/show` still reveals hidden output (Task 4 register + smoke assertion). Offline-safe (demo path emits no reasoning; provider thinking test uses a hand-built fake stream).
- **Type consistency:** field names `show_thinking` / `show_tool_output` / `reasoning_effort` and widget ids `#cfg-show-thinking` / `#cfg-show-tool-output` / `#cfg-reasoning-effort` are identical across config.py, provider.py, app.py, config_screen.py, headless.py, and all tests. Event string `"thinking"` consistent between provider emit (Task 3) and both consumers (Tasks 5, 7).
- **Note for the implementer:** the `.field-label` / `.config-section` counts in Task 6 Step 1 assume the modal currently has 4 sections and 15 field rows (per the existing `test_config_modal_renders_all_fields`). If the existing test's numbers differ when you start, recompute: new sections = old + 1, new field rows = old + 3.
