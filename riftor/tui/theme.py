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
    # Signature: deep indigo background, violet / cyan rift glow.
    "rift": {
        "bg": "#08060f", "fg": "#cbd5e1", "surface": "#0f0d1a", "panel": "#141124",
        "violet": "#a855f7", "cyan": "#22d3ee", "magenta": "#f0abfc", "danger": "#fca5a5",
        "muted": "#8b8ba7", "dim": "#5a5a6a", "faint": "#3a3a4a", "border": "#2a2a3a",
        "user-bg": "#1a1426", "user-fg": "#e9d5ff", "assistant-bg": "#101019", "tool-bg": "#0c0c14",
    },
    # Cold, icy: blue-steel background, electric cyan accent.
    "void": {
        "bg": "#0a1628", "fg": "#c4d6f0", "surface": "#102540", "panel": "#142d4a",
        "violet": "#6d8bff", "cyan": "#38e0ff", "magenta": "#7dd3fc", "danger": "#fb7185",
        "muted": "#7a90aa", "dim": "#4a6080", "faint": "#2a4058", "border": "#1e3048",
        "user-bg": "#102840", "user-fg": "#d4e4ff", "assistant-bg": "#0c1e34", "tool-bg": "#081828",
    },
    # Warm magma: dark cherry base, magenta + hot amber accents.
    "fracture": {
        "bg": "#140a10", "fg": "#e2cfd6", "surface": "#200e19", "panel": "#261022",
        "violet": "#e040c0", "cyan": "#f59e0b", "magenta": "#f0abfc", "danger": "#ef4444",
        "muted": "#a88c94", "dim": "#6e505a", "faint": "#3a2830", "border": "#2e1c24",
        "user-bg": "#281422", "user-fg": "#fce0ff", "assistant-bg": "#1a0e16", "tool-bg": "#120810",
    },
    # Deep purple: extreme contrast, intense violet-on-black.
    "singularity": {
        "bg": "#060210", "fg": "#e7dffc", "surface": "#0e0624", "panel": "#140a30",
        "violet": "#b388ff", "cyan": "#d0bcff", "magenta": "#f5d0fe", "danger": "#fb7185",
        "muted": "#9080bc", "dim": "#5e4a80", "faint": "#382a52", "border": "#281c3c",
        "user-bg": "#1a0e36", "user-fg": "#efe4ff", "assistant-bg": "#0e0820", "tool-bg": "#080414",
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
