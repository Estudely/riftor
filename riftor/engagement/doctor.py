"""Toolchain check: which recon/exploitation binaries are on PATH.

riftor runs external tools (nmap, httpx, nuclei, …) through the ``bash`` tool —
it never bundles them. A missing tool isn't fatal (the agent sees the failed
command and adapts), but discovering that mid-task is annoying. ``/doctor`` (and
``riftor --doctor``) surface the gaps up front, grouped by RIFT stage.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from riftor.codex_auth import auth_status

# Tools the methodology + system prompt reference, grouped by RIFT stage.
# (binary, one-line purpose). Kept in sync with prompts/system.md.
TOOLCHAIN: dict[str, list[tuple[str, str]]] = {
    "R": [
        ("nmap", "port/service scanning"),
        ("httpx", "HTTP probing"),
        ("subfinder", "subdomain enumeration"),
        ("dig", "DNS lookups"),
        ("whatweb", "tech fingerprinting"),
        ("curl", "ad-hoc HTTP requests"),
    ],
    "I": [
        ("nuclei", "template-based vuln scanning"),
        ("ffuf", "content/parameter fuzzing"),
        ("gobuster", "directory brute-forcing"),
        ("nikto", "web server scanning"),
        ("sqlmap", "SQL injection"),
    ],
    # Optional helpers riftor itself can use, not stage-specific.
    "_helpers": [
        ("rg", "faster grep (riftor falls back to python)"),
    ],
}

_STAGE_NAMES = {"R": "Recon", "I": "Intrusion"}


@dataclass
class ToolStatus:
    name: str
    purpose: str
    path: str | None  # resolved path, or None if not found
    stage: str

    @property
    def present(self) -> bool:
        return self.path is not None


def check_toolchain() -> list[ToolStatus]:
    """Probe every known tool on PATH. Pure except for shutil.which()."""
    out: list[ToolStatus] = []
    for stage, tools in TOOLCHAIN.items():
        for name, purpose in tools:
            out.append(ToolStatus(name, purpose, shutil.which(name), stage))
    return out


def summarize(statuses: list[ToolStatus]) -> dict:
    present = [s for s in statuses if s.present]
    missing = [s for s in statuses if not s.present]
    return {
        "present": len(present),
        "missing": len(missing),
        "total": len(statuses),
        "missing_names": [s.name for s in missing],
    }


def browser_status() -> tuple[bool, bool, str]:
    """Return (package_ok, binary_ok, detail) for the Playwright browser."""
    try:
        import importlib.util

        pkg_ok = importlib.util.find_spec("playwright") is not None
    except Exception:  # noqa: BLE001
        pkg_ok = False
    binary_ok = False
    if pkg_ok:
        from pathlib import Path

        # Playwright caches browsers under ~/.cache/ms-playwright (Linux/macOS).
        cache = Path.home() / ".cache" / "ms-playwright"
        binary_ok = cache.exists() and any(cache.glob("chromium-*"))
    if not pkg_ok:
        detail = "playwright package not installed"
    elif not binary_ok:
        detail = "Chromium not installed — run: playwright install chromium"
    else:
        detail = "ready (chromium installed)"
    return pkg_ok, binary_ok, detail


def render_markdown(statuses: list[ToolStatus]) -> str:
    """A grouped, human-readable report for the TUI / CLI."""
    by_stage: dict[str, list[ToolStatus]] = {}
    for s in statuses:
        by_stage.setdefault(s.stage, []).append(s)

    lines = ["**riftor doctor — external toolchain**", ""]
    for stage, items in by_stage.items():
        if stage == "_helpers":
            header = "Helpers"
        else:
            header = f"{stage} · {_STAGE_NAMES.get(stage, stage)}"
        lines.append(f"_{header}_")
        for s in items:
            mark = "✓" if s.present else "✗"
            where = f" `{s.path}`" if s.present else " — *not on PATH*"
            lines.append(f"- {mark} `{s.name}` — {s.purpose}{where}")
        lines.append("")

    pkg_ok, binary_ok, detail = browser_status()
    bmark = "✓" if (pkg_ok and binary_ok) else "✗"
    lines.append("_Browser_")
    lines.append(f"- {bmark} `browser` (Playwright) — {detail}")
    lines.append("")

    summary = summarize(statuses)
    lines.append(
        f"**{summary['present']}/{summary['total']} present.** "
        + (
            "Missing tools aren't fatal — the agent works around them, but install "
            f"them for full coverage: {', '.join(summary['missing_names'])}."
            if summary["missing"]
            else "Full toolchain available."
        )
    )
    return "\n".join(lines)


def render_plain(statuses: list[ToolStatus]) -> str:
    """A no-markup version for the headless CLI / stderr."""
    lines = ["riftor doctor — external toolchain"]
    for s in statuses:
        mark = "ok " if s.present else "MISSING"
        lines.append(f"  [{mark}] {s.name:<10} {s.purpose}")
    summary = summarize(statuses)
    lines.append(f"{summary['present']}/{summary['total']} present.")
    if summary["missing"]:
        lines.append("missing (not fatal): " + ", ".join(summary["missing_names"]))
    st = auth_status()
    mark = "ok " if st.logged_in else "MISSING"
    lines.append("Codex subscription:")
    lines.append(f"  [{mark}] {'codex login':<10} {st.detail}")
    pkg_ok, binary_ok, detail = browser_status()
    mark = "ok " if (pkg_ok and binary_ok) else "MISSING"
    lines.append("Browser (Playwright):")
    lines.append(f"  [{mark}] {'browser':<10} {detail}")
    return "\n".join(lines)
