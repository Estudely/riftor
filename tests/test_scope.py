"""Scope target matching — the legal/ethical guardrail. IP/CIDR/domain/wildcard."""

from __future__ import annotations

from riftor.engagement.scope import Target


def test_ip_exact_match():
    t = Target.parse("10.0.0.5")
    assert t.kind == "ip"
    assert t.matches("10.0.0.5")
    assert not t.matches("10.0.0.6")


def test_cidr_match():
    t = Target.parse("10.0.0.0/24")
    assert t.kind == "cidr"
    assert t.matches("10.0.0.7")
    assert not t.matches("10.0.1.7")


def test_domain_matches_self_and_subdomains():
    t = Target.parse("example.com")
    assert t.kind == "domain"
    assert t.matches("example.com")
    assert t.matches("sub.example.com")
    assert t.matches("a.b.example.com")
    assert not t.matches("notexample.com")
    assert not t.matches("example.com.evil.com")


def test_wildcard_matches_subdomains_only_not_base():
    """``*.example.com`` is a subdomain wildcard: it must match subdomains but
    NOT the bare base domain. Otherwise scope is broader than the operator asked
    for, on the guardrail that controls what hosts the agent may touch."""
    t = Target.parse("*.example.com")
    assert t.kind == "wildcard"
    assert t.matches("sub.example.com")
    assert t.matches("a.b.example.com")
    assert not t.matches("example.com")  # the bug: base domain must not match
    assert not t.matches("notexample.com")


from riftor.engagement.scope import Scope, extract_hosts  # noqa: E402
from riftor.engagement import Engagement  # noqa: E402


def test_extract_hosts_finds_ips_domains_urls():
    hosts = extract_hosts("scan 10.0.0.5 and https://app.example.com/login plus sub.example.com")
    assert "10.0.0.5" in hosts
    assert "app.example.com" in hosts
    assert "sub.example.com" in hosts


def test_is_in_scope_empty_allows_all():
    s = Scope()
    assert s.is_in_scope("anything.com")  # no in-scope set => permissive


def test_is_in_scope_requires_membership_when_set():
    s = Scope()
    s.add("example.com", "in")
    assert s.is_in_scope("example.com")
    assert s.is_in_scope("sub.example.com")
    assert not s.is_in_scope("evil.com")


def test_out_of_scope_overrides_in_scope():
    """Out-of-scope wins: an excluded host is blocked even if a broader in-scope
    rule would otherwise allow it."""
    s = Scope()
    s.add("example.com", "in")
    s.add("secret.example.com", "out")
    assert s.is_in_scope("public.example.com")
    assert not s.is_in_scope("secret.example.com")


def test_scope_violations_lists_out_of_scope_hosts():
    s = Scope()
    s.add("example.com", "in")
    v = s.violations("curl https://example.com and nmap 10.9.9.9 evil.org")
    assert "10.9.9.9" in v
    assert "evil.org" in v
    assert "example.com" not in v


def test_engagement_violations_respects_enforce_toggle(tmp_workdir):
    eng = Engagement(tmp_workdir)
    eng.add_scope("example.com", "in")
    assert eng.violations("touch evil.org") == ["evil.org"]
    eng.set_enforce(False)
    assert eng.violations("touch evil.org") == []  # enforcement off => no violations
