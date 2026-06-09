"""Permission engine: deny rules, allow rules, session grants, persistence."""

from __future__ import annotations

from riftor.safety.permissions import Permissions


def test_default_deny_blocks_rm_rf():
    perms = Permissions()
    assert perms.is_denied("bash", "rm -rf /tmp/x")
    assert perms.is_denied("bash", "dd if=/dev/zero of=/dev/sda")
    assert not perms.is_denied("bash", "ls -la")


def test_default_deny_catches_fork_bomb_variants():
    """The fork-bomb guard must match the readable spaced form, not just the
    compact one — shells accept whitespace between every token."""
    perms = Permissions()
    variants = [
        ":(){:|:&};:",  # compact, classic
        ": () { : | : & }",  # readable / how it's usually written
        ":() { :|:& };:",  # partial spacing
        ":()  {  : | : &  }",  # extra spacing
    ]
    for v in variants:
        assert perms.is_denied("bash", v), f"fork bomb not caught: {v!r}"
    # Must not flag innocuous commands that merely contain a colon or braces.
    assert not perms.is_denied("bash", "awk '{print $1}' file")
    assert not perms.is_denied("bash", "ls -la")


def test_allow_rule_skips_prompt():
    perms = Permissions(allow=[{"tool": "bash"}])
    assert not perms.needs_prompt("bash", True, "nmap -sV host")
    assert perms.is_allowed("bash", "anything")


def test_pattern_allow():
    perms = Permissions(allow=[{"tool": "bash", "pattern": r"^nmap\b"}])
    assert perms.is_allowed("bash", "nmap -sV host")
    assert not perms.is_allowed("bash", "curl http://x")


def test_session_grant():
    perms = Permissions()
    assert perms.needs_prompt("write", True, "a.txt")
    perms.allow_for_session("write")
    assert not perms.needs_prompt("write", True, "a.txt")


def test_no_prompt_when_not_required():
    perms = Permissions()
    assert not perms.needs_prompt("read", False, "a.txt")


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "permissions.toml"
    perms = Permissions.load(path)
    perms._path = path
    perms.add_allow_rule("bash", r"^nmap\b")
    perms.add_deny_rule("bash", r"shutdown")
    reloaded = Permissions.load(path)
    assert reloaded.is_allowed("bash", "nmap -p- host")
    assert reloaded.is_denied("bash", "shutdown now")


def test_load_missing_keeps_defaults(tmp_path):
    perms = Permissions.load(tmp_path / "nope.toml")
    assert perms.is_denied("bash", "rm -rf /")  # safe defaults preserved
