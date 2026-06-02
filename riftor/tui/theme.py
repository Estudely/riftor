"""riftor themes.

Each theme is a Textual ``Theme`` that sets the standard design tokens (so
built-in widgets like Button/Select/Switch look right) plus a ``variables`` dict
holding the riftor-specific palette referenced by ``rift.tcss`` (as ``$name``)
and by the Rich-Text widgets (via :func:`palette`).
"""

from __future__ import annotations

from textual.theme import Theme

# Variable keys shared by every theme (referenced in tcss + widgets).
_KEYS = (
    "violet", "cyan", "magenta", "danger", "muted", "dim", "faint", "border",
    "user-bg", "user-fg", "assistant-bg", "tool-bg",
)


def _theme(name: str, palette: dict[str, str]) -> Theme:
    return Theme(
        name=name,
        primary=palette["violet"],
        secondary=palette["cyan"],
        accent=palette["cyan"],
        foreground=palette["fg"],
        background=palette["bg"],
        surface=palette["surface"],
        panel=palette["panel"],
        success=palette["cyan"],
        warning=palette["magenta"],
        error=palette["danger"],
        dark=True,
        variables={k: palette[k] for k in _KEYS},
    )


_PALETTES: dict[str, dict[str, str]] = {
    # The signature look: void background, violet -> cyan rift glow.
    "rift": {
        "bg": "#0a0a12", "fg": "#c8c8d4", "surface": "#0e0e18", "panel": "#14141f",
        "violet": "#a855f7", "cyan": "#22d3ee", "magenta": "#f0abfc", "danger": "#fca5a5",
        "muted": "#8b8ba7", "dim": "#5a5a6a", "faint": "#3a3a4a", "border": "#2a2a3a",
        "user-bg": "#1a1426", "user-fg": "#e9d5ff", "assistant-bg": "#101019", "tool-bg": "#0c0c14",
    },
    # Cold, minimal, cyan-forward.
    "void": {
        "bg": "#07070d", "fg": "#b9c2d0", "surface": "#0b0d16", "panel": "#10131f",
        "violet": "#6d8bff", "cyan": "#38e0ff", "magenta": "#7dd3fc", "danger": "#fb7185",
        "muted": "#7a8499", "dim": "#4a5266", "faint": "#2a3040", "border": "#222838",
        "user-bg": "#11192b", "user-fg": "#cfe3ff", "assistant-bg": "#0b0f1a", "tool-bg": "#090c14",
    },
    # Warm, molten cracks: magenta + amber on charcoal.
    "fracture": {
        "bg": "#0d0a0f", "fg": "#d8cdd2", "surface": "#15101a", "panel": "#1a1320",
        "violet": "#d946ef", "cyan": "#fb923c", "magenta": "#f0abfc", "danger": "#ef4444",
        "muted": "#9a8c93", "dim": "#5e4f57", "faint": "#3a2f37", "border": "#2e2430",
        "user-bg": "#1f1320", "user-fg": "#fbd5ff", "assistant-bg": "#150f17", "tool-bg": "#100b12",
    },
    # Deep purple, high-contrast, intense.
    "singularity": {
        "bg": "#05030a", "fg": "#e7e2f5", "surface": "#0c0817", "panel": "#120c22",
        "violet": "#b388ff", "cyan": "#8b5cf6", "magenta": "#f5d0fe", "danger": "#fb7185",
        "muted": "#9990b5", "dim": "#5b5276", "faint": "#322a4a", "border": "#2a2440",
        "user-bg": "#1a1230", "user-fg": "#efe7ff", "assistant-bg": "#0f0a1d", "tool-bg": "#0b0717",
    },
}

THEMES: dict[str, Theme] = {name: _theme(name, pal) for name, pal in _PALETTES.items()}

DEFAULT_THEME = "rift"


def css_variable_defaults() -> dict[str, str]:
    """Fallback CSS variables so ``$name`` always resolves, even before our
    theme is active (e.g. during the very first paint under a built-in theme)."""
    return dict(_PALETTES[DEFAULT_THEME])


def palette(app) -> dict[str, str]:
    """The active theme's palette (with the default theme as a safe fallback)."""
    pal = dict(_PALETTES[DEFAULT_THEME])
    pal["fg"] = _PALETTES[DEFAULT_THEME]["fg"]
    try:
        theme = app.get_theme(app.theme)
    except Exception:  # noqa: BLE001
        theme = None
    if theme is not None:
        pal.update(theme.variables or {})
        pal["fg"] = theme.foreground or pal["fg"]
        pal["violet"] = theme.variables.get("violet", theme.primary) if theme.variables else theme.primary
    return pal
