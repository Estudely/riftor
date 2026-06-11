"""Core tools: bash, read, write, edit, grep, glob, webfetch.

Read-only tools (read/grep/glob/webfetch) run without a prompt. Mutating tools
(bash/write/edit) set ``requires_permission`` so the UI confirms before running.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import difflib
import re
import shutil
import urllib.request
from pathlib import Path

from riftor.tools.base import (
    PathOutsideWorkdir,
    Tool,
    ToolContext,
    ToolResult,
    resolve_path,
)


def _unified_diff(old: str, new: str, path: str, max_lines: int = 200) -> str:
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    )
    lines = list(diff)
    if not lines:
        return "(no changes)"
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… (+{len(lines) - max_lines} more diff lines)"]
    return "\n".join(lines)


class BashTool(Tool):
    name = "bash"
    description = (
        "Run a shell command and return combined stdout/stderr plus the exit code. "
        "Use for recon/scanning tools (nmap, httpx, ...), file operations, and git."
    )
    requires_permission = True
    danger = True
    scope_sensitive = True
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)."},
        },
        "required": ["command"],
    }

    def preview(self, args: dict) -> str:
        return str(args.get("command", ""))[:400]

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult("error: empty command", is_error=True)
        timeout = int(args.get("timeout") or 120)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ctx.workdir),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: failed to start: {exc}", is_error=True)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(f"error: timed out after {timeout}s", is_error=True)
        text = out.decode("utf-8", errors="replace")
        rc = proc.returncode
        return ToolResult(f"[exit {rc}]\n{text}", is_error=(rc != 0)).truncated(ctx.max_result_chars)


class ReadTool(Tool):
    name = "read"
    description = "Read a UTF-8 text file. Returns line-numbered content."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (absolute or relative)."},
            "offset": {"type": "integer", "description": "1-indexed start line."},
            "limit": {"type": "integer", "description": "Max lines to read (default 2000)."},
        },
        "required": ["path"],
    }

    def preview(self, args: dict) -> str:
        return str(args.get("path", ""))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path = resolve_path(ctx, args["path"])
        except PathOutsideWorkdir as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        if not path.exists():
            return ToolResult(f"error: no such file: {path}", is_error=True)
        if path.is_dir():
            return ToolResult(f"error: is a directory: {path}", is_error=True)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: {exc}", is_error=True)
        lines = text.splitlines()
        offset = max(int(args.get("offset") or 1), 1)
        limit = int(args.get("limit") or 2000)
        start = offset - 1
        chunk = lines[start : start + limit]
        numbered = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(chunk))
        return ToolResult(numbered or "(empty)").truncated()


class WriteTool(Tool):
    name = "write"
    description = "Write (create or overwrite) a UTF-8 text file."
    requires_permission = True
    danger = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write."},
            "content": {"type": "string", "description": "Full file content."},
        },
        "required": ["path", "content"],
    }

    def preview(self, args: dict) -> str:
        content = args.get("content", "")
        return f"{args.get('path', '')}  ({len(content)} bytes)"

    def confirm_detail(self, args: dict, ctx: ToolContext) -> str | None:
        try:
            path = resolve_path(ctx, str(args.get("path", "")))
        except PathOutsideWorkdir:
            return None  # execute() refuses it; no diff to preview
        new = str(args.get("content", ""))
        old = ""
        if path.exists() and path.is_file():
            try:
                old = path.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                old = ""
        if not old:
            preview = "\n".join(f"+{line}" for line in new.splitlines()[:120])
            extra = "" if new.count("\n") <= 120 else f"\n… (+{new.count(chr(10)) - 120} more lines)"
            return f"new file {path.name}:\n{preview}{extra}"
        return _unified_diff(old, new, path.name)

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path = resolve_path(ctx, args["path"])
        except PathOutsideWorkdir as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.get("content", ""), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: {exc}", is_error=True)
        return ToolResult(f"wrote {path}")


class EditTool(Tool):
    name = "edit"
    description = (
        "Replace an exact string in a file. Fails if old_string is missing or not unique "
        "(unless replace_all is true)."
    )
    requires_permission = True
    danger = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def preview(self, args: dict) -> str:
        return str(args.get("path", ""))

    def confirm_detail(self, args: dict, ctx: ToolContext) -> str | None:
        try:
            path = resolve_path(ctx, str(args.get("path", "")))
        except PathOutsideWorkdir:
            return None  # execute() refuses it; no diff to preview
        if not path.exists() or not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None
        old = str(args.get("old_string", ""))
        new = str(args.get("new_string", ""))
        if old not in text:
            return f"⚠ old_string not found in {path.name} — edit will fail"
        updated = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        return _unified_diff(text, updated, path.name)

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path = resolve_path(ctx, args["path"])
        except PathOutsideWorkdir as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        if not path.exists():
            return ToolResult(f"error: no such file: {path}", is_error=True)
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: {exc}", is_error=True)
        count = text.count(old)
        if count == 0:
            return ToolResult("error: old_string not found", is_error=True)
        if count > 1 and not args.get("replace_all"):
            return ToolResult(
                f"error: old_string is not unique ({count} matches); "
                "pass replace_all=true or add more context",
                is_error=True,
            )
        updated = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        try:
            path.write_text(updated, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: {exc}", is_error=True)
        return ToolResult(f"edited {path} ({count if args.get('replace_all') else 1} replacement(s))")


class GlobTool(Tool):
    name = "glob"
    description = "Find files by glob pattern (e.g. '**/*.py'), newest first."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern."},
            "path": {"type": "string", "description": "Base directory (default cwd)."},
        },
        "required": ["pattern"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            base = resolve_path(ctx, args.get("path", "."))
        except PathOutsideWorkdir as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        try:
            matches = [p for p in base.glob(args["pattern"]) if p.is_file()]
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: {exc}", is_error=True)
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            return ToolResult("(no matches)")
        return ToolResult("\n".join(str(p) for p in matches[:500])).truncated()


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a regex; returns file:line: match. Uses ripgrep if available."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex to search for."},
            "path": {"type": "string", "description": "File or directory (default cwd)."},
            "glob": {"type": "string", "description": "Only search files matching this glob."},
        },
        "required": ["pattern"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        try:
            base = resolve_path(ctx, args.get("path", "."))
        except PathOutsideWorkdir as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        glob = args.get("glob")
        if shutil.which("rg"):
            cmd = ["rg", "--line-number", "--no-heading", "--color", "never"]
            if glob:
                cmd += ["--glob", glob]
            cmd += ["--", pattern, str(base)]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(f"error: {exc}", is_error=True)
            text = out.decode("utf-8", errors="replace")
            return ToolResult(text or "(no matches)").truncated()
        return await asyncio.to_thread(self._py_grep, pattern, base, glob)

    def _py_grep(self, pattern: str, base: Path, glob: str | None) -> ToolResult:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return ToolResult(f"error: bad regex: {exc}", is_error=True)
        results: list[str] = []
        files = base.rglob(glob) if glob else (base.rglob("*") if base.is_dir() else [base])
        for f in files:
            if not f.is_file():
                continue
            try:
                for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        results.append(f"{f}:{n}: {line.strip()}")
                        if len(results) >= 1000:
                            return ToolResult("\n".join(results)).truncated()
            except Exception:  # noqa: BLE001
                continue
        return ToolResult("\n".join(results) or "(no matches)").truncated()


class WebFetchTool(Tool):
    name = "webfetch"
    description = "Fetch a URL over HTTP(S) and return readable text (HTML is stripped to text)."
    scope_sensitive = True
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Fully-qualified URL."}},
        "required": ["url"],
    }

    def preview(self, args: dict) -> str:
        return str(args.get("url", ""))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            return ToolResult("error: url must start with http:// or https://", is_error=True)

        def _get() -> tuple[str, bytes]:
            req = urllib.request.Request(url, headers={"User-Agent": "riftor/0.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
                return resp.headers.get("Content-Type", ""), resp.read(2_000_000)

        try:
            ctype, data = await asyncio.to_thread(_get)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: {exc}", is_error=True)
        text = data.decode("utf-8", errors="replace")
        if "html" in ctype.lower() or "<html" in text[:2000].lower():
            text = _html_to_text(text)
        return ToolResult(text).truncated()


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


@dataclass
class ShellResult:
    stdout: str
    stderr: str
    exit_code: int
    truncated: bool


async def run_shell(command: str, workdir: str | Path, timeout: int = 30) -> ShellResult:
    """Run a shell command and return captured stdout, stderr, and exit code.

    Output is truncated at 10 000 characters. Timeout raises no exception;
    the result reports truncated output and a non-zero exit code.
    """
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workdir),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ShellResult(
            stdout="",
            stderr=f"error: timed out after {timeout}s",
            exit_code=proc.returncode if proc.returncode is not None else -1,
            truncated=True,
        )
    stdout = out.decode("utf-8", errors="replace") if out else ""
    stderr = err.decode("utf-8", errors="replace") if err else ""
    truncated = False
    max_chars = 10_000
    if len(stdout) > max_chars:
        dropped = len(stdout) - max_chars
        stdout = stdout[:max_chars] + f"\n...[truncated {dropped} chars]"
        truncated = True
    if len(stderr) > max_chars:
        dropped = len(stderr) - max_chars
        stderr = stderr[:max_chars] + f"\n...[truncated {dropped} chars]"
        truncated = True
    return ShellResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode if proc.returncode is not None else 0,
        truncated=truncated,
    )
