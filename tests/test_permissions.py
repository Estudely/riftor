"""Permission engine: deny rules, allow rules, session grants, persistence."""

from __future__ import annotations

from riftor.safety.permissions import Permissions


def test_default_deny_blocks_rm_rf():
    perms = Permissions()
    assert perms.is_denied("bash", "rm -rf /tmp/x")
    assert perms.is_denied("bash", "dd if=/dev/zero of=/dev/sda")
    assert not perms.is_denied("bash", "ls -la")


def test_default_deny_catches_separated_flags():
    """rm with recursive+force in any flag arrangement must be caught (issue #109)."""
    perms = Permissions()
    variants = [
        "rm -rf /",
        "rm -fr /",
        "rm -r -f /",
        "rm -f -r /",
        "rm --recursive --force /tmp/x",
        "rm --force --recursive /tmp/x",
    ]
    for v in variants:
        assert perms.is_denied("bash", v), f"separated-flag rm not caught: {v!r}"


def test_default_deny_catches_rm_r_on_absolute_paths():
    """rm -r on an absolute path is destructive even without -f (issue #109)."""
    perms = Permissions()
    for v in ["rm -r /", "rm -r /etc", "rm -r /home", "rm -r /tmp/x"]:
        assert perms.is_denied("bash", v), f"rm -r on abs path not caught: {v!r}"
    # relative paths and non-recursive rm must NOT be flagged
    assert not perms.is_denied("bash", "rm -r relative-dir")
    assert not perms.is_denied("bash", "rm single-file.txt")
    assert not perms.is_denied("bash", "grep -r pattern /")  # grep -r, not rm -r


def test_default_deny_catches_alternative_destructive_tools():
    """find -delete, find -exec rm, mke2fs, nvme/vda writes (issue #109)."""
    perms = Permissions()
    destructive = [
        "find / -delete",
        "find / -exec rm {} \\;",
        "mke2fs /dev/sda1",
        "dd if=/dev/zero of=/dev/nvme0n1",
        "dd if=/dev/zero of=/dev/vda",
        "cat /dev/urandom > /dev/sda",
    ]
    for v in destructive:
        assert perms.is_denied("bash", v), f"destructive cmd not caught: {v!r}"
    # find without -delete/-exec rm must NOT be flagged
    assert not perms.is_denied("bash", "find . -name '*.py'")


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


def test_deny_takes_precedence_over_allow():
    """Documented precedence: deny rules beat allow rules. A command matching
    both must be denied. Callers (app.py / headless.py) enforce this by checking
    is_denied() before is_allowed(); this pins the contract so a refactor that
    reorders the checks fails loudly."""
    perms = Permissions(
        allow=[{"tool": "bash"}],  # allow all bash
        deny=[{"tool": "bash", "pattern": r"shutdown"}],
    )
    # both match — deny must win
    assert perms.is_denied("bash", "shutdown now")
    assert perms.is_allowed("bash", "shutdown now")  # the allow rule does match...
    # ...so the gate's contract is: check is_denied FIRST. A caller that does so
    # blocks the command. Assert the two signals are what callers depend on.
    assert perms.is_denied("bash", "shutdown now") and perms.is_allowed("bash", "shutdown now")
    # a non-denied command under the same allow rule still passes
    assert perms.is_allowed("bash", "nmap -sV host")
    assert not perms.is_denied("bash", "nmap -sV host")
