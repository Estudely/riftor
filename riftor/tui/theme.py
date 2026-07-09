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


def _theme(name: str, palette: dict[str, object]) -> Theme:
    return Theme(
        name=name,
        primary=str(palette["violet"]),
        secondary=str(palette["cyan"]),
        accent=str(palette["cyan"]),
        foreground=str(palette["fg"]),
        background=str(palette["bg"]),
        surface=str(palette["surface"]),
        panel=str(palette["panel"]),
        success=str(palette["cyan"]),
        warning=str(palette["magenta"]),
        error=str(palette["danger"]),
        # palette-driven so light themes get correct built-in-widget contrast
        dark=bool(palette.get("dark", True)),
        variables={k: str(palette[k]) for k in _KEYS},
    )


# Each palette: bg/fg/surface/panel + the shared accent/_KEYS, plus a "dark" flag
# that drives Textual's built-in-widget contrast. Ordered dark → light.
_PALETTES: dict[str, dict[str, object]] = {
    # Signature: deep indigo background, violet / cyan rift glow. (default)
    "rift": {
        "dark": True,
        "bg": "#08060f", "fg": "#cbd5e1", "surface": "#0f0d1a", "panel": "#141124",
        "violet": "#a855f7", "cyan": "#22d3ee", "magenta": "#f0abfc", "danger": "#fca5a5",
        "muted": "#8b8ba7", "dim": "#5a5a6a", "faint": "#3a3a4a", "border": "#2a2a3a",
        "user-bg": "#1a1426", "user-fg": "#e9d5ff", "assistant-bg": "#101019", "tool-bg": "#0c0c14",
    },
    # Dark but NOT black: a slate-gray dusk with brighter accents.
    "dusk": {
        "dark": True,
        "bg": "#1e2130", "fg": "#d7dcec", "surface": "#262a3d", "panel": "#2c3144",
        "violet": "#b794ff", "cyan": "#5ad1e6", "magenta": "#f0a6e0", "danger": "#ff8a8a",
        "muted": "#9aa0b8", "dim": "#6b7090", "faint": "#454a64", "border": "#3a3f57",
        "user-bg": "#2a2f48", "user-fg": "#e6dcff", "assistant-bg": "#242838", "tool-bg": "#1b1e2b",
    },
    # Cold, icy: blue-steel background, electric cyan accent.
    "void": {
        "dark": True,
        "bg": "#0a1628", "fg": "#c4d6f0", "surface": "#102540", "panel": "#142d4a",
        "violet": "#6d8bff", "cyan": "#38e0ff", "magenta": "#7dd3fc", "danger": "#fb7185",
        "muted": "#7a90aa", "dim": "#4a6080", "faint": "#2a4058", "border": "#1e3048",
        "user-bg": "#102840", "user-fg": "#d4e4ff", "assistant-bg": "#0c1e34", "tool-bg": "#081828",
    },
    # Warm magma: dark cherry base, magenta + hot amber accents.
    "fracture": {
        "dark": True,
        "bg": "#140a10", "fg": "#e2cfd6", "surface": "#200e19", "panel": "#261022",
        "violet": "#e040c0", "cyan": "#f59e0b", "magenta": "#f0abfc", "danger": "#ef4444",
        "muted": "#a88c94", "dim": "#6e505a", "faint": "#3a2830", "border": "#2e1c24",
        "user-bg": "#281422", "user-fg": "#fce0ff", "assistant-bg": "#1a0e16", "tool-bg": "#120810",
    },
    # Deep purple: extreme contrast, intense violet-on-black.
    "singularity": {
        "dark": True,
        "bg": "#060210", "fg": "#e7dffc", "surface": "#0e0624", "panel": "#140a30",
        "violet": "#b388ff", "cyan": "#d0bcff", "magenta": "#f5d0fe", "danger": "#fb7185",
        "muted": "#9080bc", "dim": "#5e4a80", "faint": "#382a52", "border": "#281c3c",
        "user-bg": "#1a0e36", "user-fg": "#efe4ff", "assistant-bg": "#0e0820", "tool-bg": "#080414",
    },
    # LIGHT — warm daylight: off-white bg, dark slate text, the rift glow by day.
    "dawn": {
        "dark": False,
        "bg": "#faf7ff", "fg": "#2a2438", "surface": "#f1ecfb", "panel": "#e9e1f7",
        "violet": "#7c3aed", "cyan": "#0891b2", "magenta": "#c026d3", "danger": "#dc2626",
        "muted": "#6b6480", "dim": "#8b84a0", "faint": "#cabfe0", "border": "#d8cef0",
        "user-bg": "#ede4ff", "user-fg": "#3b1f6b", "assistant-bg": "#f4eefc", "tool-bg": "#f0ecf6",
    },
    # LIGHT — neutral paper: calm near-white, near-black text, low-chroma accents.
    "paper": {
        "dark": False,
        "bg": "#f7f7f5", "fg": "#222220", "surface": "#efefec", "panel": "#e7e7e3",
        "violet": "#6d28d9", "cyan": "#0e7490", "magenta": "#a21caf", "danger": "#b91c1c",
        "muted": "#6a6a64", "dim": "#8c8c84", "faint": "#cfcfc8", "border": "#dcdcd5",
        "user-bg": "#eceae4", "user-fg": "#2a2a26", "assistant-bg": "#f2f1ec", "tool-bg": "#eeeee9",
    },
}

THEMES: dict[str, Theme] = {name: _theme(name, pal) for name, pal in _PALETTES.items()}

DEFAULT_THEME = "rift"


def css_variable_defaults() -> dict[str, str]:
    """Fallback CSS variables so ``$name`` always resolves, even before our
    theme is active (e.g. during the very first paint under a built-in theme)."""
    return {k: v for k, v in _PALETTES[DEFAULT_THEME].items() if isinstance(v, str)}


def palette(app) -> dict[str, str]:
    """The active theme's palette (with the default theme as a safe fallback)."""
    base = _PALETTES[DEFAULT_THEME]
    pal: dict[str, str] = {k: str(v) for k, v in base.items() if isinstance(v, str)}
    try:
        theme = app.get_theme(app.theme)
    except Exception:  # noqa: BLE001
        theme = None
    if theme is not None:
        pal.update({k: str(v) for k, v in (theme.variables or {}).items()})
        pal["fg"] = str(theme.foreground or pal["fg"])
        if theme.variables:
            pal["violet"] = str(theme.variables.get("violet", theme.primary))
        else:
            pal["violet"] = str(theme.primary)
    return pal
