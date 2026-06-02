"""Tool foundation: the Tool ABC, results, and execution context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

MAX_RESULT_CHARS = 30_000


@dataclass
class ToolResult:
    content: str
    is_error: bool = False

    def truncated(self, limit: int = MAX_RESULT_CHARS) -> "ToolResult":
        if len(self.content) <= limit:
            return self
        dropped = len(self.content) - limit
        return ToolResult(
            content=self.content[:limit] + f"\n\n…[truncated {dropped} chars]",
            is_error=self.is_error,
        )


@dataclass
class ToolContext:
    """Shared execution context handed to every tool."""

    workdir: Path = field(default_factory=Path.cwd)
    engagement: object | None = None


def resolve_path(ctx: ToolContext, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ctx.workdir / path
    return path


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {}
    requires_permission: bool = False
    danger: bool = False
    scope_sensitive: bool = False  # touches targets -> enforce scope before running

    def preview(self, args: dict) -> str:
        """One-line human summary for permission prompts and the audit log."""
        return ", ".join(f"{k}={v!r}" for k, v in args.items())[:300]

    @abstractmethod
    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult: ...

    def schema(self) -> dict:
        """OpenAI / litellm function-tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
