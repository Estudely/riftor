"""Wordlist discovery: probe known SecLists/system roots + an optional config dir.

Pure and side-effect-free (filesystem reads only). Never raises — a missing or
unreadable root is skipped silently, mirroring engagement/doctor.py's probing.
The agent calls the ``wordlist`` tool, which resolves absolute paths to feed into
ffuf/gobuster via ``bash``. v1 discovers existing lists only (no generation).
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from pathlib import Path

# Probed in order. A file reachable from several roots is deduped by resolved path.
KNOWN_ROOTS: list[str] = [
    "/usr/share/seclists",
    "/usr/share/wordlists/seclists",
    "/usr/share/wordlists",
    "~/.local/share/seclists",
]

_EXTENSIONS = (".txt", ".lst")
MAX_WORDLISTS = 500   # cap total results so we never flood the model's context
MAX_DEPTH = 6         # cap recursion depth under each root

# Cache line counts by (resolved path, mtime, size) so re-counting is free and a
# changed file is recounted.
_LINE_CACHE: dict[tuple[str, int, int], int] = {}


@dataclass(frozen=True)
class Wordlist:
    name: str        # file name, e.g. "common.txt"
    path: Path       # resolved absolute path
    category: str    # path under its root, e.g. "Discovery/Web-Content"; "(root)" if direct


def _roots(extra_dir: str | None, known_roots: list[str] | None) -> list[Path]:
    raw = list(KNOWN_ROOTS if known_roots is None else known_roots)
    if extra_dir:
        raw.append(extra_dir)
    seen: set[Path] = set()
    out: list[Path] = []
    for r in raw:
        try:
            p = Path(r).expanduser().resolve()
        except OSError:
            continue
        if p.is_dir() and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _category(root: Path, file_path: Path) -> str:
    rel = file_path.parent.relative_to(root)
    return "(root)" if str(rel) == "." else str(rel)


def discover(
    extra_dir: str | None = None,
    known_roots: list[str] | None = None,
) -> list[Wordlist]:
    """Walk known roots (+ optional extra dir) for *.txt/*.lst, capped & deduped."""
    found: dict[Path, Wordlist] = {}
    for root in _roots(extra_dir, known_roots):
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= MAX_DEPTH:
                dirnames[:] = []
            # never descend into symlinked dirs (loops / escaping the root)
            dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
            for fn in filenames:
                if not fn.endswith(_EXTENSIONS):
                    continue
                try:
                    fp = (Path(dirpath) / fn).resolve()
                except OSError:
                    continue
                if fp in found:
                    continue
                found[fp] = Wordlist(name=fn, path=fp, category=_category(root, fp))
                if len(found) >= MAX_WORDLISTS:
                    return list(found.values())
    return list(found.values())


def count_lines(path: Path) -> int | None:
    """Line count for ``path``, cached by (path, mtime, size). None if unreadable."""
    try:
        st = path.stat()
        key = (str(path), int(st.st_mtime), int(st.st_size))
    except OSError:
        return None
    if key in _LINE_CACHE:
        return _LINE_CACHE[key]
    try:
        with path.open("rb") as fh:
            n = sum(buf.count(b"\n") for buf in iter(lambda: fh.read(1 << 20), b""))
    except OSError:
        return None
    _LINE_CACHE[key] = n
    return n


def search(query: str, lists: list[Wordlist], limit: int = 25) -> list[Wordlist]:
    """Substring match on 'category/name' and path; difflib fuzzy fallback on names."""
    q = query.strip().lower()
    if not q:
        return lists[:limit]
    hits = [
        w for w in lists
        if q in f"{w.category}/{w.name}".lower() or q in str(w.path).lower()
    ]
    if hits:
        return hits[:limit]
    names = [w.name for w in lists]
    close = difflib.get_close_matches(q, [n.lower() for n in names], n=limit, cutoff=0.5)
    chosen = {c for c in close}
    return [w for w in lists if w.name.lower() in chosen][:limit]
