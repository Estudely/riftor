"""Wordlist discovery: known roots + optional config dir, capped recursive walk."""

from __future__ import annotations

from pathlib import Path

from riftor.engagement.wordlists import Wordlist, count_lines, discover


def _mkroot(base: Path) -> Path:
    root = base / "seclists"
    (root / "Discovery" / "Web-Content").mkdir(parents=True)
    (root / "Usernames").mkdir(parents=True)
    (root / "Discovery" / "Web-Content" / "common.txt").write_text("a\nb\nc\n")
    (root / "Usernames" / "top-usernames.txt").write_text("admin\nroot\n")
    (root / "raw.lst").write_text("x\ny\n")            # directly under root
    (root / "notes.md").write_text("ignored")          # wrong extension
    return root


def test_discover_finds_and_categorizes(tmp_path):
    root = _mkroot(tmp_path)
    lists = discover(extra_dir=str(root), known_roots=[])
    by_name = {w.name: w for w in lists}
    assert set(by_name) == {"common.txt", "top-usernames.txt", "raw.lst"}
    assert by_name["common.txt"].category == "Discovery/Web-Content"
    assert by_name["top-usernames.txt"].category == "Usernames"
    assert by_name["raw.lst"].category == "(root)"
    assert by_name["common.txt"].path.is_absolute()


def test_discover_missing_root_is_silent(tmp_path):
    lists = discover(extra_dir=str(tmp_path / "nope"), known_roots=["/does/not/exist"])
    assert lists == []


def test_discover_dedups_same_file_reachable_from_two_roots(tmp_path):
    root = _mkroot(tmp_path)
    lists = discover(extra_dir=str(root), known_roots=[str(root)])
    paths = [w.path for w in lists]
    assert len(paths) == len(set(paths))  # no dupes


def test_discover_respects_total_cap(tmp_path, monkeypatch):
    root = tmp_path / "big"
    root.mkdir()
    for i in range(10):
        (root / f"w{i}.txt").write_text("x\n")
    monkeypatch.setattr("riftor.engagement.wordlists.MAX_WORDLISTS", 4)
    lists = discover(extra_dir=str(root), known_roots=[])
    assert len(lists) == 4


def test_discover_skips_symlinked_dirs(tmp_path):
    root = _mkroot(tmp_path)
    loop = root / "Discovery" / "loop"
    loop.symlink_to(root, target_is_directory=True)
    lists = discover(extra_dir=str(root), known_roots=[])
    assert all("loop" not in str(w.path) for w in lists)


def test_count_lines_and_cache(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("a\nb\nc\n")
    assert count_lines(f) == 3
    assert count_lines(f) == 3  # cached path; same result


def test_count_lines_unreadable_returns_none(tmp_path):
    missing = tmp_path / "gone.txt"
    assert count_lines(missing) is None
