"""Wordlist discovery: known roots + optional config dir, capped recursive walk."""

from __future__ import annotations

from pathlib import Path

from riftor.config import Config
from riftor.engagement.wordlists import Wordlist, count_lines, discover, search
from riftor.tools import ToolContext, get
from riftor.tools.engagement import WordlistTool


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


def _lists():
    return [
        Wordlist("common.txt", Path("/r/Discovery/Web-Content/common.txt"), "Discovery/Web-Content"),
        Wordlist("subdomains-top1million.txt", Path("/r/DNS/subdomains-top1million.txt"), "DNS"),
        Wordlist("top-usernames.txt", Path("/r/Usernames/top-usernames.txt"), "Usernames"),
    ]


def test_search_empty_query_returns_all():
    assert search("", _lists()) == _lists()


def test_search_substring_on_category_and_name():
    hits = search("web-content", _lists())
    assert [w.name for w in hits] == ["common.txt"]
    hits = search("subdomains", _lists())
    assert [w.name for w in hits] == ["subdomains-top1million.txt"]


def test_search_fuzzy_fallback_when_no_substring():
    # "usernme" has no substring hit but fuzzy-matches top-usernames.txt
    hits = search("usernmes", _lists())
    assert any(w.name == "top-usernames.txt" for w in hits)


def test_search_no_match_returns_empty():
    assert search("zzzznotathing", _lists()) == []


def _ctx_with(root):
    return ToolContext(config=Config(wordlists_dir=str(root)))


async def test_tool_registered():
    assert get("wordlist") is not None


async def test_tool_lists_catalog_when_no_query(tmp_path):
    root = tmp_path / "seclists"
    (root / "Discovery").mkdir(parents=True)
    (root / "Discovery" / "common.txt").write_text("a\nb\n")
    tool = WordlistTool()
    res = await tool.execute({}, _ctx_with(root))
    assert not res.is_error
    assert "common.txt" in res.content
    assert "Discovery" in res.content
    assert str((root / "Discovery" / "common.txt").resolve()) in res.content


async def test_tool_search_returns_matches(tmp_path):
    root = tmp_path / "seclists"
    (root / "DNS").mkdir(parents=True)
    (root / "DNS" / "subdomains.txt").write_text("a\n")
    (root / "DNS" / "resolvers.txt").write_text("1.1.1.1\n")
    tool = WordlistTool()
    res = await tool.execute({"query": "subdomains"}, _ctx_with(root))
    assert "subdomains.txt" in res.content
    assert "resolvers.txt" not in res.content


async def test_tool_empty_install_is_friendly(tmp_path):
    tool = WordlistTool()
    ctx = ToolContext(config=Config(wordlists_dir=str(tmp_path / "nope")))
    # patch KNOWN_ROOTS empty so the host's real /usr/share doesn't leak in
    import riftor.engagement.wordlists as wl
    orig = wl.KNOWN_ROOTS
    wl.KNOWN_ROOTS = []
    try:
        res = await tool.execute({}, ctx)
    finally:
        wl.KNOWN_ROOTS = orig
    assert not res.is_error
    assert "no wordlists" in res.content.lower()
    assert "wordlists_dir" in res.content


async def test_tool_handles_none_config(tmp_path):
    tool = WordlistTool()
    import riftor.engagement.wordlists as wl
    orig = wl.KNOWN_ROOTS
    wl.KNOWN_ROOTS = []
    try:
        res = await tool.execute({}, ToolContext())  # config is None
    finally:
        wl.KNOWN_ROOTS = orig
    assert not res.is_error  # degrades to "no wordlists" message, no crash
