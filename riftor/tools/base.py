"""Tool foundation: the Tool ABC, results, and execution context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from riftor.config import Config
    from riftor.engagement import Engagement
    from riftor.safety.audit import AuditLog
    from riftor.safety.permissions import Permissions

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
    engagement: "Engagement | None" = None
    #: per-result truncation cap fed back into the model (configurable via Config)
    max_result_chars: int = MAX_RESULT_CHARS
    #: Optional plumbing for tools that spawn subagents (DispatchChaklaTool).
    #: Kept optional so ordinary tools and tests build a bare ToolContext.
    config: "Config | None" = None
    permissions: "Permissions | None" = None
    audit: "AuditLog | None" = None
    yolo: bool = False


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

    def confirm_detail(self, args: dict, ctx: ToolContext) -> str | None:
        """Optional multi-line detail (e.g. a diff) shown in the approval modal.

        Returning ``None`` means "no extra detail" — the default for most tools.
        """
        return None

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
