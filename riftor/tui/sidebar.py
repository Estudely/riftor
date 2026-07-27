"""Collapsible engagement sidebar — scope, methodology checklist, findings."""

from __future__ import annotations

from collections import defaultdict

from rich.text import Text
from textual.widgets import Static

from riftor.tui.theme import palette


class EngagementSidebar(Static):
    """Left sidebar showing scope, checklist progress, and top findings."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._engagement = None

    def bind_engagement(self, engagement) -> None:
        self._engagement = engagement
        self.refresh_sidebar()

    def refresh_sidebar(self) -> None:
        eng = self._engagement
        if eng is None:
            self.update("No engagement loaded.")
            return
        p = palette(self.app)
        t = Text()
        # Scope
        t.append("Scope\n", style=f"bold {p['violet']}")
        in_scope = eng.scope.in_scope
        if in_scope:
            for target in in_scope[:8]:
                t.append(f"  • {target.raw}\n", style=p["muted"])
            if len(in_scope) > 8:
                t.append(f"  … +{len(in_scope) - 8} more\n", style=p["dim"])
        else:
            t.append("  (none)\n", style=p["dim"])
        t.append("\n")

        # Methodology
        done, total = eng.methodology_progress()
        t.append(f"Checklist {done}/{total}\n", style=f"bold {p['cyan']}")
        by_cat: dict[str, list] = defaultdict(list)
        for item in eng.list_methodology():
            by_cat[item.category].append(item)
        for cat, items in by_cat.items():
            cat_done = sum(1 for i in items if i.checked)
            t.append(f"  {cat} ({cat_done}/{len(items)})\n", style=p["muted"])
        t.append("\n")

        # Findings by severity
        t.append("Findings\n", style=f"bold {p['magenta']}")
        findings = eng.store.list_findings()
        if not findings:
            t.append("  (none)\n", style=p["dim"])
        else:
            sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
            top = sorted(
                findings,
                key=lambda f: (sev_order.get(f.get("severity", "info"), 9), f["id"]),
            )[:6]
            for f in top:
                sev = (f.get("severity") or "info").upper()[:4]
                title = (f.get("title") or "finding")[:36]
                t.append(f"  [{sev}] {title}\n", style=p["muted"])
            if len(findings) > 6:
                t.append(f"  … +{len(findings) - 6} more\n", style=p["dim"])
        self.update(t)
